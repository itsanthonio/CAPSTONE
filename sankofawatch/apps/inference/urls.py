from django.urls import path
from .api import run_inference, model_info

urlpatterns = [
    path('api/v1/inference/', run_inference, name='run_inference'),
    path('api/v1/model-info/', model_info, name='model_info'),
]
