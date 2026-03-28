import uuid
from django.db import models
from django.contrib.gis.db import models as gis_models


class ScanTile(models.Model):
    """
    A pre-defined geographic tile that the automated scanner works through.
    Ghana is divided into a grid of these tiles. Hotspot tiles (known mining
    belt areas) are prioritised and picked first each scanning window.
    """

    class Priority(models.TextChoices):
        HOTSPOT = 'hotspot', 'Hotspot'
        NORMAL  = 'normal',  'Normal'

    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name            = models.CharField(max_length=200)
    geometry        = gis_models.PolygonField(srid=4326)
    priority        = models.CharField(
        max_length=20,
        choices=Priority.choices,
        default=Priority.NORMAL,
        db_index=True,
    )
    is_active       = models.BooleanField(default=True, db_index=True)
    last_scanned_at = models.DateTimeField(null=True, blank=True, db_index=True)
    scan_count      = models.IntegerField(default=0)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['priority', 'last_scanned_at']
        indexes = [
            models.Index(fields=['priority', 'is_active', 'last_scanned_at']),
        ]

    def __str__(self):
        return f"{self.name} ({self.priority})"


class AutoScanConfig(models.Model):
    """
    Singleton configuration for the automated scanning system.
    Controls scanning window, pause/resume, and daily rate-limit tracking.
    Only one row should ever exist (pk=1).
    """

    is_enabled          = models.BooleanField(default=True)
    window_start_hour   = models.IntegerField(default=6)   # 6am
    window_end_hour     = models.IntegerField(default=18)  # 6pm
    rate_limited_date   = models.DateField(null=True, blank=True)
    tiles_scanned_today = models.IntegerField(default=0)
    last_reset_date     = models.DateField(null=True, blank=True)
    updated_at          = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Auto Scan Config'

    @classmethod
    def get(cls):
        """Always returns the singleton row, creating it if needed."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def is_within_window(self):
        """True if current local time is inside the configured scanning window."""
        from django.utils import timezone
        now = timezone.localtime()
        return self.window_start_hour <= now.hour < self.window_end_hour

    def is_rate_limited_today(self):
        """True if GEE already rate-limited us today."""
        from django.utils.timezone import now
        today = now().date()
        return self.rate_limited_date == today

    def reset_daily_counter_if_needed(self):
        """Reset daily tile counter at the start of each new day."""
        from django.utils.timezone import now
        today = now().date()
        if self.last_reset_date != today:
            self.tiles_scanned_today = 0
            self.last_reset_date = today
            self.save(update_fields=['tiles_scanned_today', 'last_reset_date'])

    def __str__(self):
        status = 'enabled' if self.is_enabled else 'paused'
        return f"AutoScanConfig ({status}, {self.window_start_hour}:00–{self.window_end_hour}:00)"


class OrgScanConfig(models.Model):
    """
    Per-organisation scanning configuration.
    Controls whether automated scanning is enabled for an org and its daily window.
    When is_enabled=False the scanner skips jobs that would notify this org.
    """
    organisation      = models.OneToOneField(
        'accounts.Organisation',
        on_delete=models.CASCADE,
        related_name='scan_config',
    )
    is_enabled        = models.BooleanField(default=True)
    window_start_hour = models.IntegerField(default=6)
    window_end_hour   = models.IntegerField(default=18)
    updated_at        = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Org Scan Config'

    @classmethod
    def get_for_org(cls, org):
        obj, _ = cls.objects.get_or_create(organisation=org)
        return obj

    def __str__(self):
        status = 'enabled' if self.is_enabled else 'paused'
        return f"{self.organisation.name} ({status}, {self.window_start_hour}:00–{self.window_end_hour}:00)"


class GhanaPlace(models.Model):
    """
    Local gazetteer of Ghana place names.
    Populated by the import_ghana_places management command (GeoNames bulk data).
    Google Geocoding API results are also cached here so future lookups are instant.
    """
    name        = models.CharField(max_length=200, db_index=True)
    ascii_name  = models.CharField(max_length=200, db_index=True)
    latitude    = models.FloatField()
    longitude   = models.FloatField()
    feature_code = models.CharField(max_length=10, blank=True)  # PPL, PPLA, PPLX, etc.
    population  = models.IntegerField(default=0)
    region      = models.CharField(max_length=100, blank=True)  # admin1 name
    source      = models.CharField(max_length=20, default='geonames')  # 'geonames' or 'google'

    class Meta:
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['ascii_name']),
        ]

    def __str__(self):
        return f"{self.name} ({self.latitude:.4f}, {self.longitude:.4f})"
