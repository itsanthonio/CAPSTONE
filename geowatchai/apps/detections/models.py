import uuid
from django.contrib.auth.models import User
from django.contrib.gis.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from apps.jobs.models import Job


# ---------------------------------------------------------------------------
# 1. Region / Area of Interest
# ---------------------------------------------------------------------------

class Region(models.Model):
    """
    Named monitoring zone — protected forests, concession areas, hotspots,
    water bodies, or buffer zones.  Used to scope alerts to inspectors.
    """

    class RegionType(models.TextChoices):
        PROTECTED_FOREST = 'protected_forest', 'Protected Forest'
        LEGAL_CONCESSION_AREA = 'legal_concession_area', 'Legal Concession Area'
        HOTSPOT = 'hotspot', 'Known Hotspot'
        WATER_BODY = 'water_body', 'Water Body'
        BUFFER_ZONE = 'buffer_zone', 'Buffer Zone'
        DISTRICT = 'district', 'District'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    region_type = models.CharField(
        max_length=30,
        choices=RegionType.choices,
        db_index=True
    )
    geometry = models.MultiPolygonField(
        srid=4326,
        help_text='Boundary polygon(s) for this region'
    )
    district = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Inspectors assigned to monitor this region
    assigned_inspectors = models.ManyToManyField(
        User,
        blank=True,
        related_name='assigned_regions'
    )

    class Meta:
        verbose_name = 'Region'
        verbose_name_plural = 'Regions'
        indexes = [
            models.Index(fields=['region_type']),
            models.Index(fields=['is_active']),
        ]
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_region_type_display()})"


# ---------------------------------------------------------------------------
# 2. Legal Concession
# ---------------------------------------------------------------------------

class LegalConcession(models.Model):
    """
    Ghana Minerals Commission licensed concession boundaries.
    Used to classify detections as legal or illegal.
    """

    class LicenseType(models.TextChoices):
        LARGE_SCALE = 'large_scale', 'Large Scale'
        SMALL_SCALE = 'small_scale', 'Small Scale'
        EXPLORATION = 'exploration', 'Exploration'
        RECONNAISSANCE = 'reconnaissance', 'Reconnaissance'

    class DataSource(models.TextChoices):
        MINERALS_COMMISSION = 'minerals_commission', 'Minerals Commission'
        CERSGIS = 'cersgis', 'CERSGIS'
        EPA = 'epa', 'Environmental Protection Agency'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    license_number = models.CharField(max_length=100, unique=True, db_index=True)
    concession_name = models.CharField(max_length=255)
    holder_name = models.CharField(max_length=255, help_text='License holder / company name')
    license_type = models.CharField(
        max_length=20,
        choices=LicenseType.choices,
        db_index=True
    )
    geometry = models.MultiPolygonField(
        srid=4326,
        help_text='Concession boundary polygon(s)'
    )
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text='Whether the license is currently valid'
    )
    district = models.CharField(max_length=100, blank=True)
    region = models.ForeignKey(
        Region,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='concessions'
    )
    data_source = models.CharField(
        max_length=30,
        choices=DataSource.choices,
        default=DataSource.MINERALS_COMMISSION
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Legal Concession'
        verbose_name_plural = 'Legal Concessions'
        indexes = [
            models.Index(fields=['license_number']),
            models.Index(fields=['is_active']),
            models.Index(fields=['license_type']),
        ]
        ordering = ['concession_name']

    def __str__(self):
        return f"{self.concession_name} ({self.license_number})"


# ---------------------------------------------------------------------------
# 3. Satellite Imagery
# ---------------------------------------------------------------------------

class SatelliteImagery(models.Model):
    """
    Log of every HLS scene processed — required for reproducibility and audit.
    """

    class Satellite(models.TextChoices):
        LANDSAT_8 = 'L8', 'Landsat 8'
        LANDSAT_9 = 'L9', 'Landsat 9'
        SENTINEL_2A = 'S2A', 'Sentinel-2A'
        SENTINEL_2B = 'S2B', 'Sentinel-2B'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scene_id = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text='HLS tile identifier e.g. HLS.S30.T30NXM.2024001T100000'
    )
    satellite = models.CharField(
        max_length=5,
        choices=Satellite.choices,
        db_index=True
    )
    acquisition_date = models.DateField(db_index=True)
    cloud_cover_pct = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(100.0)],
        help_text='Cloud cover percentage for this scene'
    )
    # Bands processed stored as JSON list e.g. ["B3","B4","B8","B11","B12","BSI"]
    bands_processed = models.JSONField(
        default=list,
        help_text='List of band names processed for this scene'
    )
    preprocessing_version = models.CharField(
        max_length=50,
        help_text='Version of preprocessing pipeline used'
    )
    coverage_geometry = models.PolygonField(
        srid=4326,
        help_text='Spatial extent / tile boundary of this scene'
    )
    gcs_path = models.CharField(
        max_length=500,
        blank=True,
        help_text='GCS path to the exported GeoTIFF e.g. gs://bucket/path/file.tif'
    )
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Satellite Imagery'
        verbose_name_plural = 'Satellite Imagery'
        indexes = [
            models.Index(fields=['scene_id']),
            models.Index(fields=['acquisition_date']),
            models.Index(fields=['satellite']),
        ]
        ordering = ['-acquisition_date']

    def __str__(self):
        return f"{self.scene_id} ({self.acquisition_date})"


