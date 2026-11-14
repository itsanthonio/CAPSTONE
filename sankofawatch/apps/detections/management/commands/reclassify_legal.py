"""Re-judge existing detected sites with the current legal rule (>= 80% of the site's
area inside active concessions = legal).

    python manage.py reclassify_legal            # dry run: only report what would change
    python manage.py reclassify_legal --apply    # save the changes and raise alerts for
                                                 # sites that turn out to be illegal
Sites that flip from illegal to legal get their open alerts left alone (an inspector may
already be working on them); only legal -> illegal changes create new alerts.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.detections.legal import LEGAL_MIN_OVERLAP_PCT, judge_site
from apps.detections.models import DetectedSite, LegalConcession


class Command(BaseCommand):
    help = 'Re-judge detected sites by how much of their area lies inside legal concessions.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Save changes (default is a dry run).')

    def handle(self, *args, **opts):
        concessions = LegalConcession.objects.filter(is_active=True)
        to_illegal, to_legal, overlap_updates, errors = [], [], 0, 0
        sites = DetectedSite.objects.exclude(geometry__isnull=True)
        total = sites.count()
        self.stdout.write(f'Checking {total} sites against {concessions.count()} active concessions '
                          f'(legal = at least {LEGAL_MIN_OVERLAP_PCT:.0f}% inside)...')

        for site in sites.iterator(chunk_size=200):
            try:
                is_legal, best, overlap = judge_site(site, concessions)
            except Exception:
                errors += 1
                continue
            new_status = 'legal' if is_legal else 'illegal'
            changed_status = site.legal_status != new_status
            changed_overlap = abs((site.concession_overlap_pct or 0) - overlap) > 0.5
            if not (changed_status or changed_overlap):
                continue
            if changed_status:
                (to_legal if is_legal else to_illegal).append(site.pk)
            else:
                overlap_updates += 1
            if opts['apply']:
                with transaction.atomic():
                    site.legal_status = new_status
                    site.concession_overlap_pct = overlap
                    site.intersecting_concession = best
                    site.save(update_fields=['legal_status', 'concession_overlap_pct', 'intersecting_concession'])

        self.stdout.write(f'  legal -> illegal: {len(to_illegal)}')
        self.stdout.write(f'  illegal -> legal: {len(to_legal)}')
        self.stdout.write(f'  overlap % refreshed only: {overlap_updates}')
        if errors:
            self.stdout.write(self.style.WARNING(f'  skipped {errors} sites with unreadable geometry'))

        if not opts['apply']:
            self.stdout.write(self.style.WARNING('Dry run: nothing was saved. Re-run with --apply to save.'))
            return

        if to_illegal:
            from apps.core.orchestrator import MiningDetectionPipeline
            MiningDetectionPipeline._generate_alerts(list(DetectedSite.objects.filter(pk__in=to_illegal)))
        self.stdout.write(self.style.SUCCESS('Applied.'))
