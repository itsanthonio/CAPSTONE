from django.urls import path
from . import views

app_name = 'scanning'

urlpatterns = [
    path('',          views.auto_scan,         name='auto_scan'),
    path('control/',  views.auto_scan_control,  name='auto_scan_control'),
    path('api/status/',       views.ScanningStatusAPI.as_view(),      name='api_status'),
    path('api/toggle/',       views.ScanningToggleAPI.as_view(),      name='api_toggle'),
    path('api/config/',       views.ScanningConfigAPI.as_view(),      name='api_config'),
    path('api/org-toggle/<uuid:org_id>/', views.OrgScanToggleAPI.as_view(),  name='api_org_toggle'),
    path('api/org-config/<uuid:org_id>/', views.OrgScanConfigAPI.as_view(),  name='api_org_config'),
    path('api/recent-tiles/', views.ScanningRecentTilesAPI.as_view(), name='api_recent_tiles'),
    path('api/detections/',   views.ScanningDetectionsAPI.as_view(),  name='api_detections'),
    path('api/tile-detail/',  views.ScanningTileDetailAPI.as_view(),  name='api_tile_detail'),
    path('api/force-scan/',   views.ScanningForceScanAPI.as_view(),   name='api_force_scan'),
    path('api/export/',       views.ScanningExportAPI.as_view(),      name='api_export'),
]
