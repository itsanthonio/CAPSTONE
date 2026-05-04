"""
Management command to seed the database with realistic test detections.

Creates DetectedSite, Alert, and SiteTimelapse records over known
galamsey hotspots in Ghana for UI testing and demos.

Usage:
    python manage.py seed_detections          # create 20 sites (default)
    python manage.py seed_detections --count 50
    python manage.py seed_detections --clear  # wipe existing first
"""

import random
import uuid
from datetime import date, timedelta
from django.core.management.base import BaseCommand
from django.contrib.gis.geos import Polygon, Point, GEOSGeometry


# Known galamsey hotspot areas in Ghana (lon, lat bounding boxes)
# Sources: EPA Ghana reports, academic literature on ASM regions
HOTSPOT_REGIONS = [
    {
        'name': 'Tarkwa-Nsuaem',
        'district': 'Western Region',
        'bbox': (-2.05, 5.25, -1.85, 5.45),   # (min_lon, min_lat, max_lon, max_lat)
        'weight': 0.20,
    },
    {
        'name': 'Obuasi',
        'district': 'Ashanti Region',
        'bbox': (-1.75, 6.15, -1.55, 6.35),
        'weight': 0.15,
    },
    {
        'name': 'Amansie West',
        'district': 'Ashanti Region',
        'bbox': (-2.10, 6.20, -1.90, 6.40),
        'weight': 0.15,
    },
    {
        'name': 'Prestea-Huni Valley',
        'district': 'Western Region',
        'bbox': (-2.20, 5.35, -2.00, 5.55),
        'weight': 0.12,
    },
    {
        'name': 'Upper Denkyira',
        'district': 'Central Region',
        'bbox': (-1.95, 5.55, -1.70, 5.80),
        'weight': 0.10,
    },
    {
        'name': 'Bibiani-Anhwiaso',
        'district': 'Western North Region',
        'bbox': (-2.35, 6.40, -2.10, 6.65),
        'weight': 0.10,
    },
    {
        'name': 'Wassa Amenfi',
        'district': 'Western Region',
        'bbox': (-2.45, 5.55, -2.20, 5.80),
        'weight': 0.10,
    },
    {
        'name': 'Ahafo-Ano',
        'district': 'Ashanti Region',
        'bbox': (-2.00, 6.70, -1.75, 6.95),
        'weight': 0.08,
    },
]


def random_polygon_around(lon, lat, size_deg=0.003):
    """Create a small irregular polygon around a central point."""
    offsets = [
        (random.uniform(0, size_deg),  random.uniform(0, size_deg)),
        (random.uniform(0, size_deg),  random.uniform(-size_deg, 0)),
        (random.uniform(-size_deg, 0), random.uniform(-size_deg * 0.5, size_deg * 0.5)),
        (random.uniform(-size_deg, 0), random.uniform(0, size_deg)),
    ]
    coords = [(lon + dx, lat + dy) for dx, dy in offsets]
    coords.append(coords[0])  # close ring
    return Polygon(coords, srid=4326)


def weighted_choice(regions):
    r = random.random()
    cum = 0
    for region in regions:
        cum += region['weight']
        if r <= cum:
            return region
    return regions[-1]


