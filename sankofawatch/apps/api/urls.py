from django.urls import path, include
from rest_framework.routers import DefaultRouter


from .views import JobViewSet, ResultViewSet, DetectedSiteViewSet, ConcessionGeoJSONView, AlertViewSet, RegionGeoJSONView, my_assignments_notifications, geocode_proxy, session_ping, parse_aoi_file

router = DefaultRouter()
router.register(r'jobs', JobViewSet, basename='job')
router.register(r'results', ResultViewSet, basename='result')
router.register(r'sites', DetectedSiteViewSet, basename='site')
router.register(r'concessions', ConcessionGeoJSONView, basename='concession')

app_name = 'api'

urlpatterns = [
    path('', include(router.urls)),
    path('regions/', RegionGeoJSONView.as_view({'get': 'list'}), name='regions'),
    path('alerts/', AlertViewSet.as_view({'get': 'list', 'post': 'create'}), name='alert-list'),
    path('alerts/summary/', AlertViewSet.as_view({'get': 'summary'}), name='alert-summary'),
    path('alerts/bulk_action/', AlertViewSet.as_view({'post': 'bulk_action'}), name='alert-bulk-action'),
    path('alerts/bulk-assign-inspector/', AlertViewSet.as_view({'post': 'bulk_assign_inspector'}), name='alert-bulk-assign-inspector'),
    path('alerts/<pk>/', AlertViewSet.as_view({'get': 'retrieve'}), name='alert-detail'),
    path('alerts/<pk>/acknowledge/', AlertViewSet.as_view({'post': 'acknowledge'}), name='alert-acknowledge'),
    path('alerts/<pk>/dismiss/', AlertViewSet.as_view({'post': 'dismiss'}), name='alert-dismiss'),
    path('alerts/<pk>/dispatch/', AlertViewSet.as_view({'post': 'dispatch_alert'}), name='alert-dispatch'),
    path('alerts/<pk>/resolve/', AlertViewSet.as_view({'post': 'resolve'}), name='alert-resolve'),
    path('alerts/<pk>/assign_inspector/', AlertViewSet.as_view({'post': 'assign_inspector'}), name='alert-assign-inspector'),
    path('alerts/<pk>/update/', AlertViewSet.as_view({'put': 'update', 'patch': 'update'}), name='alert-update'),
    path('alerts/<pk>/delete/', AlertViewSet.as_view({'delete': 'delete'}), name='alert-delete'),
    path('my-assignments/', my_assignments_notifications, name='my-assignments'),
    path('geocode/', geocode_proxy, name='geocode'),
    path('notifications/', include('apps.notifications.urls')),
    path('session/ping/', session_ping, name='session-ping'),
    path('parse-aoi/', parse_aoi_file, name='parse-aoi'),
]