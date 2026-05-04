import uuid
from django.contrib.auth.models import User
from django.contrib.gis.db import models
from django.contrib.gis.geos import Polygon
from django.utils import timezone


class Job(models.Model):
    """Job model for HLS detection pipeline following Anti-Vibe guardrails"""
    
    class Status(models.TextChoices):
        QUEUED = 'queued', 'Queued'
        VALIDATING = 'validating', 'Validating'
        EXPORTING = 'exporting', 'Exporting'
        PREPROCESSING = 'preprocessing', 'Preprocessing'
        INFERRING = 'inferring', 'Inferring'
        POSTPROCESSING = 'postprocessing', 'Postprocessing'
        STORING = 'storing', 'Storing'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'
        CANCELLED = 'cancelled', 'Cancelled'

    class Source(models.TextChoices):
        MANUAL    = 'manual',    'Manual'
        AUTOMATED = 'automated', 'Automated'
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True
    )
    name = models.CharField(max_length=255, blank=True, default='', help_text="Human-readable name for this job (from uploaded feature name)")
    aoi_geometry = models.PolygonField(help_text="Area of Interest geometry")
    aoi_hash = models.CharField(
        max_length=64,
        db_index=True,
        help_text="Deterministic hash of AOI for deduplication"
    )
    start_date = models.DateField(help_text="Start date for imagery analysis")
    end_date = models.DateField(help_text="End date for imagery analysis")
    model_version = models.CharField(max_length=50, help_text="ML model version used")
    preprocessing_version = models.CharField(max_length=50, help_text="Preprocessing version")
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='jobs',
        help_text='Admin user who triggered this scan',
    )
    organisation = models.ForeignKey(
        'accounts.Organisation',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='jobs',
        help_text='Organisation that owns this job. Null for automated scans (visible to all orgs).',
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(null=True, blank=True)
    
    # Source — manual (user drew AOI) or automated (scanner tile)
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.MANUAL,
        db_index=True,
    )
    scan_tile = models.ForeignKey(
        'scanning.ScanTile',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='jobs',
        help_text='Automated scan tile that triggered this job (null for manual scans)',
    )

    # Detection result fields for frontend access
    total_detections = models.IntegerField(default=0, help_text="Total number of detections")
    illegal_count = models.IntegerField(default=0, help_text="Number of illegal detections")
    result_id = models.UUIDField(null=True, blank=True, help_text="Related result ID")
    detection_data = models.JSONField(default=list, help_text="Detection data as JSON")

    # ML visualization patch images (relative to MEDIA_ROOT, empty until generated)
    img_false_color       = models.CharField(max_length=500, blank=True, default='')
    img_prediction_mask   = models.CharField(max_length=500, blank=True, default='')
    img_probability_heatmap = models.CharField(max_length=500, blank=True, default='')
    img_overlay           = models.CharField(max_length=500, blank=True, default='')
    
    class Meta:
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['created_at']),
            models.Index(fields=['aoi_hash']),
        ]
        ordering = ['-created_at']  # Newest jobs appear first
        verbose_name = 'Job'
        verbose_name_plural = 'Jobs'
    
    def __str__(self):
        return f"Job {self.id} - {self.status}"
    
    @property
    def duration(self):
        """Calculate job duration in seconds"""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None
