import csv
import re
from datetime import datetime
from statistics import median
from celery import shared_task
from django.db import transaction
from .models import Job, Transaction, JobSummary
import os
import json
import google.generativeai as genai
from django.conf import settings

@shared_task(bind=True, max_retries=3)
def process_transaction_csv(self, job_id, file_path):
    job = Job.objects.get(id=job_id)
    job.status = 'processing'
    job.save()

    try:
        raw_rows = []
        with open(file_path, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                raw_rows.append(row)
        
        job.row_count_raw = len(raw_rows)

        # --- a) Data Cleaning ---
        cleaned_data = []
        seen_rows = set()

        for row in raw_rows:
            # Remove exact duplicate rows
            row_tuple = tuple(row.items())
            if row_tuple in seen_rows:
                continue
            seen_rows.add(row_tuple)

            # Strip currency symbols from amounts
            raw_amount = str(row.get('amount', '0'))
            clean_amount = float(re.sub(r'[^\d.]', '', raw_amount)) if raw_amount else 0.0

            # Normalise date formats to ISO 8601
            raw_date = row.get('date', '')
            clean_date = None
            try:
                if '/' in raw_date:
                    clean_date = datetime.strptime(raw_date, '%Y/%m/%d').date()
                elif '-' in raw_date:
                    clean_date = datetime.strptime(raw_date, '%d-%m-%Y').date()
            except ValueError:
                pass # Handle invalid dates as needed

            # Uppercase status, fill missing category
            status = str(row.get('status', '')).upper()
            category = row.get('category', '').strip() or 'Uncategorised'

            cleaned_data.append({
                'txn_id': row.get('txn_id'),
                'date': clean_date,
                'merchant': row.get('merchant'),
                'amount': clean_amount,
                'currency': str(row.get('currency', '')).upper(),
                'status': status,
                'category': category,
                'account_id': row.get('account_id'),
                'notes': row.get('notes')
            })

        job.row_count_clean = len(cleaned_data)

        # --- b) Anomaly Detection ---
        # Calculate medians per account
        account_amounts = {}
        for row in cleaned_data:
            acc = row['account_id']
            if acc not in account_amounts:
                account_amounts[acc] = []
            account_amounts[acc].append(row['amount'])
        
        account_medians = {acc: median(amounts) for acc, amounts in account_amounts.items()}

        domestic_brands = ['SWIGGY', 'OLA', 'IRCTC']
        
        transactions_to_create = []
        uncategorised_txns = []

        for row in cleaned_data:
            is_anomaly = False
            reasons = []

            # Anomaly: Amount exceeds 3x median
            acc_median = account_medians.get(row['account_id'], 0)
            if row['amount'] > (3 * acc_median) and acc_median > 0:
                is_anomaly = True
                reasons.append("Amount > 3x account median")

            # Anomaly: USD for domestic-only brand
            if row['currency'] == 'USD' and str(row['merchant']).upper() in domestic_brands:
                is_anomaly = True
                reasons.append("USD used for domestic brand")

            txn = Transaction(
                job=job,
                txn_id=row['txn_id'],
                date=row['date'],
                merchant=row['merchant'],
                amount=row['amount'],
                currency=row['currency'],
                status=row['status'],
                category=row['category'],
                account_id=row['account_id'],
                is_anomaly=is_anomaly,
                anomaly_reason=" | ".join(reasons) if reasons else None
            )
            transactions_to_create.append(txn)

            if txn.category == 'Uncategorised':
                uncategorised_txns.append(txn)

        # Save all cleaned transactions to DB
        with transaction.atomic():
            Transaction.objects.bulk_create(transactions_to_create)


        # --- c) LLM Classification (Batching) ---
        if uncategorised_txns:
            genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
            model = genai.GenerativeModel('gemini-1.5-flash')

            batch_data = [{"txn_id": t.txn_id, "merchant": t.merchant, "amount": float(t.amount)} for t in uncategorised_txns]
            
            prompt = f"""
            You are a financial categorization AI. Categorize the following transactions into one of these exact categories: 
            Food, Shopping, Travel, Transport, Utilities, Cash Withdrawal, Entertainment, or Other.
            Respond ONLY with a valid JSON array of objects in this exact format, with no markdown formatting or extra text:
            [ {{"txn_id": "...", "category": "..."}} ]
            
            Transactions:
            {json.dumps(batch_data)}
            """

            import time  # Ensure time is imported at the top of the file
            llm_success = False
            response_text = ""

            # Explicit 3-attempt exponential backoff loop for the LLM call specifically
            for attempt in range(1, 4):
                try:
                    response = model.generate_content(prompt)
                    response_text = response.text
                    llm_results = json.loads(response_text.strip('` \njson'))
                    
                    category_map = {res['txn_id']: res['category'] for res in llm_results if 'txn_id' in res and 'category' in res}
                    
                    transactions_to_update = []
                    for txn in uncategorised_txns:
                        if txn.txn_id in category_map:
                            txn.category = category_map[txn.txn_id]
                            txn.llm_category = category_map[txn.txn_id]
                        else:
                            txn.llm_failed = True
                        txn.llm_raw_response = response_text
                        transactions_to_update.append(txn)
                    
                    Transaction.objects.bulk_update(transactions_to_update, ['category', 'llm_category', 'llm_failed', 'llm_raw_response'])
                    llm_success = True
                    break  # Success! Break out of the retry loop
                except Exception as e:
                    response_text = str(e)
                    if attempt < 3:
                        time.sleep(2 ** attempt)  # 2s, then 4s backoff before trying again
            
            # If all 3 attempts fail, mark the batch as failed and continue smoothly
            if not llm_success:
                for txn in uncategorised_txns:
                    txn.llm_failed = True
                    txn.llm_raw_response = f"All 3 retry attempts failed. Last error: {response_text}"
                Transaction.objects.bulk_update(uncategorised_txns, ['llm_failed', 'llm_raw_response'])


        # --- d) LLM Narrative Summary ---
        # Calculate totals for the summary
        total_inr = sum([float(t.amount) for t in transactions_to_create if t.currency == 'INR'])
        total_usd = sum([float(t.amount) for t in transactions_to_create if t.currency == 'USD'])
        anomaly_count = sum([1 for t in transactions_to_create if t.is_anomaly])
        
        # Calculate Top 3 Merchants
        merchant_totals = {}
        for t in transactions_to_create:
            merchant_totals[t.merchant] = merchant_totals.get(t.merchant, 0) + float(t.amount)
        top_merchants = sorted(merchant_totals.items(), key=lambda x: x[1], reverse=True)[:3]
        top_merchants_list = [{"merchant": m[0], "total": m[1]} for m in top_merchants]

        summary_prompt = f"""
        Analyze this financial job summary and return ONLY a JSON object.
        Total INR: {total_inr}, Total USD: {total_usd}, Anomaly Count: {anomaly_count}, Top Merchants: {top_merchants_list}
        
        Provide a 2-3 sentence narrative describing the spending habits, and assign a risk_level of "low", "medium", or "high" based on the anomaly count.
        Format EXACTLY as:
        {{
            "narrative": "...",
            "risk_level": "low/medium/high"
        }}
        """
        
        narrative = "Summary generation failed."
        risk_level = "medium"
        try:
            model = genai.GenerativeModel('gemini-1.5-flash')
            summary_response = model.generate_content(summary_prompt)
            summary_data = json.loads(summary_response.text.strip('` \njson'))
            narrative = summary_data.get('narrative', narrative)
            risk_level = summary_data.get('risk_level', risk_level).lower()
        except Exception:
            pass # Fallback to defaults if summary LLM call fails

        JobSummary.objects.create(
            job=job,
            total_spend_inr=total_inr,
            total_spend_usd=total_usd,
            top_merchants=top_merchants_list,
            anomaly_count=anomaly_count,
            narrative=narrative,
            risk_level=risk_level
        )

        job.status = 'completed'
        job.completed_at = datetime.now()
        job.save()

    except Exception as e:
        job.status = 'failed'
        job.error_message = str(e)
        job.save()
        raise self.retry(exc=e, countdown=2 ** self.request.retries)