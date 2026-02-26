from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import JobViewSet, ResultViewSet, DetectedSiteViewSet, ConcessionGeoJSONView, AlertViewSet, RegionGeoJSONView, my_assignments_notifications

router = DefaultRouter()
router.register(r'jobs', JobViewSet, basename='job')
router.register(r'results', ResultViewSet, basename='result')
router.register(r'sites', DetectedSiteViewSet, basename='site')
router.register(r'concessions', ConcessionGeoJSONView, basename='concession')

app_name = 'api'

urlpatterns = [
    path('', include(router.urls)),
    path('regions/', RegionGeoJSONView.as_view({'get': 'list'}), name='regions'),
    path('alerts/', AlertViewSet.as_view({'get': 'list'}), name='alert-list'),
    path('alerts/bulk_action/', AlertViewSet.as_view({'post': 'bulk_action'}), name='alert-bulk-action'),
    path('alerts/<pk>/', AlertViewSet.as_view({'get': 'retrieve'}), name='alert-detail'),
    path('alerts/<pk>/acknowledge/', AlertViewSet.as_view({'post': 'acknowledge'}), name='alert-acknowledge'),
    path('alerts/<pk>/dismiss/', AlertViewSet.as_view({'post': 'dismiss'}), name='alert-dismiss'),
    path('alerts/<pk>/dispatch/', AlertViewSet.as_view({'post': 'dispatch_alert'}), name='alert-dispatch'),
    path('alerts/<pk>/resolve/', AlertViewSet.as_view({'post': 'resolve'}), name='alert-resolve'),
    path('my-assignments/', my_assignments_notifications, name='my-assignments'),
]