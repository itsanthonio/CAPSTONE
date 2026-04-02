import re
import uuid
from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError


def _validate_notification_link(value):
    """Accept only blank, relative paths (/…), or http(s) URLs.
    Blocks javascript: and data: URIs which could cause XSS in link tags."""
    if not value:
        return
    if re.match(r'^https?://', value) or value.startswith('/'):
        return
    raise ValidationError('Link must be a relative path starting with / or an http(s) URL.')


class NotificationInbox(models.Model):
    """One row per in-app notification delivered to a user."""

    class Kind(models.TextChoices):
        ASSIGNMENT = 'assignment', 'New Assignment'
        ALERT      = 'alert',      'Alert'
        SLA        = 'sla',        'SLA / Reminder'
        REPORT     = 'report',     'Field Report'
        SYSTEM     = 'system',     'System'

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    kind       = models.CharField(max_length=20, choices=Kind.choices, default=Kind.SYSTEM)
    title      = models.CharField(max_length=200)
    body       = models.CharField(max_length=500, blank=True)
    link       = models.CharField(max_length=300, blank=True, validators=[_validate_notification_link])
    is_read    = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes  = [models.Index(fields=['user', 'is_read'], name='notif_user_read_idx')]

    def __str__(self):
        state = 'read' if self.is_read else 'unread'
        return f'{self.user.username}: {self.title} ({state})'
