import uuid
from django.contrib.gis.db import models
from django.contrib.gis.geos import Polygon
from apps.jobs.models import Job


class Result(models.Model):
    """Result model for HLS detection pipeline following Anti-Vibe guardrails"""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        Job, 
        on_delete=models.CASCADE, 
        related_name='results',
        db_index=True
    )
    geojson = models.JSONField(
        help_text="GeoJSON FeatureCollection with detection polygons"
    )
    tile_reference = models.CharField(
        max_length=255,
        help_text="Reference to source tiles used for detection"
    )
    summary_statistics = models.JSONField(
        help_text="Summary statistics including total area, confidence distribution, etc."
    )
    total_area_detected = models.FloatField(
        help_text="Total detected area in hectares"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['job_id']),
            models.Index(fields=['created_at']),
        ]
        verbose_name = 'Result'
        verbose_name_plural = 'Results'
    
    def __str__(self):
        return f"Result for Job {self.job.id} - {self.total_area_detected:.2f}ha"
