from django.contrib import admin
from .models import Alert

@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
	list_display = ("alert_id", "timestamp", "location", "confidence", "status", "type", "risk_level")
	search_fields = ("alert_id", "location", "type")
	list_filter = ("status", "risk_level", "type")
