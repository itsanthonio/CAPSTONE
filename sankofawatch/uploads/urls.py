from django.urls import path
from . import views

app_name = 'uploads'

urlpatterns = [
    path('data-uploads/', views.DataUploadsView.as_view(), name='data_uploads'),
    path('upload/concessions/', views.UploadConcessionsView.as_view(), name='upload_concessions'),
    path('upload/water-bodies/', views.UploadWaterBodiesView.as_view(), name='upload_water_bodies'),
    path('upload/protected-forests/', views.UploadProtectedForestView.as_view(), name='upload_protected_forests'),
    path('upload/districts/', views.UploadDistrictsView.as_view(), name='upload_districts'),
]
