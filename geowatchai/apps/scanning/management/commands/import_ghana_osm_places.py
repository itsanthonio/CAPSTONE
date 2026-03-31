"""
Management command: import_ghana_osm_places

Downloads the OpenStreetMap Ghana extract from Geofabrik and imports every
named place / POI into the GhanaPlace table.

Replaces the old GeoNames-based import_ghana_places command. OSM data covers
towns AND specific POIs (universities, malls, hospitals, etc.) so searches for
"Ashesi University" or "West Hills Mall" resolve instantly from the local DB
without any API call.

Requires:
    pip install osmium

Usage:
    python manage.py import_ghana_osm_places
    python manage.py import_ghana_osm_places --pbf /path/to/ghana-latest.osm.pbf
"""

import os
import tempfile
import requests
from django.core.management.base import BaseCommand
from apps.scanning.models import GhanaPlace

GEOFABRIK_URL = 'https://download.geofabrik.de/africa/ghana-latest.osm.pbf'

# OSM place= values → how important they are (higher = more prominent in search)
PLACE_RANK = {
    'city': 100, 'town': 90, 'village': 70, 'suburb': 60,
    'neighbourhood': 50, 'hamlet': 40, 'locality': 30, 'island': 50,
}

# OSM tag categories we want to include
AMENITY_KEEP = {
    'university', 'college', 'school', 'hospital', 'clinic', 'pharmacy',
    'marketplace', 'bank', 'police', 'fire_station', 'prison', 'courthouse',
    'library', 'community_centre', 'arts_centre', 'cinema', 'theatre',
    'place_of_worship', 'fuel', 'bus_station', 'ferry_terminal',
}
TOURISM_KEEP = {
    'hotel', 'hostel', 'guest_house', 'museum', 'attraction',
    'viewpoint', 'zoo', 'theme_park', 'gallery',
}
SHOP_KEEP = {
    'mall', 'supermarket', 'department_store', 'wholesale',
}
LEISURE_KEEP = {
    'stadium', 'park', 'sports_centre', 'golf_course', 'swimming_pool',
}
OFFICE_KEEP = {
    'government', 'embassy', 'ngo', 'company',
}
BUILDING_KEEP = {
    'university', 'college', 'school', 'hospital', 'hotel',
    'government', 'cathedral', 'church', 'mosque', 'stadium',
}
NATURAL_KEEP = {
    'water', 'lake', 'bay', 'cape', 'cliff', 'peak',
}


def _categorize(tags):
    """
    Return (feature_code, population) for an OSM feature, or (None, None) to skip.
    feature_code is a short human-readable type string.
    """
    name = tags.get('name')
    if not name:
        return None, None

    pop = 0
    raw_pop = tags.get('population', '')
    if raw_pop.isdigit():
        pop = int(raw_pop)

    place = tags.get('place')
    if place in PLACE_RANK:
        return place, pop

    amenity = tags.get('amenity')
    if amenity in AMENITY_KEEP:
        return amenity, pop

    tourism = tags.get('tourism')
    if tourism in TOURISM_KEEP:
        return tourism, pop

    shop = tags.get('shop')
    if shop in SHOP_KEEP:
        return shop, pop

    leisure = tags.get('leisure')
    if leisure in LEISURE_KEEP:
        return leisure, pop

    office = tags.get('office')
    if office in OFFICE_KEEP:
        return office, pop

    building = tags.get('building')
    if building in BUILDING_KEEP:
        # Only include buildings that also have a name (already checked above)
        return building, pop

    natural = tags.get('natural')
    if natural in NATURAL_KEEP:
        return natural, pop

    return None, None


