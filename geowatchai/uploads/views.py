import json
import string
from django.shortcuts import render
from django.views.generic import TemplateView
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.conf import settings
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon, LineString, MultiLineString


def _title(s):
    return string.capwords(s.strip().lower())


class DataUploadsView(LoginRequiredMixin, TemplateView):
    template_name = 'uploads/data_uploads.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            from apps.detections.models import LegalConcession
            concession_count = LegalConcession.objects.count()
        except Exception:
            concession_count = 0

        context.update({
            'settings': settings,
            'page_title': 'Data Uploads',
            'concession_count': concession_count,
        })
        return context


def _parse_geojson(file):
    """Read uploaded file and return parsed GeoJSON dict."""
    raw = file.read()
    try:
        return json.loads(raw.decode('utf-8'))
    except Exception:
        return json.loads(raw.decode('latin-1'))


# ~500 m buffer in degrees (suitable for Ghana near the equator)
_LINE_BUFFER_DEG = 0.005


def _to_multipolygon(geom):
    """Coerce a GEOSGeometry to MultiPolygon, or return None if not polygon-like.
    LineString / MultiLineString features are buffered into polygon zones."""
    if isinstance(geom, MultiPolygon):
        return geom
    if isinstance(geom, Polygon):
        return MultiPolygon(geom)
    if isinstance(geom, (LineString, MultiLineString)):
        buffered = geom.buffer(_LINE_BUFFER_DEG)
        if isinstance(buffered, Polygon):
            return MultiPolygon(buffered)
        if isinstance(buffered, MultiPolygon):
            return buffered
    return None


class UploadConcessionsView(LoginRequiredMixin, View):
    """
    Import legal concession boundaries from a GeoJSON file.
    Clears all existing concessions, imports the new ones,
    then reruns the spatial join to update every DetectedSite's legal_status.
    """

    def post(self, request):
        upload = request.FILES.get('file')
        if not upload:
            return JsonResponse({'error': 'No file uploaded.'}, status=400)

        try:
            geojson = _parse_geojson(upload)
        except Exception:
            return JsonResponse({'error': 'Could not parse file: invalid or unsupported format.'}, status=400)

        features = geojson.get('features', [])
        if not features:
            return JsonResponse({'error': 'GeoJSON has no features.'}, status=400)

        from apps.detections.models import LegalConcession, DetectedSite
        from django.db import transaction

        imported = 0
        skipped = 0

        with transaction.atomic():
            # Clear and reimport — all within a single transaction so a
            # mid-import failure rolls back the delete automatically.
            LegalConcession.objects.all().delete()

            for feat in features:
                props = feat.get('properties') or {}
                raw_geom = feat.get('geometry')
                if not raw_geom:
                    skipped += 1
                    continue

                try:
                    geom = _to_multipolygon(GEOSGeometry(json.dumps(raw_geom), srid=4326))
                except Exception:
                    skipped += 1
                    continue
                if geom is None:
                    skipped += 1
                    continue

                # Try common GeoJSON property name variants
                def get_prop(*keys):
                    for k in keys:
                        v = props.get(k) or props.get(k.lower()) or props.get(k.upper())
                        if v:
                            return str(v).strip()
                    return ''

                license_number = get_prop('license_number', 'LicenseNo', 'license_no', 'LICENSE_NO', 'id', 'FID') or f'IMPORT-{imported + 1}'
                concession_name = get_prop('concession_name', 'Name', 'name', 'ConcessionName', 'CONCESSION') or f'Concession {imported + 1}'
                holder_name = get_prop('holder_name', 'Holder', 'Company', 'company', 'CompanyName', 'COMPANY') or 'Unknown'
                license_type = get_prop('license_type', 'LicenseType', 'Type', 'type', 'LICENSE_TYPE') or 'small_scale'

                # Normalise license type to valid choice
                lt_map = {
                    'large': 'large_scale', 'large_scale': 'large_scale',
                    'small': 'small_scale', 'small_scale': 'small_scale',
                    'exploration': 'exploration', 'reconnaissance': 'reconnaissance',
                }
                license_type = lt_map.get(license_type.lower().replace(' ', '_'), 'small_scale')

                try:
                    LegalConcession.objects.create(
                        license_number=license_number,
                        concession_name=concession_name,
                        holder_name=holder_name,
                        license_type=license_type,
                        geometry=geom,
                        is_active=True,
                        data_source='minerals_commission',
                    )
                    imported += 1
                except Exception:
                    # Duplicate license_number — skip
                    skipped += 1
                    continue

            # Rerun spatial join for all detected sites
            updated_legal = 0
            updated_illegal = 0
            sites = list(DetectedSite.objects.all().select_related('intersecting_concession'))
            for site in sites:
                if not site.geometry:
                    continue
                match = LegalConcession.objects.filter(
                    geometry__intersects=site.geometry,
                    is_active=True
                ).first()
                if match:
                    site.legal_status = DetectedSite.LegalStatus.LEGAL
                    site.intersecting_concession = match
                    updated_legal += 1
                else:
                    site.legal_status = DetectedSite.LegalStatus.ILLEGAL
                    site.intersecting_concession = None
                    updated_illegal += 1

            DetectedSite.objects.bulk_update(sites, ['legal_status', 'intersecting_concession'])

        return JsonResponse({
            'success': True,
            'imported': imported,
            'skipped': skipped,
            'sites_updated': len(sites),
            'now_legal': updated_legal,
            'now_illegal': updated_illegal,
            'message': (
                f'Imported {imported} concessions ({skipped} skipped). '
                f'Updated {len(sites)} sites: {updated_legal} legal, {updated_illegal} illegal.'
            ),
        })


