"""
URL configuration for API app.

Provides endpoints for job creation, status tracking, and result retrieval.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import JobViewSet, ResultViewSet

# Create router for ViewSets
router = DefaultRouter()
router.register(r'jobs', JobViewSet, basename='job')
router.register(r'results', ResultViewSet, basename='result')

app_name = 'api'

urlpatterns = [
    path('', include(router.urls)),
]
