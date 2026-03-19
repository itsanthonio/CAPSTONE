"""
Management command: import_ghana_places

Downloads the GeoNames Ghana dump (GH.zip) and imports all populated places
into the GhanaPlace table. Safe to re-run — clears old geonames rows first.

Usage:
    python manage.py import_ghana_places
"""

import io
import zipfile
import requests
from django.core.management.base import BaseCommand
from apps.scanning.models import GhanaPlace

GEONAMES_URL = 'https://download.geonames.org/export/dump/GH.zip'

# GeoNames admin1 codes → region names for Ghana
ADMIN1_NAMES = {
    '01': 'Western Region',
    '02': 'Central Region',
    '03': 'Greater Accra Region',
    '04': 'Volta Region',
    '05': 'Eastern Region',
    '06': 'Ashanti Region',
    '07': 'Brong-Ahafo Region',
    '08': 'Northern Region',
    '09': 'Upper East Region',
    '10': 'Upper West Region',
    '11': 'Western North Region',
    '12': 'Ahafo Region',
    '13': 'Bono East Region',
    '14': 'Oti Region',
    '15': 'Savannah Region',
    '16': 'North East Region',
}


class Command(BaseCommand):
    help = 'Import Ghana place names from GeoNames bulk data into the local database'

    def handle(self, *args, **options):
        self.stdout.write('Downloading GeoNames Ghana data…')
        try:
            resp = requests.get(GEONAMES_URL, timeout=60)
            resp.raise_for_status()
        except Exception as e:
            self.stderr.write(f'Download failed: {e}')
            return

        self.stdout.write('Parsing…')
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            with z.open('GH.txt') as f:
                lines = f.read().decode('utf-8').splitlines()

        # Clear existing GeoNames rows (keep any cached Google results)
        deleted, _ = GhanaPlace.objects.filter(source='geonames').delete()
        self.stdout.write(f'Cleared {deleted} existing geonames records')

        places = []
        skipped = 0
        for line in lines:
            cols = line.split('\t')
            if len(cols) < 15:
                continue
            feature_class = cols[6]   # P = populated place, A = admin area, etc.
            feature_code  = cols[7]
            # Keep populated places + administrative areas
            if feature_class not in ('P', 'A'):
                skipped += 1
                continue
            try:
                lat = float(cols[4])
                lon = float(cols[5])
            except ValueError:
                continue

            admin1_code = cols[10]
            region = ADMIN1_NAMES.get(admin1_code, '')

            places.append(GhanaPlace(
                name         = cols[1],
                ascii_name   = cols[2],
                latitude     = lat,
                longitude    = lon,
                feature_code = feature_code,
                population   = int(cols[14]) if cols[14].isdigit() else 0,
                region       = region,
                source       = 'geonames',
            ))

        self.stdout.write(f'Importing {len(places)} places (skipped {skipped} non-place features)…')
        GhanaPlace.objects.bulk_create(places, batch_size=500)
        self.stdout.write(self.style.SUCCESS(
            f'Done — {len(places)} Ghana places now in the local database.'
        ))
