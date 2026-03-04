"""
Management command: seed_ghana_grid

Generates a grid of ScanTile records covering Ghana's bounding box.
Tiles that intersect known mining hotspot regions are marked as 'hotspot'.
All others are marked 'normal'.

Usage:
    python manage.py seed_ghana_grid
    python manage.py seed_ghana_grid --clear          # wipe existing tiles first
    python manage.py seed_ghana_grid --tile-size 0.1  # larger tiles (~11km)
"""

from django.core.management.base import BaseCommand
from django.contrib.gis.geos import Polygon

# Ghana approximate bounding box
GHANA_BOUNDS = {
    'min_lon': -3.5,
    'max_lon':  1.2,
    'min_lat':  4.5,
    'max_lat': 11.2,
}

# Default tile size in degrees.
# 0.07° ≈ 7.77 km — matches the model's 7680m patch size at 30m resolution.
DEFAULT_TILE_SIZE = 0.07

# Known mining hotspot regions — hardcoded from Ghana's galamsey belts.
# Each entry defines a bounding box (min_lon, min_lat, max_lon, max_lat).
# Any generated tile that intersects one of these boxes is tagged 'hotspot'.
HOTSPOT_REGIONS = [
    # ── Western Region ──────────────────────────────────────────────────────
    {
        'name': 'Tarkwa-Prestea Belt',
        # Goldfields Tarkwa (~-1.99,5.30), Prestea (~-2.15,5.43), Wassa Amenfi (~-2.08,5.52)
        'bounds': (-2.40, 4.90, -1.60, 5.80),
    },
    {
        'name': 'Chirano-Bibiani Belt',
        # Kinross Chirano (~-2.63,6.88), Bibiani (~-2.33,6.47)
        'bounds': (-2.90, 6.30, -2.20, 7.10),
    },
    {
        'name': 'Damang Mine Area',
        # Goldfields Damang (~-2.08,5.54)
        'bounds': (-2.30, 5.30, -1.80, 5.80),
    },

    # ── Ashanti Region ──────────────────────────────────────────────────────
    {
        'name': 'Obuasi-Amansie Belt',
        # AngloGold Obuasi (~-1.67,6.20), Amansie West/East (~-1.85,6.12)
        'bounds': (-2.10, 5.80, -1.30, 6.60),
    },
    {
        'name': 'Ahafo Mining Zone',
        # Newmont Ahafo Kenyasi (~-2.34,7.00)
        'bounds': (-2.70, 6.70, -2.00, 7.40),
    },
    {
        'name': 'Dunkwa-Offin River',
        # Dunkwa-on-Offin (~-1.78,5.97) — major illegal mining corridor
        'bounds': (-2.10, 5.70, -1.50, 6.20),
    },

    # ── Eastern Region ──────────────────────────────────────────────────────
    {
        'name': 'Birim-Oda Valley',
        # Birim Valley (~-0.98,5.92), Oda area
        'bounds': (-1.30, 5.60, -0.60, 6.30),
    },
    {
        'name': 'Atiwa-Birim Belt',
        # Atiwa Forest (illegal mining pressure), Birim North
        'bounds': (-0.90, 6.00, -0.20, 6.70),
    },

    # ── Central Region ───────────────────────────────────────────────────────
    {
        'name': 'Upper Denkyira',
        'bounds': (-1.80, 5.50, -1.20, 6.00),
    },
]


class Command(BaseCommand):
    help = 'Generate Ghana grid tiles and seed hotspot areas'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Delete all existing ScanTile records before generating',
        )
        parser.add_argument(
            '--tile-size',
            type=float,
            default=DEFAULT_TILE_SIZE,
            dest='tile_size',
            help=f'Tile size in degrees (default: {DEFAULT_TILE_SIZE} ≈ 7.77km)',
        )

    def handle(self, *args, **options):
        from apps.scanning.models import ScanTile

        if options['clear']:
            count = ScanTile.objects.count()
            ScanTile.objects.all().delete()
            self.stdout.write(self.style.WARNING(f'Deleted {count} existing tiles.'))

        tile_size = options['tile_size']
        self.stdout.write(f'Generating Ghana grid (tile size: {tile_size}°)...')

        # Pre-build hotspot Polygon objects for intersection checks
        hotspot_polys = []
        for region in HOTSPOT_REGIONS:
            min_lon, min_lat, max_lon, max_lat = region['bounds']
            poly = Polygon.from_bbox((min_lon, min_lat, max_lon, max_lat))
            poly.srid = 4326
            hotspot_polys.append(poly)

        tiles        = []
        tile_count   = 0
        hotspot_count = 0

        lon = GHANA_BOUNDS['min_lon']
        while lon < GHANA_BOUNDS['max_lon']:
            lat = GHANA_BOUNDS['min_lat']
            while lat < GHANA_BOUNDS['max_lat']:
                tile_poly = Polygon.from_bbox((
                    lon,
                    lat,
                    min(lon + tile_size, GHANA_BOUNDS['max_lon']),
                    min(lat + tile_size, GHANA_BOUNDS['max_lat']),
                ))
                tile_poly.srid = 4326

                # Tag as hotspot if the tile intersects any hotspot polygon
                priority = ScanTile.Priority.NORMAL
                for hp in hotspot_polys:
                    if tile_poly.intersects(hp):
                        priority = ScanTile.Priority.HOTSPOT
                        hotspot_count += 1
                        break

                tiles.append(ScanTile(
                    name=f'GH_{lon:.3f}_{lat:.3f}',
                    geometry=tile_poly,
                    priority=priority,
                    is_active=True,
                ))
                tile_count += 1
                lat = round(lat + tile_size, 6)
            lon = round(lon + tile_size, 6)

        # Bulk insert in batches of 500
        ScanTile.objects.bulk_create(tiles, batch_size=500)

        normal_count = tile_count - hotspot_count
        self.stdout.write(self.style.SUCCESS(
            f'Done. Created {tile_count} tiles: '
            f'{hotspot_count} hotspot, {normal_count} normal.'
        ))
