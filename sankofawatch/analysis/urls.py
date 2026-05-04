from django.urls import path
from . import views

app_name = 'analysis'

urlpatterns = [
    path('live-map/', views.LiveMapView.as_view(), name='live_map'),
    path('run-hls-inference/', views.run_hls_inference, name='run_hls_inference'),
]
