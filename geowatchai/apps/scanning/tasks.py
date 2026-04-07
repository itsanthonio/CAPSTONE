"""
Celery tasks for the automated scanning system.

auto_scan_tick — runs every 5 minutes via Celery beat.
Checks the scanning window, picks the next batch of due tiles,
and fires the detection pipeline for each one.
"""

import logging
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


def _get_site_region(site):
    """Return the Ghana region name for a site via spatial lookup against admin_district records."""
    if not site or not getattr(site, 'centroid', None):
        return 'Unknown region'
    try:
        from apps.detections.models import Region
        dist_obj = Region.objects.filter(
            geometry__contains=site.centroid,
            is_active=True,
            region_type='admin_district',
        ).values('district').first()
        return (dist_obj['district'] or 'Unknown region') if dist_obj else 'Unknown region'
    except Exception:
        return 'Unknown region'


# Tiered scanning strategy:
#   Tier 1 — never-scanned tiles (any priority) — always first
#   Tier 2 — hotspot tiles not scanned in the last HOTSPOT_COOLDOWN_HOURS hours
#   Tier 3 — normal tiles not scanned in the last NORMAL_COOLDOWN_DAYS days
# Max TILES_PER_TICK tiles queued per 5-minute tick.
# Max MAX_IN_FLIGHT automated jobs running at once — skip tick if exceeded.
TILES_PER_TICK          = 3
MAX_IN_FLIGHT           = 6
HOTSPOT_COOLDOWN_HOURS  = 20
NORMAL_COOLDOWN_DAYS    = 7


@shared_task
def auto_scan_tick():
    """
    Called every 5 minutes by Celery beat.
    Checks scanning window and GEE rate-limit, then queues the next
    batch of tiles using the tiered cooldown strategy.
    """
    from datetime import timedelta
    from .models import AutoScanConfig, ScanTile
    from apps.jobs.services import JobService
    from apps.core.tasks import run_detection_task

    config = AutoScanConfig.get()

    # Reset daily counter if it's a new day
    config.reset_daily_counter_if_needed()

    # Guard: system paused
    if not config.is_enabled:
        logger.debug('auto_scan_tick: system paused, skipping.')
        return {'skipped': 'paused'}

    # Guard: outside scanning window
    if not config.is_within_window():
        logger.debug('auto_scan_tick: outside scanning window, skipping.')
        return {'skipped': 'outside_window'}

    # Guard: already rate-limited by GEE today
    if config.is_rate_limited_today():
        logger.info('auto_scan_tick: rate-limited today, skipping.')
        return {'skipped': 'rate_limited'}

    now            = timezone.now()
    today          = now.date()
    hotspot_cutoff = now - timedelta(hours=HOTSPOT_COOLDOWN_HOURS)
    normal_cutoff  = now - timedelta(days=NORMAL_COOLDOWN_DAYS)
    end_date       = today
    start_date     = today.replace(year=today.year - 2)

    # Guard: too many jobs already in flight — wait for them to drain
    from apps.jobs.models import Job as _Job
    in_flight_statuses = [
        'queued', 'validating', 'exporting',
        'preprocessing', 'inferring', 'postprocessing', 'storing',
    ]
    in_flight = _Job.objects.filter(source='automated', status__in=in_flight_statuses).count()
    if in_flight >= MAX_IN_FLIGHT:
        logger.info(f'auto_scan_tick: {in_flight} jobs in flight (max {MAX_IN_FLIGHT}), waiting.')
        return {'skipped': 'max_in_flight', 'in_flight': in_flight}

    queued = 0
    failed = 0

    # ── Tier 1: never-scanned tiles (highest priority — fills new Ghana coverage) ──
    tier1 = list(
        ScanTile.objects
        .filter(is_active=True, last_scanned_at__isnull=True)
        .order_by('priority', 'id')   # hotspot before normal; consistent order
        [:TILES_PER_TICK]
    )
    slots_left = TILES_PER_TICK - len(tier1)

    # ── Tier 2: hotspot tiles overdue for daily re-check ──
    tier2 = []
    if slots_left > 0:
        tier2 = list(
            ScanTile.objects
            .filter(
                is_active=True,
                priority=ScanTile.Priority.HOTSPOT,
                last_scanned_at__lt=hotspot_cutoff,
            )
            .order_by('last_scanned_at')  # oldest first
            [:slots_left]
        )
        slots_left -= len(tier2)

    # ── Tier 3: normal tiles overdue for weekly sweep ──
    tier3 = []
    if slots_left > 0:
        tier3 = list(
            ScanTile.objects
            .filter(
                is_active=True,
                priority=ScanTile.Priority.NORMAL,
                last_scanned_at__lt=normal_cutoff,
            )
            .order_by('last_scanned_at')  # oldest first
            [:slots_left]
        )

    tiles_to_scan = tier1 + tier2 + tier3

    if not tiles_to_scan:
        logger.info('auto_scan_tick: all tiles within cooldown, skipping tick.')
        return {'skipped': 'all_fresh'}

    logger.info(
        f'auto_scan_tick: tier1={len(tier1)} unscanned, '
        f'tier2={len(tier2)} hotspot-due, tier3={len(tier3)} normal-due'
    )

    for tile in tiles_to_scan:
        try:
            job = JobService.create_job(
                aoi_geometry=tile.geometry,
                start_date=str(start_date),
                end_date=str(end_date),
                source='automated',
                scan_tile_id=str(tile.id),
            )

            # Queue as priority=5 (automated) on the priority queue
            run_detection_task.apply_async(
                args=[str(job.id)],
                queue='priority',
                priority=5,
            )

            # Mark tile as queued
            tile.last_scanned_at = timezone.now()
            tile.scan_count += 1
            tile.save(update_fields=['last_scanned_at', 'scan_count'])

            queued += 1
            logger.info(f'auto_scan_tick: queued tile {tile.name} → job {job.id}')

        except Exception as exc:
            failed += 1
            error_msg = str(exc)

            # Detect GEE rate limit error
            if 'quota' in error_msg.lower() or '429' in error_msg or 'rate' in error_msg.lower():
                logger.warning(f'auto_scan_tick: GEE rate limit hit — stopping for today.')
                config.rate_limited_date = today
                config.save(update_fields=['rate_limited_date'])
                break

            logger.error(f'auto_scan_tick: failed to queue tile {tile.name}: {exc}')

    # Update daily counter
    config.tiles_scanned_today += queued
    config.save(update_fields=['tiles_scanned_today'])

    logger.info(f'auto_scan_tick: queued={queued}, failed={failed}')
    return {'queued': queued, 'failed': failed}


