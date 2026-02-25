"""
Celery task for notification reminders.

check_overdue_assignments — runs daily, finds InspectorAssignment records
that have been in PENDING status for 3+ days and emails the inspector.
"""

import logging
from celery import shared_task
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger(__name__)


@shared_task(bind=True, name='notifications.check_overdue_assignments')
def check_overdue_assignments(self):
    """
    Daily task: send reminder emails for assignments pending 3+ days.
    """
    try:
        from apps.accounts.models import InspectorAssignment
        from apps.notifications.services import send_assignment_reminder

        cutoff = timezone.now() - timedelta(days=3)
        overdue = InspectorAssignment.objects.filter(
            status=InspectorAssignment.Status.PENDING,
            assigned_at__lte=cutoff,
        ).select_related('inspector__user', 'alert')

        sent = 0
        for assignment in overdue:
            try:
                days_pending = (timezone.now() - assignment.assigned_at).days
                send_assignment_reminder(assignment, assignment.alert, days_pending)
                sent += 1
            except Exception as exc:
                logger.warning(
                    f'[Notifications] Reminder failed for assignment {assignment.id}: {exc}'
                )

        logger.info(
            f'[Notifications] check_overdue_assignments: '
            f'{overdue.count()} overdue, {sent} reminders sent'
        )
        return {'overdue': overdue.count(), 'sent': sent}

    except Exception as exc:
        logger.error(f'[Notifications] check_overdue_assignments error: {exc}')
        raise
