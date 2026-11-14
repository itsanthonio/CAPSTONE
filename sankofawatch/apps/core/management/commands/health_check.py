"""Run the system health checks and email OPS_EMAILS when something is wrong.

Intended for cron (every 10 minutes). The same problem is only emailed again after
`--repeat-hours` (default 6); a single "recovered" email is sent when everything is
healthy again. State is kept in a small JSON file so it works even when Redis is down.

    python manage.py health_check            # check + email if needed
    python manage.py health_check --print    # just print the report, send nothing
"""
import json
import logging
import time
from pathlib import Path

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand

from apps.core.health import OK, collect_health

logger = logging.getLogger(__name__)
STATE_FILE = Path(settings.BASE_DIR) / '.health_state.json'


def _load():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _save(state):
    try:
        STATE_FILE.write_text(json.dumps(state))
    except OSError as exc:
        logger.warning('health_check: could not save state: %s', exc)


class Command(BaseCommand):
    help = 'Check system health and email OPS_EMAILS about problems.'

    def add_arguments(self, parser):
        parser.add_argument('--print', action='store_true', help='Print the report only; send no email.')
        parser.add_argument('--repeat-hours', type=float, default=6.0)

    def handle(self, *args, **opts):
        report = collect_health()
        for c in report['checks']:
            self.stdout.write(f"[{c['level'].upper():4}] {c['label']}: {c['detail']}")
        if opts['print']:
            return

        recipients = getattr(settings, 'OPS_EMAILS', [])
        if not recipients:
            return
        state, now = _load(), time.time()
        known = state.get('issues', {})  # code -> last emailed (epoch)
        current = {c['code']: c for c in report['issues']}

        # Debounce: a warning must show up in 2 checks in a row (e.g. workers still booting
        # after a deploy is not an incident). Critical problems are reported immediately.
        streak = {code: state.get('streak', {}).get(code, 0) + 1 for code in current}
        state['streak'] = streak
        due = [c for code, c in current.items()
               if (c['level'] == 'crit' or streak[code] >= 2)
               and now - known.get(code, 0) >= opts['repeat_hours'] * 3600]
        if due:
            lines = [f"- [{c['level'].upper()}] {c['label']}: {c['detail']}" for c in report['issues']]
            body = ("SankofaWatch health check found problems:\n\n" + "\n".join(lines) +
                    f"\n\nSee {settings.SITE_URL.rstrip('/')}/dashboard/system-health/ for the live view.")
            worst = 'CRITICAL' if any(c['level'] == 'crit' for c in report['issues']) else 'WARNING'
            try:
                send_mail(f'[SankofaWatch] {worst}: {len(report["issues"])} health issue(s)', body,
                          settings.DEFAULT_FROM_EMAIL, recipients, fail_silently=False)
                for c in due:
                    known[c['code']] = now
            except Exception as exc:
                logger.error('health_check: could not send alert email: %s', exc)
        elif not current and known:
            try:
                send_mail('[SankofaWatch] Recovered: all health checks passing',
                          'All SankofaWatch health checks are passing again.',
                          settings.DEFAULT_FROM_EMAIL, recipients, fail_silently=False)
                known = {}
            except Exception as exc:
                logger.error('health_check: could not send recovery email: %s', exc)
        # forget issues that have cleared so a repeat later alerts immediately
        state['issues'] = {k: v for k, v in known.items() if k in current}
        _save(state)