class Command(BaseCommand):
    help = 'Import Ghana place names from OpenStreetMap (Geofabrik) into the local database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--pbf',
            dest='pbf_path',
            default=None,
            help='Path to a locally downloaded ghana-latest.osm.pbf file (skips download)',
        )

    def handle(self, *args, **options):
        try:
            import osmium
        except ImportError:
            self.stderr.write(
                'osmium is required. Install it with: pip install osmium'
            )
            return

        pbf_path = options['pbf_path']

        if pbf_path:
            if not os.path.exists(pbf_path):
                self.stderr.write(f'File not found: {pbf_path}')
                return
            self.stdout.write(f'Using local file: {pbf_path}')
            self._import(osmium, pbf_path)
        else:
            self.stdout.write(f'Downloading Ghana OSM extract from Geofabrik…')
            self.stdout.write('(~45 MB — this may take a minute on a slow connection)')
            try:
                resp = requests.get(GEOFABRIK_URL, timeout=300, stream=True)
                resp.raise_for_status()
            except Exception as e:
                self.stderr.write(f'Download failed: {e}')
                return

            # Stream to a temp file so osmium can read it
            with tempfile.NamedTemporaryFile(suffix='.osm.pbf', delete=False) as tmp:
                tmp_path = tmp.name
                downloaded = 0
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    tmp.write(chunk)
                    downloaded += len(chunk)
                    mb = downloaded / 1024 / 1024
                    self.stdout.write(f'\r  {mb:.1f} MB downloaded…', ending='')
                    self.stdout.flush()
            self.stdout.write('')

            try:
                self._import(osmium, tmp_path)
            finally:
                os.unlink(tmp_path)

    def _import(self, osmium, pbf_path):
        self.stdout.write('Parsing OSM data (nodes + ways)…')

        places = []

        class PlaceHandler(osmium.SimpleHandler):
            def node(self, n):
                if not n.location.valid():
                    return
                feature_code, pop = _categorize(n.tags)
                if feature_code is None:
                    return
                name = n.tags.get('name')
                en_name = n.tags.get('name:en', name)
                places.append(GhanaPlace(
                    name=name,
                    ascii_name=en_name,
                    latitude=n.location.lat,
                    longitude=n.location.lon,
                    feature_code=feature_code[:50],
                    population=pop,
                    source='osm',
                ))

            def way(self, w):
                feature_code, pop = _categorize(w.tags)
                if feature_code is None:
                    return
                # Compute centroid from node locations
                valid_locs = [
                    (nd.location.lat, nd.location.lon)
                    for nd in w.nodes
                    if nd.location.valid()
                ]
                if not valid_locs:
                    return
                avg_lat = sum(lat for lat, _ in valid_locs) / len(valid_locs)
                avg_lon = sum(lon for _, lon in valid_locs) / len(valid_locs)
                name = w.tags.get('name')
                en_name = w.tags.get('name:en', name)
                places.append(GhanaPlace(
                    name=name,
                    ascii_name=en_name,
                    latitude=avg_lat,
                    longitude=avg_lon,
                    feature_code=feature_code[:50],
                    population=pop,
                    source='osm',
                ))

        handler = PlaceHandler()
        # locations=True builds a node-location index so way nodes have coordinates
        handler.apply_file(pbf_path, locations=True)

        self.stdout.write(f'Found {len(places):,} named features')

        # ── Wipe everything and re-import ────────────────────────────────────
        self.stdout.write('Clearing old GhanaPlace entries…')
        deleted, _ = GhanaPlace.objects.all().delete()
        self.stdout.write(f'  Deleted {deleted:,} old records')

        self.stdout.write('Importing…')
        GhanaPlace.objects.bulk_create(places, batch_size=500, ignore_conflicts=True)
        self.stdout.write(f'  Inserted {len(places):,} OSM places')

        # ── Spatial region assignment ─────────────────────────────────────────
        # Use our existing Ghana district boundaries to tag each place with its
        # region name (e.g. "Eastern Region") via a single PostGIS spatial join.
        self.stdout.write('Assigning regions via spatial join…')
        try:
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute("""
                    UPDATE scanning_ghanaplace gp
                    SET region = subq.district
                    FROM (
                        SELECT
                            gp2.id,
                            r.district
                        FROM scanning_ghanaplace gp2
                        JOIN detections_region r
                          ON r.region_type = 'admin_district'
                          AND r.is_active = TRUE
                          AND ST_Contains(
                              r.geometry,
                              ST_SetSRID(ST_Point(gp2.longitude, gp2.latitude), 4326)
                          )
                        WHERE gp2.source = 'osm'
                    ) subq
                    WHERE gp.id = subq.id
                """)
            assigned = cursor.rowcount if hasattr(cursor, 'rowcount') else '?'
            self.stdout.write(f'  Regions assigned')
        except Exception as e:
            self.stdout.write(
                self.style.WARNING(f'  Spatial region assignment skipped: {e}')
            )

        total = GhanaPlace.objects.filter(source='osm').count()
        self.stdout.write(self.style.SUCCESS(
            f'Done — {total:,} OSM places in the local database.'
        ))