@shared_task
def automated_scan_daily_digest():
    """
    Runs once daily at 18:00 (end of scanning window).
    Collects all automated detections from today and emails a summary
    to OPS_EMAILS. Manual scan alerts are NOT included — those are
    handled inline when the user reviews their scan result.
    """
    from django.utils import timezone
    from django.conf import settings
    from django.core.mail import send_mail
    from apps.detections.models import Alert

    ops_emails = getattr(settings, 'OPS_EMAILS', [])
    if not ops_emails:
        logger.info('daily_digest: no OPS_EMAILS configured, skipping.')
        return {'skipped': 'no_recipients'}

    today = timezone.now().date()
    site_url = getattr(settings, 'SITE_URL', 'http://localhost:8000')

    # Automated alerts created today (exclude dismissed/resolved — not actionable)
    alerts = (
        Alert.objects
        .filter(
            created_at__date=today,
            detected_site__job__source='automated',
        )
        .exclude(status__in=[Alert.AlertStatus.DISMISSED, Alert.AlertStatus.RESOLVED])
        .select_related('detected_site', 'detected_site__region')
        .order_by('-severity', '-created_at')
    )

    total = alerts.count()
    if total == 0:
        logger.info('daily_digest: no automated detections today, skipping email.')
        return {'sent': False, 'reason': 'no_detections'}

    # Severity breakdown
    severity_counts = {}
    for a in alerts:
        sev = a.get_severity_display()
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    # Build plain-text body
    breakdown_lines = '\n'.join(
        f'  {sev}: {cnt}' for sev, cnt in severity_counts.items()
    )
    detail_lines = '\n'.join(
        f"  - {a.title} | {a.detected_site.area_hectares:.1f} ha | "
        f"{_get_site_region(a.detected_site)} | "
        f"{a.get_severity_display()}"
        for a in alerts[:30]  # cap at 30 in the email body
    )
    suffix = f'\n  ... and {total - 30} more.' if total > 30 else ''

    body = (
        f"SankofaWatch — Automated Scan Daily Digest\n"
        f"Date: {today.strftime('%B %d, %Y')}\n"
        f"{'=' * 52}\n\n"
        f"Total detections today: {total}\n\n"
        f"Severity breakdown:\n{breakdown_lines}\n\n"
        f"Detections:\n{detail_lines}{suffix}\n\n"
        f"View all alerts: {site_url}/dashboard/alerts/\n"
        f"View auto-scan map: {site_url}/scanning/auto-scan/\n\n"
        f"— SankofaWatch"
    )

    try:
        send_mail(
            subject=f"[SankofaWatch] Daily Digest — {total} automated detection(s) on {today}",
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=ops_emails,
            fail_silently=False,
        )
        logger.info(f'daily_digest: sent digest ({total} alerts) to {ops_emails}')
        return {'sent': True, 'alerts': total}
    except Exception as exc:
        logger.error(f'daily_digest: failed to send email: {exc}')
        return {'sent': False, 'error': str(exc)}
