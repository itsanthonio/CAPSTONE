"""
Import legal concessions from a GeoJSON file into the LegalConcession table.

Usage:
    python manage.py import_concessions path/to/legal_concessions.geojson

Options:
    --clear     Delete all existing concessions before importing
    --dry-run   Validate and report without writing to the database
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon


class Command(BaseCommand):
    help = 'Import legal mining concessions from a GeoJSON file'

    def add_arguments(self, parser):
        parser.add_argument(
            'geojson_path',
            type=str,
            help='Path to the GeoJSON file'
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Delete all existing concessions before importing'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Validate without writing to the database'
        )

    def handle(self, *args, **options):
        from apps.detections.models import LegalConcession

        path = Path(options['geojson_path'])
        if not path.exists():
            raise CommandError(f"File not found: {path}")

        self.stdout.write(f"Reading {path} ...")

        with open(path, encoding='utf-8') as f:
            data = json.load(f)

        if data.get('type') != 'FeatureCollection':
            raise CommandError("GeoJSON must be a FeatureCollection")

        features = data.get('features', [])
        self.stdout.write(f"Found {len(features)} features")

        if options['clear'] and not options['dry_run']:
            deleted, _ = LegalConcession.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Deleted {deleted} existing concessions"))

        created = 0
        updated = 0
        skipped = 0

        for i, feature in enumerate(features):
            props = feature.get('properties', {})
            geom_dict = feature.get('geometry')

            if not geom_dict:
                self.stdout.write(self.style.WARNING(f"  Feature {i}: no geometry, skipping"))
                skipped += 1
                continue

            # Parse geometry
            try:
                geom = GEOSGeometry(json.dumps(geom_dict), srid=4326)
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"  Feature {i}: invalid geometry ({e}), skipping"))
                skipped += 1
                continue

            # Ensure MultiPolygon
            if isinstance(geom, Polygon):
                geom = MultiPolygon(geom, srid=4326)
            elif not isinstance(geom, MultiPolygon):
                self.stdout.write(self.style.WARNING(f"  Feature {i}: unsupported geometry type {geom.geom_type}, skipping"))
                skipped += 1
                continue

            # Extract fields from properties
            # The GeoJSON has 'name' (license number) and 'description'
            license_number = (props.get('name') or '').strip()
            description = (props.get('description') or '').strip()

            if not license_number:
                # Fall back to a generated identifier
                license_number = f"UNKNOWN_{i:04d}"

            # Parse license type from license number prefix
            # Common Ghana formats: ML = Mining Lease, SML = Small Mining Lease,
            # RL = Reconnaissance License, EL = Exploration License
            license_type = _parse_license_type(license_number)

            if options['dry_run']:
                self.stdout.write(f"  [dry-run] {license_number} — {license_type}")
                created += 1
                continue

            obj, was_created = LegalConcession.objects.update_or_create(
                license_number=license_number,
                defaults={
                    'concession_name': description or license_number,
                    'holder_name': description or 'Unknown',
                    'license_type': license_type,
                    'geometry': geom,
                    'is_active': True,
                    'data_source': LegalConcession.DataSource.MINERALS_COMMISSION,
                }
            )

            if was_created:
                created += 1
            else:
                updated += 1

        # Summary
        self.stdout.write("")
        if options['dry_run']:
            self.stdout.write(self.style.SUCCESS(
                f"[dry-run] Would import {created} concessions, skip {skipped}"
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Done — created: {created}, updated: {updated}, skipped: {skipped}"
            ))
            self.stdout.write(
                f"Total in database: {LegalConcession.objects.count()}"
            )


def _parse_license_type(license_number: str) -> str:
    """
    Infer license type from Ghana license number prefix.
    Examples: ML6/2, SML4/20, RL3/26, EL1/10
    """
    from apps.detections.models import LegalConcession

    upper = license_number.upper()
    if upper.startswith('SML'):
        return LegalConcession.LicenseType.SMALL_SCALE
    elif upper.startswith('ML'):
        return LegalConcession.LicenseType.LARGE_SCALE
    elif upper.startswith('RL'):
        return LegalConcession.LicenseType.RECONNAISSANCE
    elif upper.startswith('EL'):
        return LegalConcession.LicenseType.EXPLORATION
    else:
        return LegalConcession.LicenseType.SMALL_SCALE