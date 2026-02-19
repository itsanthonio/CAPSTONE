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
        ANALYST = 'analyst', 'Analyst'
        INSPECTOR = 'inspector', 'Inspector'
        VIEWER = 'viewer', 'Viewer'

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
        default=Role.VIEWER
    )
    organization = models.CharField(
        max_length=30,
        choices=Organization.choices,
        default=Organization.OTHER
    )
    phone_number = models.CharField(max_length=20, blank=True)
    receive_email_alerts = models.BooleanField(default=True)
    receive_sms_alerts = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'User Profile'
        verbose_name_plural = 'User Profiles'

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"
