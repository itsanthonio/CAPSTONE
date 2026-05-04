"""
Management command to fetch Sentinel-2 timelapse images for DetectedSite records
that don't have them yet (or for a specific site).

Runs synchronously — no Celery workers needed.

Usage:
    # Fetch for all sites missing timelapse frames
    python manage.py fetch_timelapse

    # Fetch for a specific site
    python manage.py fetch_timelapse --site <uuid>

    # Limit how many sites to process in one run
    python manage.py fetch_timelapse --limit 20

    # Re-fetch even if frames already exist (refresh)
    python manage.py fetch_timelapse --force
"""

from django.core.management.base import BaseCommand
from django.db.models import Count


class Command(BaseCommand):
    help = 'Fetch Sentinel-2 timelapse images for detections missing them'

    def add_arguments(self, parser):
        parser.add_argument(
            '--site',
            type=str,
            default=None,
            help='UUID of a specific DetectedSite to fetch timelapse for',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Maximum number of sites to process (default: all)',
        )
        parser.add_argument(
            '--latest',
            type=int,
            default=None,
            metavar='N',
            help='Fetch timelapse for the N most recently detected sites (ignores --site/--limit)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            default=False,
            help='Re-fetch even if timelapse frames already exist',
        )

    def handle(self, *args, **options):
        from apps.detections.models import DetectedSite, SiteTimelapse
        from apps.detections.tasks import fetch_site_timelapse

        site_id = options['site']
        limit    = options['limit']
        force    = options['force']
        latest   = options['latest']

        # ── Build queryset ─────────────────────────────────────────────────
        if latest:
            qs = DetectedSite.objects.order_by('-created_at')[:latest]
            display_total = qs.count()
            self.stdout.write(
                f'Fetching timelapse for the {display_total} most recently detected site(s).\n'
            )
        elif site_id:
            qs = DetectedSite.objects.filter(id=site_id)
            if not qs.exists():
                self.stderr.write(self.style.ERROR(f'Site {site_id} not found'))
                return
            display_total = qs.count()
        else:
            qs = DetectedSite.objects.all().order_by('-detection_date')
            if not force:
                sites_with_frames = (
                    SiteTimelapse.objects
                    .values('detected_site_id')
                    .distinct()
                )
                qs = qs.exclude(id__in=sites_with_frames)
            total = qs.count()
            if limit:
                qs = qs[:limit]
                display_total = min(total, limit)
            else:
                display_total = total

        if display_total == 0:
            self.stdout.write(self.style.SUCCESS('No sites need timelapse fetching.'))
            return

        self.stdout.write(
            f'Fetching timelapse for {display_total} site(s)'
            + (f' (of {total} missing)' if limit and total > limit else '')
            + ' — running synchronously, no workers needed.\n'
        )

        ok = 0
        skipped = 0
        failed = 0

        for i, site in enumerate(qs, 1):
            self.stdout.write(
                f'  [{i}/{display_total}] Site {site.id} '
                f'({site.detection_date}) … ',
                ending='',
            )
            self.stdout.flush()

            try:
                # Call the task function directly (bypasses Celery)
                result = fetch_site_timelapse.run(str(site.id))
                status = result.get('status', '?')
                frames = result.get('frames', 0)

                if status == 'completed':
                    self.stdout.write(self.style.SUCCESS(f'✓ {frames} frame(s)'))
                    ok += 1
                elif status == 'skipped':
                    self.stdout.write(self.style.WARNING(f'skipped ({result.get("reason", "")})'))
                    skipped += 1
                else:
                    self.stdout.write(self.style.ERROR(f'failed: {result.get("error", "unknown")}'))
                    failed += 1

            except Exception as exc:
                self.stdout.write(self.style.ERROR(f'error: {exc}'))
                failed += 1

        self.stdout.write('')
        self.stdout.write(
            self.style.SUCCESS(f'Done — {ok} fetched, {skipped} skipped, {failed} failed')
        )
