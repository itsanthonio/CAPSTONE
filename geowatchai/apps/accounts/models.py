import uuid
from django.contrib.auth.models import User
from django.contrib.gis.db import models


class UserProfile(models.Model):
    """
    Extends Django's built-in User with role and organization.
    One-to-one with User. Created automatically via signal.
    """

    class Role(models.TextChoices):
        ADMIN = 'admin', 'Admin'
        INSPECTOR = 'inspector', 'Inspector'

    class Organization(models.TextChoices):
        EPA = 'epa', 'Environmental Protection Agency'
        MINERALS_COMMISSION = 'minerals_commission', 'Minerals Commission'
        CERSGIS = 'cersgis', 'CERSGIS'
        FORESTRY_COMMISSION = 'forestry_commission', 'Forestry Commission'
        OTHER = 'other', 'Other'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='profile'
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.ADMIN
    )
    organization = models.CharField(
        max_length=30,
        choices=Organization.choices,
        default=Organization.OTHER
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
        on_delete=models.CASCADE,
        related_name='assignments',
        db_column='alert_id',
    )
    inspector = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
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
