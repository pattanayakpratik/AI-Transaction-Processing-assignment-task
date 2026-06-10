from django.urls import path
from .views import JobUploadView, JobListView, JobStatusView, JobResultsView

urlpatterns = [
    path('jobs/upload', JobUploadView.as_view(), name='job-upload'),
    path('jobs', JobListView.as_view(), name='job-list'),
    path('jobs/<int:job_id>/status', JobStatusView.as_view(), name='job-status'),
    path('jobs/<int:job_id>/results', JobResultsView.as_view(), name='job-results'),
]