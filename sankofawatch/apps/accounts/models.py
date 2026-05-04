import uuid
from django.contrib.auth.models import User
from django.contrib.gis.db import models
from django.core.validators import MinValueValidator


class SystemConfig(models.Model):
    """
    Singleton table (always pk=1) holding platform-wide operational defaults.
    Use SystemConfig.get() rather than constructing directly.
    """
    sla_days = models.IntegerField(
        default=5,
        help_text='Default days from assignment creation to SLA deadline',
    )
    max_pending_assignments = models.IntegerField(
        default=10,
        help_text='Default maximum pending assignments per inspector before they are blocked',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'System Configuration'

    def __str__(self):
        return 'System Configuration'

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Organisation(models.Model):
    """An agency or body that uses the platform."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    sla_days_override = models.IntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(1)],
        help_text='Override the system-wide SLA days for inspectors in this organisation. Leave blank to use the system default.',
    )
    max_pending_override = models.IntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(1)],
        help_text='Override the system-wide max pending assignments for inspectors in this organisation. Leave blank to use the system default.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Organisation'
        verbose_name_plural = 'Organisations'
        ordering = ['name']

    def __str__(self):
        return self.name


class UserPreferences(models.Model):
    """
    User-specific preferences for theme, notifications, and display settings.
    One-to-one with User. Created automatically via signal.
    """
    
    class Theme(models.TextChoices):
        LIGHT = 'light', 'Light'
        DARK = 'dark', 'Dark'
        AUTO = 'auto', 'Auto (System)'
    
    class Layout(models.TextChoices):
        COMPACT = 'compact', 'Compact'
        SPACED = 'spaced', 'Spaced'
        COMFORTABLE = 'comfortable', 'Comfortable'
    
    class Language(models.TextChoices):
        ENGLISH = 'en', 'English'
        FRENCH = 'fr', 'French'
        SPANISH = 'es', 'Spanish'
    
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='preferences'
    )
    
    # Theme & Display
    theme = models.CharField(
        max_length=10,
        choices=Theme.choices,
        default=Theme.LIGHT
    )
    layout = models.CharField(
        max_length=15,
        choices=Layout.choices,
        default=Layout.COMFORTABLE
    )
    font_size = models.CharField(
        max_length=15,
        choices=[
            ('small', 'Small'),
            ('medium', 'Medium'),
            ('large', 'Large'),
            ('extra_large', 'Extra Large'),
        ],
        default='medium'
    )
    high_contrast = models.BooleanField(default=False)
    
    # Dashboard Customization
    show_alerts_widget = models.BooleanField(default=True)
    show_assignments_widget = models.BooleanField(default=True)
    show_reports_widget = models.BooleanField(default=True)
    show_audit_widget = models.BooleanField(default=True)
    default_page = models.CharField(
        max_length=20,
        choices=[
            ('home', 'Home'),
            ('alerts', 'Alerts'),
            ('inspector', 'Inspector'),
            ('report', 'Report'),
        ],
        default='home'
    )
    
    # Notification Preferences
    email_notifications = models.BooleanField(default=True)
    quiet_hours_enabled = models.BooleanField(default=False)
    quiet_hours_start = models.TimeField(default='22:00')
    quiet_hours_end = models.TimeField(default='08:00')
    timezone = models.CharField(
        max_length=50,
        default='UTC'
    )
    language = models.CharField(
        max_length=5,
        choices=Language.choices,
        default=Language.ENGLISH
    )
    critical_alerts_override = models.BooleanField(default=True)
    alert_min_severity = models.CharField(
        max_length=10,
        choices=[
            ('critical', 'Critical only'),
            ('high', 'High & Critical'),
            ('medium', 'Medium, High & Critical'),
            ('all', 'All alerts'),
        ],
        default='all',
        help_text='Minimum severity level that triggers an email notification',
    )
    report_default_days = models.IntegerField(
        choices=[(7, 'Last 7 days'), (30, 'Last 30 days'), (90, 'Last 90 days')],
        default=30,
        help_text='Default date range pre-selected on the report page',
    )

    # Privacy Settings
    activity_visibility = models.BooleanField(default=True)
    location_sharing = models.BooleanField(default=False)
    
    # Mobile Settings
    mobile_push_notifications = models.BooleanField(default=True)
    mobile_offline_sync = models.BooleanField(default=True)
    mobile_theme = models.CharField(
        max_length=10,
        choices=Theme.choices,
        default=Theme.AUTO
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'User Preferences'
        verbose_name_plural = 'User Preferences'
    
    def __str__(self):
        return f"{self.user.username} Preferences"


class UserProfile(models.Model):
    """
    Extends Django's built-in User with role and organization.
    One-to-one with User. Created automatically via signal.
    """

    class Role(models.TextChoices):
        SYSTEM_ADMIN = 'system_admin', 'System Administrator'
        AGENCY_ADMIN = 'agency_admin', 'Agency Administrator'
        INSPECTOR = 'inspector', 'Inspector'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='profile'
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.INSPECTOR
    )
    organisation = models.ForeignKey(
        Organisation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='members'
    )
    phone_number = models.CharField(max_length=20, blank=True)
    receive_email_alerts = models.BooleanField(default=True)
    receive_sms_alerts = models.BooleanField(default=False)
    is_available = models.BooleanField(default=True, help_text="Whether inspector is currently available for assignments")
    current_assignment = models.OneToOneField(
        'detections.Alert',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_alerts'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'User Profile'
        verbose_name_plural = 'User Profiles'

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"


class InspectorAssignment(models.Model):
    """
    Tracks assignment of alerts to inspectors with status and history.
    """

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        RESOLVED = 'resolved', 'Resolved'

    class Outcome(models.TextChoices):
        MINING_CONFIRMED = 'mining_confirmed', 'Mining Confirmed'
        FALSE_POSITIVE = 'false_positive', 'False Positive'
        INCONCLUSIVE = 'inconclusive', 'Inconclusive'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    alert = models.ForeignKey(
        'detections.Alert',
        on_delete=models.PROTECT,
        related_name='assignments',
        db_column='alert_id',
    )
    inspector = models.ForeignKey(
        UserProfile,
        on_delete=models.PROTECT,
        related_name='inspector_assignments'
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING
    )
    outcome = models.CharField(
        max_length=25,
        choices=Outcome.choices,
        null=True,
        blank=True,
        help_text='Field verification outcome set by inspector after site visit'
    )
    visit_date = models.DateField(
        null=True,
        blank=True,
        help_text='Date the inspector physically visited the site'
    )
    evidence_photos = models.JSONField(
        default=list,
        help_text='List of media paths for photos taken at the site'
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    # SLA tracking
    due_date = models.DateField(
        null=True, blank=True,
        help_text='SLA deadline for completing this assignment'
    )
    sla_reminder_sent = models.BooleanField(
        default=False,
        help_text='Whether the SLA reminder email has been sent to the inspector'
    )
    sla_escalated = models.BooleanField(
        default=False,
        help_text='Whether this overdue assignment has been escalated to admins'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Inspector Assignment'
        verbose_name_plural = 'Inspector Assignments'
        ordering = ['-created_at']
        unique_together = [('alert', 'inspector')]

    def __str__(self):
        return f"Alert {self.alert_id} → {self.inspector.user.username}"


class EvidencePhoto(models.Model):
    """
    One record per evidence photo uploaded with a field report.
    Replaces the evidence_photos JSONField on InspectorAssignment for new uploads.
    Stores SHA-256 hash for deduplication and integrity verification.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assignment = models.ForeignKey(
        InspectorAssignment,
        on_delete=models.CASCADE,
        related_name='evidence_photo_set'
    )
    file = models.ImageField(
        upload_to='inspections/%Y/%m/',
        help_text='Uploaded evidence photo'
    )
    sha256_hash = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text='SHA-256 of the file bytes for deduplication and integrity checks'
    )
    original_name = models.CharField(
        max_length=255,
        blank=True,
        help_text='Original filename as uploaded by the inspector'
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Evidence Photo'
        verbose_name_plural = 'Evidence Photos'
        ordering = ['uploaded_at']

    def __str__(self):
        return f"Photo {self.id} — assignment {self.assignment_id}"
