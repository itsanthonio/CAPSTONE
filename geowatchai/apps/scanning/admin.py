from django.contrib import admin
from .models import ScanTile, AutoScanConfig


@admin.register(ScanTile)
class ScanTileAdmin(admin.ModelAdmin):
    list_display  = ['name', 'priority', 'is_active', 'last_scanned_at', 'scan_count']
    list_filter   = ['priority', 'is_active']
    search_fields = ['name']
    ordering      = ['priority', 'last_scanned_at']
    readonly_fields = ['id', 'scan_count', 'created_at']


@admin.register(AutoScanConfig)
class AutoScanConfigAdmin(admin.ModelAdmin):
    list_display = [
        'is_enabled', 'window_start_hour', 'window_end_hour',
        'tiles_scanned_today', 'rate_limited_date', 'updated_at',
    ]
    readonly_fields = ['tiles_scanned_today', 'last_reset_date', 'updated_at']