class Command(BaseCommand):
    help = 'Seed database with realistic test detection sites and alerts'

    def add_arguments(self, parser):
        parser.add_argument('--count', type=int, default=20,
                            help='Number of DetectedSite records to create (default: 20)')
        parser.add_argument('--clear', action='store_true',
                            help='Delete all existing DetectedSite and Alert records first')

    def handle(self, *args, **options):
        from apps.detections.models import DetectedSite, Alert, Region, SiteTimelapse
        from apps.jobs.models import Job

        count = options['count']

        if options['clear']:
            deleted_alerts = Alert.objects.all().delete()[0]
            deleted_sites  = DetectedSite.objects.all().delete()[0]
            self.stdout.write(self.style.WARNING(
                f'Cleared {deleted_sites} sites and {deleted_alerts} alerts.'
            ))

        # Ensure Region records exist for each hotspot
        region_cache = {}
        for h in HOTSPOT_REGIONS:
            min_lon, min_lat, max_lon, max_lat = h['bbox']
            # Build a MultiPolygon from the bounding box to satisfy NOT NULL geometry
            from django.contrib.gis.geos import MultiPolygon
            bbox_poly = Polygon.from_bbox((min_lon, min_lat, max_lon, max_lat))
            bbox_poly.srid = 4326
            bbox_multi = MultiPolygon(bbox_poly, srid=4326)

            region, _ = Region.objects.get_or_create(
                name=h['name'],
                defaults={
                    'region_type': 'hotspot',
                    'district': h['district'],
                    'is_active': True,
                    'geometry': bbox_multi,
                }
            )
            region_cache[h['name']] = region

        # Create a dummy job to satisfy FK (if no jobs exist)
        try:
            job = Job.objects.filter(status='completed').first()
            if not job:
                from django.contrib.gis.geos import Polygon as GPolygon
                aoi = GPolygon((
                    (-2.5, 5.0), (-1.5, 5.0), (-1.5, 7.0), (-2.5, 7.0), (-2.5, 5.0)
                ), srid=4326)
                job = Job.objects.create(
                    aoi_geometry=aoi,
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 3, 31),
                    model_version='v1.0',
                    status='completed',
                )
        except Exception as e:
            self.stderr.write(f'Warning: could not create/find job: {e}')
            job = None

        today = date.today()
        sites_created  = 0
        alerts_created = 0

        for i in range(count):
            hotspot = weighted_choice(HOTSPOT_REGIONS)
            min_lon, min_lat, max_lon, max_lat = hotspot['bbox']

            # Random centre within the hotspot bbox
            center_lon = random.uniform(min_lon, max_lon)
            center_lat = random.uniform(min_lat, max_lat)

            # Variable polygon size: 0.5–8 ha in the field
            size_deg = random.uniform(0.001, 0.005)
            polygon  = random_polygon_around(center_lon, center_lat, size_deg)
            centroid = Point(center_lon, center_lat, srid=4326)

            # Detection date spread over last 6 months
            days_ago = random.randint(0, 180)
            det_date = today - timedelta(days=days_ago)

            confidence   = random.uniform(0.62, 0.97)
            area_ha      = random.uniform(0.3, 12.0)
            recurrence   = random.choices([1, 2, 3, 4], weights=[0.55, 0.25, 0.12, 0.08])[0]

            # Legal status: ~75% illegal in known hotspots
            legal_status = random.choices(
                ['illegal', 'legal', 'unknown'],
                weights=[0.75, 0.10, 0.15]
            )[0]

            status = random.choices(
                ['pending_review', 'confirmed_illegal', 'false_positive'],
                weights=[0.5, 0.4, 0.1]
            )[0]

            kwargs = dict(
                geometry=polygon,
                centroid=centroid,
                confidence_score=round(confidence, 4),
                area_hectares=round(area_ha, 2),
                detection_date=det_date,
                legal_status=legal_status,
                status=status,
                recurrence_count=recurrence,
                first_detected_at=det_date,
                region=region_cache[hotspot['name']],
            )
            if job:
                kwargs['job'] = job

            site = DetectedSite.objects.create(**kwargs)
            sites_created += 1

            # Create Alert for illegal sites
            if legal_status == 'illegal':
                # Severity based on confidence + recurrence + area
                if recurrence > 2 or confidence > 0.90:
                    severity = 'critical'
                elif recurrence > 1 or area_ha > 5:
                    severity = 'high'
                elif confidence > 0.75:
                    severity = 'medium'
                else:
                    severity = 'low'

                alert_type = random.choices(
                    ['new_detection', 'expansion_detected', 'recurring_site', 'high_confidence'],
                    weights=[0.5, 0.2, 0.2, 0.1]
                )[0]

                alert_status = random.choices(
                    ['open', 'acknowledged', 'dispatched', 'resolved'],
                    weights=[0.45, 0.25, 0.15, 0.15]
                )[0]

                title = (
                    f"Illegal mining detected — {hotspot['name']}"
                    if alert_type == 'new_detection' else
                    f"Recurring illegal site — {hotspot['name']}"
                    if alert_type == 'recurring_site' else
                    f"Site expansion detected — {hotspot['name']}"
                    if alert_type == 'expansion_detected' else
                    f"High-confidence detection — {hotspot['name']}"
                )

                description = (
                    f"{round(area_ha, 1)} ha site detected with {round(confidence*100,1)}% confidence "
                    f"in {hotspot['district']}. "
                )
                if recurrence > 1:
                    description += f"Previously detected {recurrence - 1} time(s). "
                if severity in ('critical', 'high'):
                    description += "Immediate inspection recommended."

                from django.utils import timezone
                created_at = timezone.now() - timedelta(days=days_ago)

                alert = Alert(
                    detected_site=site,
                    alert_type=alert_type,
                    severity=severity,
                    status=alert_status,
                    title=title,
                    description=description,
                )
                alert.save()
                # Backdate created_at to match detection
                Alert.objects.filter(pk=alert.pk).update(created_at=created_at)
                alerts_created += 1

            # Add stub timelapse entries (no real thumbnails, just year markers)
            for year in range(2020, 2025):
                SiteTimelapse.objects.get_or_create(
                    detected_site=site,
                    year=year,
                    defaults={
                        'acquisition_period': f'Q4-{year}',
                        'thumbnail_url': '',
                        'cloud_cover_pct': round(random.uniform(5, 30), 1),
                        'mean_ndvi': round(random.uniform(0.05, 0.35), 3),
                        'mean_bsi':  round(random.uniform(0.10, 0.55), 3),
                    }
                )

        self.stdout.write(self.style.SUCCESS(
            f'Created {sites_created} detection sites and {alerts_created} alerts '
            f'across {len(HOTSPOT_REGIONS)} hotspot regions in Ghana.'
        ))