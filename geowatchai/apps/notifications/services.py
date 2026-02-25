"""
Notification service — sends HTML email notifications at key points in the workflow.

Five email types:
  ADMIN:
    1. send_scan_completed   — scan finished, summary of what was found
    2. send_scan_failed      — scan pipeline failed, includes reason
    3. send_field_report_received — inspector submitted a field report

  INSPECTOR:
    4. send_new_assignment   — inspector has been assigned a new site
    5. send_assignment_reminder — pending assignment older than 3 days

All functions are silent on failure — email should never crash the main flow.
Recipients are filtered by UserProfile.receive_email_alerts = True.
"""

import logging
from django.core.mail import EmailMultiAlternatives
from django.conf import settings

logger = logging.getLogger(__name__)

APP_NAME = getattr(settings, 'APP_NAME', 'GalamseyWatch AI')

# ─────────────────────────────────────────────
# HTML primitives
# ─────────────────────────────────────────────

def _wrap(body_html, preview_text=''):
    """Wrap content in the standard branded email shell."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <title>{APP_NAME}</title>
  <!--[if mso]><noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript><![endif]-->
</head>
<body style="margin:0;padding:0;background:#f0f4f0;font-family:Arial,Helvetica,sans-serif;">
  <!-- preview text -->
  <span style="display:none;max-height:0;overflow:hidden;">{preview_text}</span>

  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f4f0;padding:32px 16px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

        <!-- HEADER -->
        <tr>
          <td style="background:#1B4332;border-radius:12px 12px 0 0;padding:28px 36px;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td>
                  <span style="display:inline-block;background:#40916C;color:#fff;font-size:11px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;padding:4px 10px;border-radius:4px;">Satellite Monitoring</span>
                  <div style="color:#fff;font-size:22px;font-weight:700;margin-top:10px;">{APP_NAME}</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- BODY -->
        <tr>
          <td style="background:#ffffff;padding:36px 36px 28px 36px;">
            {body_html}
          </td>
        </tr>

        <!-- FOOTER -->
        <tr>
          <td style="background:#f8faf8;border-top:1px solid #e5ede5;border-radius:0 0 12px 12px;padding:20px 36px;text-align:center;">
            <p style="margin:0;color:#6b7280;font-size:12px;">
              This email was sent by {APP_NAME}. You are receiving this because you have email alerts enabled.
            </p>
            <p style="margin:8px 0 0;color:#9ca3af;font-size:11px;">
              To stop receiving these emails, disable alerts in your account settings.
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _heading(text):
    return f'<h1 style="margin:0 0 6px;color:#1B4332;font-size:22px;font-weight:700;">{text}</h1>'


def _subheading(text):
    return f'<p style="margin:0 0 24px;color:#6b7280;font-size:14px;">{text}</p>'


def _divider():
    return '<hr style="border:none;border-top:1px solid #e5ede5;margin:24px 0;"/>'


def _stat_row(items):
    """
    items = list of (label, value, value_color) tuples.
    Renders as a horizontal row of stat boxes.
    """
    cells = ''
    for label, value, color in items:
        cells += f"""
        <td align="center" style="padding:0 8px;">
          <div style="background:#f8faf8;border:1px solid #e5ede5;border-radius:8px;padding:14px 20px;min-width:90px;">
            <div style="font-size:24px;font-weight:700;color:{color};">{value}</div>
            <div style="font-size:11px;color:#6b7280;margin-top:4px;text-transform:uppercase;letter-spacing:0.5px;">{label}</div>
          </div>
        </td>"""
    return f'<table cellpadding="0" cellspacing="0" style="margin:20px 0;"><tr>{cells}</tr></table>'


def _detail_table(rows):
    """
    rows = list of (label, value) tuples.
    Renders as a clean two-column details table.
    """
    html = '<table cellpadding="0" cellspacing="0" width="100%" style="margin:16px 0;">'
    for i, (label, value) in enumerate(rows):
        bg = '#f8faf8' if i % 2 == 0 else '#ffffff'
        html += f"""
        <tr style="background:{bg};">
          <td style="padding:10px 12px;font-size:13px;color:#6b7280;font-weight:600;width:140px;white-space:nowrap;">{label}</td>
          <td style="padding:10px 12px;font-size:13px;color:#111827;">{value}</td>
        </tr>"""
    html += '</table>'
    return html


def _badge(text, color, bg):
    return f'<span style="display:inline-block;background:{bg};color:{color};font-size:12px;font-weight:700;padding:4px 12px;border-radius:20px;">{text}</span>'


def _alert_box(text, kind='info'):
    styles = {
        'info':    ('background:#eff6ff;border-left:4px solid #3b82f6;color:#1e40af;'),
        'warning': ('background:#fffbeb;border-left:4px solid #f59e0b;color:#92400e;'),
        'danger':  ('background:#fef2f2;border-left:4px solid #ef4444;color:#991b1b;'),
        'success': ('background:#f0fdf4;border-left:4px solid #22c55e;color:#166534;'),
    }
    style = styles.get(kind, styles['info'])
    return f'<div style="{style}border-radius:6px;padding:14px 16px;margin:16px 0;font-size:13px;">{text}</div>'


def _cta_button(url, label):
    return f"""
    <table cellpadding="0" cellspacing="0" style="margin:28px 0 8px;">
      <tr>
        <td style="background:#1B4332;border-radius:8px;padding:0;">
          <a href="{url}" style="display:inline-block;padding:14px 32px;color:#fff;font-size:14px;font-weight:700;text-decoration:none;letter-spacing:0.3px;">{label}</a>
        </td>
      </tr>
    </table>"""


def _notes_block(notes):
    return f"""
    <div style="background:#f8faf8;border:1px solid #e5ede5;border-radius:8px;padding:14px 16px;margin:16px 0;">
      <div style="font-size:11px;color:#6b7280;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">Notes</div>
      <div style="font-size:13px;color:#374151;line-height:1.6;">{notes}</div>
    </div>"""


# ─────────────────────────────────────────────
# Recipient helpers
# ─────────────────────────────────────────────

def _admin_recipients():
    try:
        from apps.accounts.models import UserProfile
        profiles = UserProfile.objects.filter(
            role=UserProfile.Role.ADMIN,
            receive_email_alerts=True,
            user__email__gt='',
        ).select_related('user')
        return [p.user.email for p in profiles if p.user.email]
    except Exception as exc:
        logger.warning(f'[Notifications] Could not fetch admin recipients: {exc}')
        return []


def _inspector_recipient(inspector_profile):
    try:
        if inspector_profile.receive_email_alerts and inspector_profile.user.email:
            return inspector_profile.user.email
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────
# 1. Scan completed (Admin)
# ─────────────────────────────────────────────

def send_scan_completed(job):
    recipients = _admin_recipients()
    if not recipients:
        return

    illegal = job.illegal_count or 0
    total   = job.total_detections or 0
    legal   = total - illegal

    subject = f'Scan Complete — {total} site(s) detected | {APP_NAME}'

    if illegal == 0:
        summary_box = _alert_box('No illegal mining sites were detected in this scan.', 'success')
    else:
        summary_box = _alert_box(
            f'<strong>{illegal} illegal site(s)</strong> have been flagged and alerts created.',
            'danger'
        )

    body = (
        _heading('Satellite Scan Complete')
        + _subheading(f'Date range: {job.start_date} &rarr; {job.end_date}')
        + _stat_row([
            ('Total Sites', total,   '#1B4332'),
            ('Illegal',     illegal, '#dc2626' if illegal > 0 else '#6b7280'),
            ('Legal',       legal,   '#16a34a'),
        ])
        + _divider()
        + _detail_table([
            ('Job ID',     str(job.id)),
            ('Start date', str(job.start_date)),
            ('End date',   str(job.end_date)),
        ])
        + summary_box
        + _cta_button(_site_url('/dashboard/alerts/'), 'View Alerts &rarr;')
    )

    plain = (
        f"Satellite scan complete.\n\n"
        f"Job: {job.id}\nDate range: {job.start_date} to {job.end_date}\n"
        f"Total: {total}  Illegal: {illegal}  Legal: {legal}\n\n"
        f"{'No illegal sites detected.' if illegal == 0 else f'{illegal} illegal site(s) flagged.'}\n\n"
        f"View alerts: {_site_url('/dashboard/alerts/')}"
    )

    _send(subject, plain, _wrap(body, f'{total} sites detected, {illegal} illegal'), recipients, tag='scan_completed')


# ─────────────────────────────────────────────
# 2. Scan failed (Admin)
# ─────────────────────────────────────────────

def send_scan_failed(job):
    recipients = _admin_recipients()
    if not recipients:
        return

    reason = getattr(job, 'failure_reason', None) or 'Unknown error — check server logs.'

    subject = f'Scan Failed — Action Required | {APP_NAME}'

    body = (
        _heading('Satellite Scan Failed')
        + _subheading(f'Date range: {job.start_date} &rarr; {job.end_date}')
        + _alert_box('<strong>The scan pipeline encountered an error and could not complete.</strong> Please review the details below and retry if needed.', 'danger')
        + _detail_table([
            ('Job ID',     str(job.id)),
            ('Start date', str(job.start_date)),
            ('End date',   str(job.end_date)),
            ('Reason',     f'<span style="color:#dc2626;">{reason}</span>'),
        ])
        + _cta_button(_site_url('/dashboard/home/'), 'Go to Dashboard &rarr;')
    )

    plain = (
        f"Scan failed.\n\nJob: {job.id}\n"
        f"Date range: {job.start_date} to {job.end_date}\n"
        f"Reason: {reason}\n\n"
        f"Dashboard: {_site_url('/dashboard/home/')}"
    )

    _send(subject, plain, _wrap(body, 'A scan job has failed and requires attention'), recipients, tag='scan_failed')


# ─────────────────────────────────────────────
# 3. Field report received (Admin)
# ─────────────────────────────────────────────

def send_field_report_received(assignment, alert):
    recipients = _admin_recipients()
    if not recipients:
        return

    outcome_display = dict(
        assignment.__class__.Outcome.choices
    ).get(assignment.outcome, assignment.outcome or '—')

    inspector_name = (
        assignment.inspector.user.get_full_name()
        or assignment.inspector.user.username
    )
    visit_date = str(assignment.visit_date) if assignment.visit_date else 'Not recorded'
    notes      = assignment.notes or 'No notes provided.'

    site = getattr(alert, 'detected_site', None)
    area = f'{site.area_hectares:.2f} ha' if site else '—'
    conf = f'{site.confidence_score:.0%}' if site else '—'
    region = site.region.name if site and site.region else '—'

    OUTCOME_BADGE = {
        'mining_confirmed': _badge('Mining Confirmed', '#991b1b', '#fef2f2'),
        'false_positive':   _badge('False Positive',   '#166534', '#f0fdf4'),
        'inconclusive':     _badge('Inconclusive',     '#92400e', '#fffbeb'),
    }
    outcome_badge = OUTCOME_BADGE.get(assignment.outcome, _badge(outcome_display, '#374151', '#f3f4f6'))

    subject = f'Field Report Received — {outcome_display} | {APP_NAME}'

    body = (
        _heading('Field Report Submitted')
        + _subheading(f'Inspector: {inspector_name} &nbsp;·&nbsp; Visit date: {visit_date}')
        + f'<div style="margin-bottom:20px;">Outcome: &nbsp;{outcome_badge}</div>'
        + _divider()
        + _detail_table([
            ('Inspector',  inspector_name),
            ('Visit date', visit_date),
            ('Region',     region),
            ('Area',       area),
            ('Confidence', conf),
        ])
        + _notes_block(notes)
        + _cta_button(_site_url('/dashboard/alerts/'), 'View Alert &rarr;')
    )

    plain = (
        f"Field report received.\n\n"
        f"Outcome: {outcome_display}\nInspector: {inspector_name}\n"
        f"Visit date: {visit_date}\nRegion: {region}\n"
        f"Area: {area}  Confidence: {conf}\n\n"
        f"Notes: {notes}\n\n"
        f"View alert: {_site_url('/dashboard/alerts/')}"
    )

    _send(subject, plain, _wrap(body, f'Field report: {outcome_display} by {inspector_name}'), recipients, tag='field_report_received')


# ─────────────────────────────────────────────
# 4. New assignment (Inspector)
# ─────────────────────────────────────────────

def send_new_assignment(assignment, alert):
    recipient = _inspector_recipient(assignment.inspector)
    if not recipient:
        return

    inspector_name = (
        assignment.inspector.user.get_full_name()
        or assignment.inspector.user.username
    )

    site     = getattr(alert, 'detected_site', None) if alert else None
    severity = alert.get_severity_display() if alert else '—'
    area     = f'{site.area_hectares:.2f} ha' if site else '—'
    conf     = f'{site.confidence_score:.0%}' if site else '—'
    region   = site.region.name if site and site.region else '—'

    centroid = site.centroid if site else None
    coords   = (
        f'{centroid.y:.4f}&deg; N, {abs(centroid.x):.4f}&deg; W'
        if centroid else '—'
    )

    SEVERITY_BADGE = {
        'Critical': _badge('Critical', '#991b1b', '#fef2f2'),
        'High':     _badge('High',     '#92400e', '#fff7ed'),
        'Medium':   _badge('Medium',   '#854d0e', '#fefce8'),
        'Low':      _badge('Low',      '#1e40af', '#eff6ff'),
    }
    severity_badge = SEVERITY_BADGE.get(severity, _badge(severity, '#374151', '#f3f4f6'))

    notes = assignment.notes or 'No additional instructions.'

    subject = f'New Field Assignment — {severity} Alert | {APP_NAME}'

    body = (
        f'<p style="margin:0 0 4px;font-size:15px;color:#374151;">Hello <strong>{inspector_name}</strong>,</p>'
        + '<p style="margin:0 0 24px;font-size:14px;color:#6b7280;">You have been assigned a new field verification task. Please review the details below and visit the site as soon as possible.</p>'
        + f'<div style="margin-bottom:20px;">Alert severity: &nbsp;{severity_badge}</div>'
        + _divider()
        + _detail_table([
            ('Region',      region),
            ('Coordinates', coords),
            ('Area',        area),
            ('Confidence',  conf),
        ])
        + _notes_block(notes)
        + _alert_box('Please visit the site and submit your field verification report promptly.', 'info')
        + _cta_button(_site_url('/dashboard/inspector/'), 'Open My Dashboard &rarr;')
    )

    plain = (
        f"Hello {inspector_name},\n\nYou have a new field assignment.\n\n"
        f"Severity: {severity}\nRegion: {region}\nCoordinates: {coords}\n"
        f"Area: {area}  Confidence: {conf}\n\n"
        f"Instructions: {notes}\n\n"
        f"Dashboard: {_site_url('/dashboard/inspector/')}"
    )

    _send(subject, plain, _wrap(body, f'New {severity} field assignment in {region}'), [recipient], tag='new_assignment')


# ─────────────────────────────────────────────
# 5. Assignment reminder (Inspector)
# ─────────────────────────────────────────────

def send_assignment_reminder(assignment, alert, days_pending):
    recipient = _inspector_recipient(assignment.inspector)
    if not recipient:
        return

    inspector_name = (
        assignment.inspector.user.get_full_name()
        or assignment.inspector.user.username
    )

    site     = getattr(alert, 'detected_site', None) if alert else None
    severity = alert.get_severity_display() if alert else '—'
    region   = site.region.name if site and site.region else '—'
    assigned = assignment.assigned_at.strftime('%d %b %Y') if assignment.assigned_at else '—'

    subject = f'Reminder: Field Report Pending ({days_pending} days) | {APP_NAME}'

    body = (
        f'<p style="margin:0 0 4px;font-size:15px;color:#374151;">Hello <strong>{inspector_name}</strong>,</p>'
        + '<p style="margin:0 0 24px;font-size:14px;color:#6b7280;">This is a reminder that you have a pending field assignment that has not yet been completed.</p>'
        + _alert_box(
            f'This assignment has been pending for <strong>{days_pending} day{"s" if days_pending != 1 else ""}</strong>. '
            f'Please submit your field report at your earliest convenience.',
            'warning'
        )
        + _detail_table([
            ('Assigned on',     assigned),
            ('Days pending',    str(days_pending)),
            ('Alert severity',  severity),
            ('Region',          region),
        ])
        + _cta_button(_site_url('/dashboard/inspector/'), 'Submit Field Report &rarr;')
    )

    plain = (
        f"Hello {inspector_name},\n\n"
        f"Reminder: you have a pending field assignment ({days_pending} days).\n\n"
        f"Assigned: {assigned}\nSeverity: {severity}\nRegion: {region}\n\n"
        f"Dashboard: {_site_url('/dashboard/inspector/')}"
    )

    _send(subject, plain, _wrap(body, f'Field report pending for {days_pending} days'), [recipient], tag='assignment_reminder')


# ─────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────

def _site_url(path=''):
    base = getattr(settings, 'SITE_URL', 'http://localhost:8000')
    return base.rstrip('/') + path


def _send(subject, plain_body, html_body, recipients, tag=''):
    """Send an HTML email with plain-text fallback. Logs and swallows any exception."""
    if not recipients:
        return
    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=plain_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=recipients,
        )
        msg.attach_alternative(html_body, 'text/html')
        msg.send(fail_silently=False)
        logger.info(f'[Notifications] Sent {tag!r} to {recipients}')
    except Exception as exc:
        logger.error(f'[Notifications] Failed to send {tag!r}: {exc}')
