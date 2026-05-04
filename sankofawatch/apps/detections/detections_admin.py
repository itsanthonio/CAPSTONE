from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin
from .models import (
    Region,
    LegalConcession,
    SatelliteImagery,
    ModelRun,
    DetectedSite,
    Alert,
    Inspection,
    SiteTimelapse,
)


@admin.register(Region)
class RegionAdmin(GISModelAdmin):
    list_display = ('name', 'region_type', 'district', 'is_active', 'created_at')
    list_filter = ('region_type', 'is_active')
    search_fields = ('name', 'district')


@admin.register(LegalConcession)
class LegalConcessionAdmin(GISModelAdmin):
    list_display = ('license_number', 'concession_name', 'holder_name', 'license_type', 'is_active', 'district')
    list_filter = ('license_type', 'is_active', 'data_source')
    search_fields = ('license_number', 'concession_name', 'holder_name')


@admin.register(SatelliteImagery)
class SatelliteImageryAdmin(GISModelAdmin):
    list_display = ('scene_id', 'satellite', 'acquisition_date', 'cloud_cover_pct', 'processed_at')
    list_filter = ('satellite',)
    search_fields = ('scene_id',)


@admin.register(ModelRun)
class ModelRunAdmin(admin.ModelAdmin):
    list_display = ('model_name', 'model_version', 'val_precision', 'val_recall', 'val_iou', 'run_at')
    list_filter = ('model_version', 'architecture')
    search_fields = ('model_name', 'model_version')


@admin.register(DetectedSite)
class DetectedSiteAdmin(GISModelAdmin):
    list_display = ('id', 'detection_date', 'confidence_score', 'area_hectares', 'legal_status', 'status')
    list_filter = ('legal_status', 'status', 'detection_date')
    search_fields = ('id',)
    readonly_fields = ('centroid', 'created_at', 'updated_at')


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ('id', 'alert_type', 'severity', 'status', 'assigned_to', 'created_at')
    list_filter = ('severity', 'status', 'alert_type')
    search_fields = ('title',)


@admin.register(Inspection)
class InspectionAdmin(GISModelAdmin):
    list_display = ('id', 'inspector', 'visit_date', 'outcome', 'created_at')
    list_filter = ('outcome',)
    search_fields = ('inspector__username',)


@admin.register(SiteTimelapse)
class SiteTimelapseAdmin(admin.ModelAdmin):
    list_display = ('detected_site', 'year', 'cloud_cover_pct', 'mean_ndvi', 'mean_bsi', 'fetched_at')
    list_filter = ('year',)