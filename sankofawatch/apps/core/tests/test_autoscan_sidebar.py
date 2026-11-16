"""Auto Scan sidebar: nearest-place names and plain-language fields."""
import pytest
from django.contrib.gis.geos import Polygon
from django.urls import reverse
from django.utils import timezone


def _agency_login(client, django_user_model):
    from apps.accounts.models import Organisation
    org, _ = Organisation.objects.get_or_create(name='Org1')
    u = django_user_model.objects.create_user('aa', 'aa@example.com', 'pw-12345-xyz')
    u.profile.role = 'agency_admin'
    u.profile.organisation = org
    u.profile.save()
    client.force_login(u)


def _place(name, lat, lng, code='town', region='Western'):
    from apps.scanning.models import GhanaPlace
    return GhanaPlace.objects.create(
        name=name, ascii_name=name, latitude=lat, longitude=lng,
        feature_code=code, region=region)


@pytest.mark.django_db
def test_nearest_place_prefers_closest_settlement_and_ignores_other_kinds():
    from apps.scanning import places
    places._cache.clear()
    _place('Tarkwa', 5.30, -1.99)
    _place('Faraway', 5.45, -1.99)
    _place('Tarkwa Primary School', 5.3001, -1.9901, code='school')
    assert places.nearest_place_label(5.301, -1.991) == 'Tarkwa, Western'


@pytest.mark.django_db
def test_nearest_place_empty_when_nothing_near():
    from apps.scanning import places
    places._cache.clear()
    assert places.nearest_place_label(9.0, 0.0) == ''
    assert places.nearest_place_label('x', None) == ''


@pytest.mark.django_db
def test_recent_tiles_and_tile_detail_include_place_name(client, django_user_model):
    from apps.scanning import places
    from apps.scanning.models import ScanTile
    places._cache.clear()
    _place('Tarkwa', 5.30, -1.99)
    tile = ScanTile.objects.create(
        name='GH_-2.000_5.270',
        geometry=Polygon(((-2.0, 5.27), (-1.93, 5.27), (-1.93, 5.34), (-2.0, 5.34), (-2.0, 5.27)), srid=4326),
        priority='hotspot', scan_count=3, last_scanned_at=timezone.now())
    _agency_login(client, django_user_model)

    data = client.get(reverse('scanning:api_recent_tiles')).json()
    assert data['features'][0]['properties']['place'] == 'Tarkwa, Western'

    detail = client.get(reverse('scanning:api_tile_detail'), {'lat': 5.30, 'lng': -1.97}).json()
    assert detail['id'] == str(tile.id)
    assert detail['place'] == 'Tarkwa, Western'


@pytest.mark.django_db
def test_auto_scan_page_renders_plain_language_sidebar(client, django_user_model):
    _agency_login(client, django_user_model)
    html = client.get(reverse('scanning:auto_scan')).content.decode()
    assert 'id="today-summary"' in html
    assert 'Recent scans' in html and 'Top regions' in html
    assert 'activity-empty-title' in html


@pytest.mark.django_db
def test_recent_tiles_report_sites_found_per_tile(client, django_user_model):
    from datetime import date
    from apps.detections.models import DetectedSite
    from apps.jobs.models import Job
    from apps.scanning.models import ScanTile
    from django.contrib.gis.geos import Point

    def mk(name, x0):
        return ScanTile.objects.create(
            name=name,
            geometry=Polygon(((x0, 5.27), (x0 + 0.07, 5.27), (x0 + 0.07, 5.34), (x0, 5.34), (x0, 5.27)), srid=4326),
            priority='normal', scan_count=1, last_scanned_at=timezone.now())

    t1, t2 = mk('T1', -2.0), mk('T2', -1.93)
    aoi = Polygon(((-2.0, 5.27), (-1.8, 5.27), (-1.8, 5.34), (-2.0, 5.34), (-2.0, 5.27)), srid=4326)
    job = Job.objects.create(aoi_geometry=aoi, start_date=date.today(), end_date=date.today(),
                             model_version='v', preprocessing_version='v', source='automated')

    def site(x, y):
        sq = Polygon(((x, y), (x + 0.001, y), (x + 0.001, y + 0.001), (x, y + 0.001), (x, y)), srid=4326)
        DetectedSite.objects.create(geometry=sq, centroid=Point(x, y, srid=4326), confidence_score=0.9,
                                    area_hectares=1.0, detection_date=date.today(), job=job)

    site(-1.97, 5.30); site(-1.96, 5.31)      # inside T1
    site(-1.90, 5.30)                         # inside T2
    site(-1.50, 5.30)                         # outside both

    admin = django_user_model.objects.create_user('sa', 'sa@example.com', 'pw-12345-xyz')
    admin.profile.role = 'system_admin'
    admin.profile.save()
    client.force_login(admin)
    feats = client.get(reverse('scanning:api_recent_tiles')).json()['features']
    counts = {f['properties']['name']: f['properties']['detection_count'] for f in feats}
    assert counts == {'T1': 2, 'T2': 1}


@pytest.mark.django_db
def test_system_admin_can_open_auto_scan_map(client, django_user_model):
    u = django_user_model.objects.create_user('sa2', 'sa2@example.com', 'pw-12345-xyz')
    u.profile.role = 'system_admin'
    u.profile.save()
    client.force_login(u)
    r = client.get(reverse('scanning:auto_scan'))
    assert r.status_code == 200
    assert 'const IS_STAFF = true' in r.content.decode()
