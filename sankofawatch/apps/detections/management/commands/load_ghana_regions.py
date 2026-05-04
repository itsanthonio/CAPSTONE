"""
Load all Ghana administrative regions (ADM1) into the Region table.

Fetches boundary GeoJSON from the GeoBoundaries public API and creates/updates
a Region record for each of Ghana's 16 administrative regions.

Usage:
    python manage.py load_ghana_regions
    python manage.py load_ghana_regions --clear    # remove existing regions first
    python manage.py load_ghana_regions --dry-run  # preview without writing
"""

import json
import requests
from django.core.management.base import BaseCommand, CommandError
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon


GEOBOUNDARIES_API = 'https://www.geoboundaries.org/api/current/gbOpen/GHA/ADM1/'


class Command(BaseCommand):
    help = 'Load Ghana ADM1 administrative region boundaries from GeoBoundaries'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Delete all existing Region records before importing',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Parse and report without writing to the database',
        )

    def handle(self, *args, **options):
        from apps.detections.models import Region

        dry_run = options['dry_run']
        clear   = options['clear']

        # ── Step 1: fetch metadata ──────────────────────────────────────────
        self.stdout.write('Fetching metadata from GeoBoundaries API...')
        try:
            meta_resp = requests.get(GEOBOUNDARIES_API, timeout=30)
            meta_resp.raise_for_status()
            meta = meta_resp.json()
        except Exception as exc:
            raise CommandError(f'Failed to fetch GeoBoundaries metadata: {exc}')

        geojson_url = meta.get('gjDownloadURL')
        if not geojson_url:
            raise CommandError(
                f'No gjDownloadURL in API response. Keys: {list(meta.keys())}'
            )

        self.stdout.write(f'GeoJSON URL: {geojson_url}')

        # ── Step 2: download GeoJSON ────────────────────────────────────────
        self.stdout.write('Downloading GeoJSON...')
        try:
            geojson_resp = requests.get(geojson_url, timeout=120)
            geojson_resp.raise_for_status()
            geojson = geojson_resp.json()
        except Exception as exc:
            raise CommandError(f'Failed to download GeoJSON: {exc}')

        features = geojson.get('features', [])
        self.stdout.write(f'Found {len(features)} region features.')

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be written.'))
            for f in features:
                self.stdout.write(f"  Would upsert: {f['properties'].get('shapeName', '?')}")
            return

        # ── Step 3: optionally clear existing regions ───────────────────────
        if clear:
            deleted, _ = Region.objects.all().delete()
            self.stdout.write(self.style.WARNING(f'Cleared {deleted} existing Region records.'))

        # ── Step 4: upsert each region ──────────────────────────────────────
        created = updated = skipped = 0

        for feature in features:
            props = feature.get('properties', {})
            name  = props.get('shapeName') or props.get('NAME_1') or props.get('name')

            if not name:
                self.stdout.write(self.style.WARNING(f'Skipping feature with no name: {props}'))
                skipped += 1
                continue

            try:
                raw_geom = GEOSGeometry(json.dumps(feature['geometry']))
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f'Invalid geometry for {name}: {exc}'))
                skipped += 1
                continue

            # Normalise to MultiPolygon
            if raw_geom.geom_type == 'Polygon':
                raw_geom = MultiPolygon(raw_geom)
            elif raw_geom.geom_type != 'MultiPolygon':
                self.stdout.write(self.style.WARNING(f'Unexpected geometry type for {name}: {raw_geom.geom_type}'))
                skipped += 1
                continue

            obj, was_created = Region.objects.update_or_create(
                name=name,
                defaults={
                    'region_type': Region.RegionType.DISTRICT,
                    'geometry': raw_geom,
                    'district': name,
                    'is_active': True,
                    'notes': 'Ghana ADM1 administrative region (GeoBoundaries)',
                },
            )

            if was_created:
                created += 1
                self.stdout.write(f'  Created: {name}')
            else:
                updated += 1
                self.stdout.write(f'  Updated: {name}')

        # ── Summary ──────────────────────────────────────────────────────────
        self.stdout.write(self.style.SUCCESS(
            f'\nDone — {created} created, {updated} updated, {skipped} skipped.'
        ))
        self.stdout.write(
            f'Total Region records now: {Region.objects.count()}'
        )