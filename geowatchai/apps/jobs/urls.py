from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .api import JobViewSet

router = DefaultRouter()
router.register(r'jobs', JobViewSet, basename='job')

urlpatterns = [
    path('api/v1/', include(router.urls)),
]
