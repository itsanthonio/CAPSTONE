from django.urls import path
from . import views

app_name = 'uploads'

urlpatterns = [
    path('data-uploads/', views.DataUploadsView.as_view(), name='data_uploads'),
]
