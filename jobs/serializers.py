from rest_framework import serializers
from .models import Job, Transaction, JobSummary

class JobSerializer(serializers.ModelSerializer):
    class Meta:
        model = Job
        fields = ['id', 'filename', 'status', 'row_count_raw', 'row_count_clean', 'created_at', 'completed_at']

class TransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transaction
        exclude = ['job']

class JobSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = JobSummary
        exclude = ['id', 'job']