# ---------------------------------------------------------------------------
# 4. Model Run
# ---------------------------------------------------------------------------

class ModelRun(models.Model):
    """
    Records every inference run — which checkpoint, which bands, what metrics.
    DetectedSite links here so you always know what produced a detection.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    model_name = models.CharField(
        max_length=100,
        help_text='Human-readable model name e.g. FPN-ResNet50-6band'
    )
    model_version = models.CharField(
        max_length=50,
        db_index=True,
        help_text='Version tag e.g. v1.0, best_precision'
    )
    checkpoint_path = models.CharField(
        max_length=500,
        help_text='Local or GCS path to the .pth checkpoint file'
    )
    architecture = models.CharField(
        max_length=100,
        default='FPN',
        help_text='Model architecture e.g. FPN, UNet, SegFormer'
    )
    encoder = models.CharField(
        max_length=100,
        default='resnet50',
        help_text='Encoder backbone e.g. resnet50'
    )
    bands_used = models.JSONField(
        default=list,
        help_text='Ordered list of band names used as input'
    )
    # Validation metrics at training time
    val_precision = models.FloatField(
        null=True, blank=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )
    val_recall = models.FloatField(
        null=True, blank=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )
    val_iou = models.FloatField(
        null=True, blank=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )
    val_f1 = models.FloatField(
        null=True, blank=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )
    inference_threshold = models.FloatField(
        default=0.5,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text='Probability threshold used during this run'
    )
    run_at = models.DateTimeField(auto_now_add=True, db_index=True)
    job = models.ForeignKey(
        Job,
        on_delete=models.CASCADE,
        related_name='model_runs'
    )

    class Meta:
        verbose_name = 'Model Run'
        verbose_name_plural = 'Model Runs'
        indexes = [
            models.Index(fields=['model_version']),
            models.Index(fields=['run_at']),
        ]
        ordering = ['-run_at']

    def __str__(self):
        return f"{self.model_name} v{self.model_version} — job {self.job_id}"


# ---------------------------------------------------------------------------
# 5. Detected Site
# ---------------------------------------------------------------------------

class DetectedSite(models.Model):
    """
    One record per detected mining polygon output by the model.
    Central entity that everything else hangs off.
    """

    class Status(models.TextChoices):
        PENDING_REVIEW = 'pending_review', 'Pending Review'
        CONFIRMED_ILLEGAL = 'confirmed_illegal', 'Confirmed Illegal'
        CONFIRMED_LEGAL = 'confirmed_legal', 'Confirmed Legal'
        FALSE_POSITIVE = 'false_positive', 'False Positive'
        DISMISSED = 'dismissed', 'Dismissed'

    class LegalStatus(models.TextChoices):
        ILLEGAL = 'illegal', 'Illegal'
        LEGAL = 'legal', 'Legal'
        UNKNOWN = 'unknown', 'Unknown'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Spatial
    geometry = models.PolygonField(
        srid=4326,
        help_text='Detection polygon in WGS-84'
    )
    centroid = models.PointField(
        srid=4326,
        null=True,
        blank=True,
        help_text='Centroid of detection polygon — auto-computed'
    )

    # Detection metadata
    confidence_score = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text='Mean probability score within this polygon'
    )
    area_hectares = models.FloatField(
        validators=[MinValueValidator(0.0)],
        help_text='Area of detection polygon in hectares'
    )
    detection_date = models.DateField(
        db_index=True,
        help_text='Date the imagery used for this detection was acquired'
    )

    # Review status
    status = models.CharField(
        max_length=25,
        choices=Status.choices,
        default=Status.PENDING_REVIEW,
        db_index=True
    )

    # Legal classification — set by spatial join against LegalConcession
    legal_status = models.CharField(
        max_length=10,
        choices=LegalStatus.choices,
        default=LegalStatus.UNKNOWN,
        db_index=True
    )
    intersecting_concession = models.ForeignKey(
        LegalConcession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='detections',
        help_text='Concession this site overlaps, if any'
    )
    concession_overlap_pct = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(100.0)],
        help_text='Percentage of detection polygon within a concession boundary'
    )

    # Region
    region = models.ForeignKey(
        Region,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='detections'
    )

    # Pipeline provenance
    job = models.ForeignKey(
        Job,
        on_delete=models.CASCADE,
        related_name='detected_sites'
    )
    model_run = models.ForeignKey(
        ModelRun,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='detected_sites'
    )
    satellite_imagery = models.ForeignKey(
        SatelliteImagery,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='detected_sites'
    )

    # Recurrence — links to earlier detection of the same physical location
    first_detected_at = models.DateField(
        null=True,
        blank=True,
        help_text='Date this location was first ever detected'
    )
    recurrence_count = models.PositiveIntegerField(
        default=1,
        help_text='How many times this location has been flagged (>=2 means recurring)'
    )

    # Reviewer
    reviewed_by = models.ForeignKey(
        'auth.User',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reviewed_sites'
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Detected Site'
        verbose_name_plural = 'Detected Sites'
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['legal_status']),
            models.Index(fields=['detection_date']),
            models.Index(fields=['confidence_score']),
        ]
        ordering = ['-detection_date', '-confidence_score']

    def __str__(self):
        return f"Site {self.id} | {self.legal_status} | {self.confidence_score:.2f} | {self.detection_date}"

    def save(self, *args, **kwargs):
        # Auto-compute centroid from geometry
        if self.geometry and not self.centroid:
            self.centroid = self.geometry.centroid
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# 6. Alert
# ---------------------------------------------------------------------------

class Alert(models.Model):
    """
    Generated when a DetectedSite is classified as illegal.
    Drives the inspector notification and dispatch workflow.
    """

    class AlertType(models.TextChoices):
        NEW_DETECTION = 'new_detection', 'New Detection'
        EXPANSION_DETECTED = 'expansion_detected', 'Site Expansion Detected'
        RECURRING_SITE = 'recurring_site', 'Recurring Illegal Site'
        HIGH_CONFIDENCE = 'high_confidence', 'High Confidence Detection'

    class Severity(models.TextChoices):
        LOW = 'low', 'Low'
        MEDIUM = 'medium', 'Medium'
        HIGH = 'high', 'High'
        CRITICAL = 'critical', 'Critical'

    class AlertStatus(models.TextChoices):
        OPEN = 'open', 'Open'
        ACKNOWLEDGED = 'acknowledged', 'Acknowledged'
        DISPATCHED = 'dispatched', 'Inspector Dispatched'
        RESOLVED = 'resolved', 'Resolved'
        DISMISSED = 'dismissed', 'Dismissed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    detected_site = models.ForeignKey(
        DetectedSite,
        on_delete=models.CASCADE,
        related_name='alerts'
    )
    alert_type = models.CharField(
        max_length=25,
        choices=AlertType.choices,
        default=AlertType.NEW_DETECTION,
        db_index=True
    )
    severity = models.CharField(
        max_length=10,
        choices=Severity.choices,
        default=Severity.MEDIUM,
        db_index=True
    )
    status = models.CharField(
        max_length=15,
        choices=AlertStatus.choices,
        default=AlertStatus.OPEN,
        db_index=True
    )
    assigned_to = models.ForeignKey(
        'auth.User',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='assigned_alerts'
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_notes = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Alert'
        verbose_name_plural = 'Alerts'
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['severity']),
            models.Index(fields=['created_at']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f"Alert {self.id} | {self.severity} | {self.status}"


# ---------------------------------------------------------------------------
# 7. Inspection
# ---------------------------------------------------------------------------

class Inspection(models.Model):
    """
    Field verification record for a detected site.
    Inspector visits the location and records ground truth.
    """

    class InspectionOutcome(models.TextChoices):
        CONFIRMED_ILLEGAL = 'confirmed_illegal', 'Confirmed Illegal Mining'
        CONFIRMED_LEGAL = 'confirmed_legal', 'Confirmed Legal Activity'
        FALSE_POSITIVE = 'false_positive', 'False Positive — No Mining'
        INCONCLUSIVE = 'inconclusive', 'Inconclusive'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    alert = models.ForeignKey(
        Alert,
        on_delete=models.CASCADE,
        related_name='inspections'
    )
    detected_site = models.ForeignKey(
        DetectedSite,
        on_delete=models.CASCADE,
        related_name='inspections'
    )
    inspector = models.ForeignKey(
        'auth.User',
        on_delete=models.PROTECT,
        related_name='inspections'
    )
    visit_date = models.DateField(db_index=True)
    gps_coordinates = models.PointField(
        srid=4326,
        null=True,
        blank=True,
        help_text='GPS location where inspector stood during visit'
    )
    outcome = models.CharField(
        max_length=25,
        choices=InspectionOutcome.choices,
        null=True,
        blank=True,
        db_index=True
    )
    field_notes = models.TextField(blank=True)
    # Evidence photo keys — store GCS paths or relative media paths
    evidence_photos = models.JSONField(
        default=list,
        help_text='List of GCS paths or media paths to evidence photos'
    )
    drone_footage_path = models.CharField(
        max_length=500,
        blank=True,
        help_text='GCS path to drone footage from this visit'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Inspection'
        verbose_name_plural = 'Inspections'
        indexes = [
            models.Index(fields=['visit_date']),
            models.Index(fields=['outcome']),
        ]
        ordering = ['-visit_date']

    def __str__(self):
        return f"Inspection {self.id} | {self.visit_date} | {self.outcome or 'pending'}"


# ---------------------------------------------------------------------------
# 8. Site Timelapse
# ---------------------------------------------------------------------------

class SiteTimelapse(models.Model):
    """
    Historical RGB composites for a detected site, pulled from GEE.
    One record per year-snapshot per site.
    Fetched automatically when the model flags a site — no confirmation needed.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    detected_site = models.ForeignKey(
        DetectedSite,
        on_delete=models.CASCADE,
        related_name='timelapse_frames'
    )
    year = models.PositiveSmallIntegerField(
        db_index=True,
        help_text='Year this composite represents'
    )
    acquisition_period = models.CharField(
        max_length=20,
        help_text='Period label e.g. "2020-Q1" or "2020"'
    )
    # URL or GCS path to the RGB tile / thumbnail
    thumbnail_url = models.URLField(
        max_length=1000,
        blank=True,
        help_text='Public URL to the RGB thumbnail image'
    )
    gcs_path = models.CharField(
        max_length=500,
        blank=True,
        help_text='GCS path to the full-resolution GeoTIFF for this year'
    )
    cloud_cover_pct = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(100.0)]
    )
    # Rough NDVI to show vegetation change over time
    mean_ndvi = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(-1.0), MaxValueValidator(1.0)]
    )
    # BSI to show bare soil / disturbance progression
    mean_bsi = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(-1.0), MaxValueValidator(1.0)]
    )
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Site Timelapse Frame'
        verbose_name_plural = 'Site Timelapse Frames'
        unique_together = [('detected_site', 'year')]
        indexes = [
            models.Index(fields=['year']),
        ]
        ordering = ['detected_site', 'year']

    def __str__(self):
        return f"Timelapse {self.detected_site_id} — {self.year}"
