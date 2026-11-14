"""System health checks, shared by the admin System Health page and the
`health_check` management command (run from cron, emails OPS_EMAILS).

Every check is wrapped so that a failing check is *reported* and never raises:
a broken dependency must not take the health page or the cron job down.
"""
import logging
import shutil
from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.utils import timezone

logger = logging.getLogger(__name__)

OK, WARN, CRIT = 'ok', 'warn', 'crit'

DISK_WARN_PCT, DISK_CRIT_PCT = 85, 92
MEM_WARN_MB, MEM_CRIT_MB = 1024, 512
QUEUE_WARN = {'priority': 50, 'automated': 200, 'background': 500}


def _check(code, label, level, detail, **extra):
    return {'code': code, 'label': label, 'level': level, 'detail': detail, **extra}


def _disk():
    try:
        u = shutil.disk_usage(str(settings.BASE_DIR))
        pct = round(u.used / u.total * 100)
        free_gb = u.free / 1e9
        level = CRIT if pct >= DISK_CRIT_PCT else WARN if pct >= DISK_WARN_PCT else OK
        return _check('disk', 'Disk space', level, f'{pct}% used, {free_gb:.1f} GB free', value=pct)
    except Exception as exc:
        return _check('disk', 'Disk space', WARN, f'could not read disk usage: {exc}')


def _memory():
    """Linux only (/proc/meminfo). Returns None elsewhere so the check is skipped."""
    try:
        info = {}
        with open('/proc/meminfo') as f:
            for line in f:
                k, v = line.split(':', 1)
                info[k] = int(v.split()[0])  # kB
        avail_mb = info.get('MemAvailable', 0) // 1024
        swap_total = info.get('SwapTotal', 0) // 1024
        swap_free = info.get('SwapFree', 0) // 1024
        level = CRIT if avail_mb < MEM_CRIT_MB else WARN if avail_mb < MEM_WARN_MB else OK
        detail = f'{avail_mb} MB available'
        if swap_total:
            detail += f', swap {swap_total - swap_free}/{swap_total} MB used'
        return _check('memory', 'Memory', level, detail, value=avail_mb)
    except (OSError, ValueError):
        return None
    except Exception as exc:
        return _check('memory', 'Memory', WARN, f'could not read memory: {exc}')


def _database():
    try:
        with connection.cursor() as cur:
            cur.execute('SELECT 1')
        return _check('database', 'Database', OK, 'reachable')
    except Exception as exc:
        return _check('database', 'Database', CRIT, f'unreachable: {exc}')


def _redis_client():
    import redis
    return redis.Redis.from_url(settings.CELERY_BROKER_URL, socket_connect_timeout=3, socket_timeout=3)


def _redis_and_queues():
    out = []
    try:
        r = _redis_client()
        r.ping()
        out.append(_check('redis', 'Redis', OK, 'reachable'))
    except Exception as exc:
        return [_check('redis', 'Redis', CRIT, f'unreachable: {exc}')]
    depths = {}
    for q, warn_at in QUEUE_WARN.items():
        try:
            depths[q] = int(r.llen(q))
        except Exception:
            depths[q] = None
    parts = [f'{q}: {n}' for q, n in depths.items() if n is not None]
    worst = OK
    for q, n in depths.items():
        if n is not None and n >= QUEUE_WARN[q]:
            worst = WARN
    out.append(_check('queues', 'Task queues', worst, ', '.join(parts) or 'no data', value=depths))
    return out


def _workers():
    expected = int(getattr(settings, 'HEALTH_EXPECTED_WORKERS', 0) or 0)
    try:
        from config.celery import app as celery_app
        replies = celery_app.control.inspect(timeout=3).ping() or {}
        online = len(replies)
    except Exception as exc:
        return _check('workers', 'Celery workers', CRIT, f'could not query workers: {exc}')
    if expected and online < expected:
        level = CRIT if online == 0 else WARN
        return _check('workers', 'Celery workers', level, f'{online} of {expected} online', value=online)
    return _check('workers', 'Celery workers', OK if online else WARN,
                  f'{online} online' + (f' of {expected}' if expected else ''), value=online)


def _scans():
    """Scan throughput and failure rate over the last hour, plus auto-scanner state."""
    out = []
    try:
        from apps.jobs.models import Job
        since = timezone.now() - timedelta(hours=1)
        recent = Job.objects.filter(created_at__gte=since)
        total = recent.count()
        failed = recent.filter(status='failed').count()
        level = WARN if (total >= 5 and failed / total >= 0.5) else OK
        out.append(_check('scans', 'Scans (last hour)', level,
                          f'{total} started, {failed} failed', value={'total': total, 'failed': failed}))
    except Exception as exc:
        out.append(_check('scans', 'Scans (last hour)', WARN, f'could not read jobs: {exc}'))
        return out
    try:
        from apps.scanning.models import AutoScanConfig
        cfg = AutoScanConfig.get()
        if not cfg.is_enabled:
            out.append(_check('autoscan', 'Auto scanner', OK, 'paused'))
        elif cfg.is_rate_limited_today():
            out.append(_check('autoscan', 'Auto scanner', WARN, 'stopped for today: Earth Engine rate limit'))
        elif not cfg.is_within_window():
            out.append(_check('autoscan', 'Auto scanner', OK, 'outside scanning hours'))
        else:
            last = (Job.objects.filter(source='automated').order_by('-created_at')
                    .values_list('created_at', flat=True).first())
            idle_min = int((timezone.now() - last).total_seconds() // 60) if last else None
            if last is None or idle_min > 45:
                out.append(_check('autoscan', 'Auto scanner', WARN,
                                  'enabled but no scan queued in the last 45 minutes (is beat running?)'))
            else:
                out.append(_check('autoscan', 'Auto scanner', OK, f'running, last scan queued {idle_min} min ago'))
    except Exception as exc:
        out.append(_check('autoscan', 'Auto scanner', WARN, f'could not read scanner state: {exc}'))
    return out


def collect_health(include_workers=True):
    """Run every check. Returns {'checks': [...], 'issues': [...], 'overall': 'ok|warn|crit', 'at': datetime}."""
    checks = [_database()]
    checks.extend(_redis_and_queues())
    if include_workers:
        checks.append(_workers())
    for c in (_disk(), _memory()):
        if c:
            checks.append(c)
    checks.extend(_scans())
    issues = [c for c in checks if c['level'] != OK]
    overall = CRIT if any(c['level'] == CRIT for c in checks) else WARN if issues else OK
    return {'checks': checks, 'issues': issues, 'overall': overall, 'at': timezone.now()}
