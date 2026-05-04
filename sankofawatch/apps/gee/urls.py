from django.urls import path
from .api import gee_service_info, validate_aoi, trigger_export, export_status

urlpatterns = [
    path('api/v1/service-info/', gee_service_info, name='gee_service_info'),
    path('api/v1/validate-aoi/', validate_aoi, name='validate_aoi'),
    path('api/v1/trigger-export/<uuid:job_id>/', trigger_export, name='trigger_export'),
    path('api/v1/export-status/<str:export_id>/', export_status, name='export_status'),
]