class UploadRegionView(LoginRequiredMixin, View):
    """
    Generic GeoJSON upload for Region records.
    `region_type` is fixed by the URL (water_body or protected_forest).
    """
    region_type = None  # set per subclass

    def post(self, request):
        upload = request.FILES.get('file')
        if not upload:
            return JsonResponse({'error': 'No file uploaded.'}, status=400)

        try:
            geojson = _parse_geojson(upload)
        except Exception:
            return JsonResponse({'error': 'Could not parse file: invalid or unsupported format.'}, status=400)

        features = geojson.get('features', [])
        if not features:
            return JsonResponse({'error': 'GeoJSON has no features.'}, status=400)

        from apps.detections.models import Region
        from django.db import transaction

        imported = 0
        skipped = 0

        with transaction.atomic():
            # Clear existing regions of this type and reimport — atomic so a
            # mid-import failure rolls back the delete.
            Region.objects.filter(region_type=self.region_type).delete()

            for i, feat in enumerate(features):
                props = feat.get('properties') or {}
                raw_geom = feat.get('geometry')
                if not raw_geom:
                    skipped += 1
                    continue

                try:
                    geom = GEOSGeometry(json.dumps(raw_geom), srid=4326)
                except Exception:
                    skipped += 1
                    continue

                # Region needs a MultiPolygon
                mp = _to_multipolygon(geom)
                if mp is None:
                    skipped += 1
                    continue

                def get_prop(*keys):
                    for k in keys:
                        v = props.get(k) or props.get(k.lower()) or props.get(k.upper())
                        if v:
                            return str(v).strip()
                    return ''

                name = get_prop('name', 'Name', 'NAME', 'label', 'Label') or f'Region {i + 1}'
                district = get_prop('district', 'District', 'DISTRICT', 'region', 'Region') or ''
                notes = get_prop('notes', 'Notes', 'description', 'Description') or ''

                # Ensure unique name
                base_name = name
                counter = 1
                while Region.objects.filter(name=name).exists():
                    name = f'{base_name} ({counter})'
                    counter += 1

                try:
                    Region.objects.create(
                        name=name,
                        region_type=self.region_type,
                        geometry=mp,
                        district=district,
                        notes=notes,
                        is_active=True,
                    )
                    imported += 1
                except Exception:
                    skipped += 1

        return JsonResponse({
            'success': True,
            'imported': imported,
            'skipped': skipped,
            'message': f'Imported {imported} regions ({skipped} skipped).',
        })


class UploadWaterBodiesView(UploadRegionView):
    region_type = 'water_body'


class UploadProtectedForestView(UploadRegionView):
    region_type = 'protected_forest'


class UploadDistrictsView(LoginRequiredMixin, View):
    """
    Import Ghana administrative district boundaries from a GeoJSON file.
    Expects properties: DISTRICT (name), Region_19 or REGION (parent region).
    Replaces all existing admin_district records on each upload.
    """

    def post(self, request):
        upload = request.FILES.get('file')
        if not upload:
            return JsonResponse({'error': 'No file uploaded.'}, status=400)

        try:
            geojson = _parse_geojson(upload)
        except Exception:
            return JsonResponse({'error': 'Could not parse file: invalid or unsupported format.'}, status=400)

        features = geojson.get('features', [])
        if not features:
            return JsonResponse({'error': 'GeoJSON has no features.'}, status=400)

        from apps.detections.models import Region
        from django.db import transaction

        imported = skipped = 0

        with transaction.atomic():
            # Atomic: if any unrecoverable error occurs, the delete is rolled back.
            Region.objects.filter(region_type='admin_district').delete()

            for i, feat in enumerate(features):
                props = feat.get('properties') or {}
                raw_geom = feat.get('geometry')
                if not raw_geom:
                    skipped += 1
                    continue

                raw_name   = props.get('DISTRICT') or props.get('name') or props.get('NAME') or ''
                raw_region = props.get('Region_19') or props.get('REGION') or props.get('region') or ''

                if not raw_name.strip():
                    skipped += 1
                    continue

                name   = _title(raw_name)
                parent = _title(raw_region)

                try:
                    geom = _to_multipolygon(GEOSGeometry(json.dumps(raw_geom), srid=4326))
                except Exception:
                    skipped += 1
                    continue
                if geom is None:
                    skipped += 1
                    continue

                # Ensure unique name
                base_name, counter = name, 1
                while Region.objects.filter(name=name).exists():
                    name = f'{base_name} ({counter})'
                    counter += 1

                try:
                    Region.objects.create(
                        name=name,
                        region_type='admin_district',
                        district=parent,
                        geometry=geom,
                        is_active=True,
                    )
                    imported += 1
                except Exception:
                    skipped += 1

        return JsonResponse({
            'success': True,
            'imported': imported,
            'skipped': skipped,
            'message': f'Imported {imported} districts ({skipped} skipped).',
        })
