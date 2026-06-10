import os
from django.core.files.storage import default_storage
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, generics
from .models import Job, Transaction, JobSummary
from .serializers import JobSerializer, TransactionSerializer, JobSummarySerializer
from .task import process_transaction_csv

class JobUploadView(APIView):
    def post(self, request, *args, **kwargs):
        file = request.FILES.get('file')
        if not file:
            return Response({"error": "No CSV file provided."}, status=status.HTTP_400_BAD_REQUEST)
        if not file.name.endswith('.csv'):
            return Response({"error": "File must be a CSV."}, status=status.HTTP_400_BAD_REQUEST)

        # Create the Job record
        job = Job.objects.create(filename=file.name, status='pending')

        # Save the file temporarily for the Celery worker to read
        file_path = default_storage.save(f"tmp/{job.id}_{file.name}", file)
        full_path = default_storage.path(file_path)

        # Enqueue the background task
        process_transaction_csv.delay(job.id, full_path)

        return Response({"job_id": job.id}, status=status.HTTP_202_ACCEPTED)

class JobListView(generics.ListAPIView):
    serializer_class = JobSerializer

    def get_queryset(self):
        queryset = Job.objects.all().order_by('-created_at')
        status_param = self.request.query_params.get('status')
        if status_param:
            queryset = queryset.filter(status=status_param)
        return queryset

class JobStatusView(APIView):
    def get(self, request, job_id, *args, **kwargs):
        try:
            job = Job.objects.get(id=job_id)
        except Job.DoesNotExist:
            return Response({"error": "Job not found."}, status=status.HTTP_404_NOT_FOUND)
        
        data = {"job_id": job.id, "status": job.status}
        
        # Include summary stats if completed
        if job.status == 'completed' and hasattr(job, 'summary'):
            data['summary'] = JobSummarySerializer(job.summary).data

        return Response(data, status=status.HTTP_200_OK)

class JobResultsView(APIView):
    def get(self, request, job_id, *args, **kwargs):
        try:
            job = Job.objects.get(id=job_id)
        except Job.DoesNotExist:
            return Response({"error": "Job not found."}, status=status.HTTP_404_NOT_FOUND)

        if job.status != 'completed':
            return Response({"error": f"Job results are not available yet. Current status: {job.status}"}, status=status.HTTP_400_BAD_REQUEST)

        transactions = Transaction.objects.filter(job=job)
        anomalies = transactions.filter(is_anomaly=True)
        summary = getattr(job, 'summary', None)

        return Response({
            "cleaned_transactions": TransactionSerializer(transactions, many=True).data,
            "anomalies": TransactionSerializer(anomalies, many=True).data,
            "summary": JobSummarySerializer(summary).data if summary else None
        }, status=status.HTTP_200_OK)