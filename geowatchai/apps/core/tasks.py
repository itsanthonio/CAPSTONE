"""
Celery tasks for the core orchestrator module.

This module provides async tasks for triggering the detection pipeline
and periodic maintenance tasks (alert escalation, etc.).
"""

import logging
from celery import shared_task
from .orchestrator import trigger_detection_pipeline

logger = logging.getLogger(__name__)


@shared_task
def escalate_stale_alerts():
    """
    Periodic task: find critical alerts that have been open/unacknowledged
    for 48+ hours and email the ops team, then mark them escalated so they
    don't spam on the next run.
    """
    from datetime import timedelta
    from django.utils import timezone
    from django.core.mail import send_mail
    from django.conf import settings
    from apps.detections.models import Alert, AuditLog

    threshold = timezone.now() - timedelta(hours=48)

    stale = Alert.objects.filter(
        severity=Alert.Severity.CRITICAL,
        status__in=[Alert.AlertStatus.OPEN, Alert.AlertStatus.ACKNOWLEDGED],
        created_at__lt=threshold,
        escalated_at__isnull=True,
    ).select_related('detected_site__region')

    count = stale.count()
    if count == 0:
        return {'escalated': 0}

    # Build email
    site_url = getattr(settings, 'SITE_URL', 'http://localhost:8000')
    lines = [f"  • Alert {str(a.id)[:8].upper()} — {a.title} ({a.status}) "
             f"[{site_url}/dashboard/alerts/?highlight={a.id}]"
             for a in stale]
    body = (
        f"{count} critical alert(s) have been open for 48+ hours with no action:\n\n"
        + "\n".join(lines)
        + "\n\nPlease review and dispatch inspectors immediately."
    )

    ops_emails = list(getattr(settings, 'OPS_EMAILS', []))
    if not ops_emails:
        # Fall back to ADMINS list
        ops_emails = [email for _, email in getattr(settings, 'ADMINS', [])]

    if ops_emails:
        try:
            send_mail(
                subject=f"[GalamseyWatch] {count} Critical Alert(s) Unactioned for 48h",
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=ops_emails,
                fail_silently=True,
            )
        except Exception as exc:
            logger.error(f"Escalation email failed: {exc}")

    now = timezone.now()
    for alert in stale:
        alert.escalated_at = now
        alert.save(update_fields=['escalated_at'])
        AuditLog.objects.create(
            user=None,
            action='alert.escalated',
            object_id=str(alert.id),
            detail={'hours_open': round((now - alert.created_at).total_seconds() / 3600, 1)},
        )

    logger.info(f"Escalated {count} stale critical alert(s).")
    return {'escalated': count}


@shared_task
def check_assignment_sla():
    """
    Periodic task: check all pending assignments for SLA violations.

    - Reminder: due_date passed + sla_reminder_sent=False → email inspector
    - Escalation: 2+ days past due_date + sla_escalated=False → email admins + audit log
    """
    from datetime import date, timedelta
    from apps.accounts.models import InspectorAssignment
    from apps.detections.models import Alert, AuditLog

    today = date.today()
    reminded = 0
    escalated = 0

    # ── Reminder: any overdue assignment not yet reminded ─────────────────────
    overdue = InspectorAssignment.objects.filter(
        status=InspectorAssignment.Status.PENDING,
        due_date__lte=today,
        sla_reminder_sent=False,
        sla_escalated=False,
    ).select_related('inspector__user', 'alert__detected_site__region')

    for assignment in overdue:
        days_overdue = (today - assignment.due_date).days
        try:
            alert = Alert.objects.get(id=assignment.alert_id)
            from apps.notifications.services import send_sla_reminder
            send_sla_reminder(assignment, alert, days_overdue)
        except Exception as exc:
            logger.warning(f'SLA reminder failed for assignment {assignment.id}: {exc}')
        assignment.sla_reminder_sent = True
        assignment.save(update_fields=['sla_reminder_sent'])
        reminded += 1

    # ── Escalation: 2+ days overdue, not yet escalated ───────────────────────
    escalation_threshold = today - timedelta(days=2)
    critical_overdue = InspectorAssignment.objects.filter(
        status=InspectorAssignment.Status.PENDING,
        due_date__lte=escalation_threshold,
        sla_escalated=False,
    ).select_related('inspector__user', 'alert__detected_site__region')

    for assignment in critical_overdue:
        days_overdue = (today - assignment.due_date).days
        try:
            alert = Alert.objects.get(id=assignment.alert_id)
            from apps.notifications.services import send_sla_escalation
            send_sla_escalation(assignment, alert, days_overdue)
        except Exception as exc:
            logger.warning(f'SLA escalation email failed for assignment {assignment.id}: {exc}')

        assignment.sla_escalated = True
        assignment.save(update_fields=['sla_escalated'])

        try:
            AuditLog.objects.create(
                user=None,
                action='assignment.sla_escalated',
                object_id=str(assignment.id),
                detail={
                    'inspector': assignment.inspector.user.username,
                    'days_overdue': days_overdue,
                    'due_date': str(assignment.due_date),
                },
            )
        except Exception:
            pass
        escalated += 1

    logger.info(f'SLA check: {reminded} reminded, {escalated} escalated.')
    return {'reminded': reminded, 'escalated': escalated}


@shared_task(bind=True, max_retries=3)
def run_detection_task(self, job_id: str, threshold: float = 0.5, min_area: float = 100.0):
    """
    Async task to run detection pipeline for a job.
    
    Args:
        job_id: Job UUID to process
        threshold: Probability threshold for binary classification
        min_area: Minimum polygon area in square meters
        
    Returns:
        dict: Processing result
    """
    try:
        logger.info(f"Starting detection pipeline for job {job_id}")
        
        # Trigger the detection pipeline
        result = trigger_detection_pipeline(job_id, threshold, min_area)
        
        logger.info(f"Detection pipeline completed for job {job_id}: {result['status']}")
        return result
        
    except Exception as exc:
        logger.error(f"Detection pipeline failed for job {job_id}: {str(exc)}")
        
        # Retry the task if possible
        if self.request.retries < self.max_retries:
            logger.info(f"Retrying detection pipeline for job {job_id} (attempt {self.request.retries + 1})")
            raise self.retry(countdown=60 * (2 ** self.request.retries), exc=exc)
        
        # Mark as failed after max retries
        logger.error(f"Detection pipeline permanently failed for job {job_id}")
        raise exc
