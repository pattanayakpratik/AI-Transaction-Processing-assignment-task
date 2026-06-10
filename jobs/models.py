from django.db import models

class Job(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    filename = models.CharField(max_length=255) # [cite: 60]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending') # [cite: 60]
    row_count_raw = models.IntegerField(null=True, blank=True) # [cite: 60]
    row_count_clean = models.IntegerField(null=True, blank=True) # [cite: 60]
    created_at = models.DateTimeField(auto_now_add=True) # [cite: 60]
    completed_at = models.DateTimeField(null=True, blank=True) # [cite: 60]
    error_message = models.TextField(null=True, blank=True) # [cite: 60]

    def __str__(self):
        return f"Job {self.id} - {self.filename} ({self.status})"

class Transaction(models.Model):
    job = models.ForeignKey(Job, related_name='transactions', on_delete=models.CASCADE) # [cite: 62]
    txn_id = models.CharField(max_length=100, null=True, blank=True) # [cite: 62]
    date = models.DateField(null=True, blank=True)  # Will store the ISO 8601 cleaned date [cite: 43, 62]
    merchant = models.CharField(max_length=255, null=True, blank=True) # [cite: 62]
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True) # [cite: 62]
    currency = models.CharField(max_length=10, null=True, blank=True) # [cite: 62]
    status = models.CharField(max_length=50, null=True, blank=True) # [cite: 64]
    category = models.CharField(max_length=100, null=True, blank=True) # [cite: 64]
    account_id = models.CharField(max_length=100, null=True, blank=True) # [cite: 64]
    
    # Anomaly Tracking
    is_anomaly = models.BooleanField(default=False) # [cite: 64]
    anomaly_reason = models.TextField(null=True, blank=True) # [cite: 64]
    
    # LLM Processing fields
    llm_category = models.CharField(max_length=100, null=True, blank=True) # [cite: 64]
    llm_raw_response = models.TextField(null=True, blank=True) # [cite: 64]
    llm_failed = models.BooleanField(default=False) # [cite: 64]

    def __str__(self):
        return f"{self.txn_id} - {self.merchant} ({self.amount} {self.currency})"

class JobSummary(models.Model):
    RISK_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
    ]

    job = models.OneToOneField(Job, related_name='summary', on_delete=models.CASCADE) # [cite: 66]
    total_spend_inr = models.DecimalField(max_digits=15, decimal_places=2, default=0.00) # [cite: 66]
    total_spend_usd = models.DecimalField(max_digits=15, decimal_places=2, default=0.00) # [cite: 66]
    top_merchants = models.JSONField(default=list)  # Stores the top 3 merchants 
    anomaly_count = models.IntegerField(default=0) # 
    narrative = models.TextField(null=True, blank=True) # 
    risk_level = models.CharField(max_length=10, choices=RISK_CHOICES, null=True, blank=True) # 

    def __str__(self):
        return f"Summary for Job {self.job.id}"