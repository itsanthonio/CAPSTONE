"""
Import Ghana administrative district boundaries from a GeoJSON file.

Replaces all existing admin_district Region records. Uses the Region_19
field for the parent Ghana region name (2019 administrative boundaries).

Usage:
    python manage.py import_districts
    python manage.py import_districts path/to/Ghana_District.geojson
    python manage.py import_districts --dry-run
"""

import json
import string
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


DEFAULT_GEOJSON = Path(__file__).resolve().parents[5] / 'ghana_admin_boundaries' / 'Ghana_District.geojson'


def _title(s):
    return string.capwords(s.strip().lower())


class Command(BaseCommand):
    help = 'Import Ghana district boundaries from GeoJSON, replacing existing admin_district records.'

    def add_arguments(self, parser):
        parser.add_argument(
            'geojson',
            nargs='?',
            default=str(DEFAULT_GEOJSON),
            help=f'Path to GeoJSON file (default: {DEFAULT_GEOJSON})',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Validate and report without writing to the database.',
        )

    def handle(self, *args, **options):
        from apps.detections.models import Region
        from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon

        path = Path(options['geojson'])
        if not path.exists():
            raise CommandError(f'File not found: {path}')

        self.stdout.write(f'Reading {path} …')
        with open(path, encoding='utf-8') as f:
            geojson = json.load(f)

        features = geojson.get('features', [])
        if not features:
            raise CommandError('GeoJSON has no features.')

        self.stdout.write(f'Found {len(features)} features.')

        dry_run = options['dry_run']
        imported = skipped = 0
        seen_names = set()

        rows = []
        for feat in features:
            props = feat.get('properties') or {}
            raw_geom = feat.get('geometry')
            if not raw_geom:
                skipped += 1
                continue

            raw_name = props.get('DISTRICT') or props.get('name') or props.get('NAME') or ''
            # Always prefer Region_19 (2019 boundaries) over the old REGION field
            raw_region = props.get('Region_19') or props.get('REGION') or props.get('region') or ''

            if not raw_name.strip():
                skipped += 1
                continue

            name   = _title(raw_name)
            parent = _title(raw_region)

            # Deduplicate names within this import
            base_name, counter = name, 1
            while name in seen_names:
                name = f'{base_name} ({counter})'
                counter += 1
            seen_names.add(name)

            try:
                geos = GEOSGeometry(json.dumps(raw_geom), srid=4326)
                if isinstance(geos, Polygon):
                    geos = MultiPolygon(geos)
                elif not isinstance(geos, MultiPolygon):
                    skipped += 1
                    continue
            except Exception:
                skipped += 1
                continue

            rows.append((name, parent, geos))
            imported += 1

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'Dry run — would import {imported} districts, skip {skipped}.'
            ))
            # Show a sample of parent region values found
            region_values = sorted({r[1] for r in rows})
            self.stdout.write('Parent region values found:')
            for v in region_values:
                self.stdout.write(f'  {v}')
            return

        with transaction.atomic():
            deleted, _ = Region.objects.filter(region_type='admin_district').delete()
            self.stdout.write(f'Deleted {deleted} existing admin_district records.')

            for name, parent, geom in rows:
                Region.objects.create(
                    name=name,
                    region_type='admin_district',
                    district=parent,
                    geometry=geom,
                    is_active=True,
                )

        self.stdout.write(self.style.SUCCESS(
            f'Done — imported {imported} districts, skipped {skipped}.'
        ))
