"""
Views for the automated scanning system.
- auto_scan_control      — web page for system_admin (pause/resume + time window)
- auto_scan              — web page for agency_admin (Leaflet map + live stats)
- ScanningStatusAPI      — GET /scanning/api/status/
- ScanningToggleAPI      — POST /scanning/api/toggle/
- ScanningConfigAPI      — PATCH /scanning/api/config/
- ScanningRecentTilesAPI — GET /scanning/api/recent-tiles/
- ScanningDetectionsAPI  — GET /scanning/api/detections/
- ScanningTileDetailAPI  — GET /scanning/api/tile-detail/?lat=X&lng=Y
- ScanningForceScanAPI   — POST /scanning/api/force-scan/
- ScanningExportAPI      — GET /scanning/api/export/?format=geojson|csv
"""
import csv
import json
import logging
from datetime import timedelta
from io import StringIO

from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View

from .models import AutoScanConfig, OrgScanConfig, ScanTile

logger = logging.getLogger(__name__)


@login_required
def auto_scan_control(request):
    """System admin only: pause/resume + time window configuration."""
    if not (hasattr(request.user, 'profile') and request.user.profile.role == 'system_admin'):
        from django.shortcuts import redirect
        return redirect('scanning:auto_scan')
    from apps.accounts.models import Organisation
    config = AutoScanConfig.get()
    orgs   = Organisation.objects.all().order_by('name')
    # Attach per-org scan config (create defaults as needed)
    global_on   = config.is_enabled
    org_configs = []
    for org in orgs:
        cfg       = OrgScanConfig.get_for_org(org)
        effective = global_on and cfg.is_enabled
        org_configs.append({'org': org, 'cfg': cfg, 'effective': effective})
    return render(request, 'scanning/auto_scan_control.html', {
        'config':         config,
        'global_enabled': config.is_enabled,
        'org_configs':    org_configs,
        'hours':          list(range(24)),
        'title':          'Auto Scan Control',
    })


@login_required
def auto_scan(request):
    """Render the detailed Auto Scan page (agency_admin). Redirect system_admin."""
    if hasattr(request.user, 'profile') and request.user.profile.role == 'system_admin':
        from django.shortcuts import redirect
        return redirect('scanning:auto_scan_control')
    config = AutoScanConfig.get()
    return render(request, 'scanning/auto_scan.html', {
        'config': config,
        'is_system_admin': False,
        'title': 'Auto Scan',
    })


@method_decorator(login_required, name='dispatch')
class ScanningStatusAPI(View):
    """GET /scanning/api/status/ — returns system state + today's stats."""

    def get(self, request):
        if not (hasattr(request.user, 'profile') and request.user.profile.role in ('system_admin', 'agency_admin')):
            return JsonResponse({'error': 'Administrator access required.'}, status=403)
        from apps.jobs.models import Job
        from apps.detections.models import DetectedSite

        config = AutoScanConfig.get()
        now    = timezone.now()
        today  = now.date()
        fourteen_days_ago = today - timedelta(days=13)

        # Reset daily counter if needed
        config.reset_daily_counter_if_needed()

        # ── Today's job stats ─────────────────────────────────────────────
        auto_jobs_today = Job.objects.filter(source='automated', created_at__date=today)
        total_auto_today     = auto_jobs_today.count()
        completed_auto_today = auto_jobs_today.filter(status='completed').count()
        failed_auto_today    = auto_jobs_today.filter(status='failed').count()

        in_flight_statuses = [
            'queued', 'validating', 'exporting',
            'preprocessing', 'inferring', 'postprocessing', 'storing',
        ]
        running_auto_today = auto_jobs_today.filter(status__in=in_flight_statuses).count()

        # ── Running jobs with tile location (for "now scanning" badges) ───
        running_jobs_qs = (
            auto_jobs_today
            .filter(status__in=in_flight_statuses, scan_tile__isnull=False)
            .select_related('scan_tile')
        )
        running_jobs = []
        for job in running_jobs_qs:
            tile = job.scan_tile
            try:
                centroid = tile.geometry.centroid
                running_jobs.append({
                    'job_id':    str(job.id),
                    'status':    job.status,
                    'tile_name': tile.name,
                    'tile_lat':  round(centroid.y, 6),
                    'tile_lng':  round(centroid.x, 6),
                })
            except Exception:
                pass

        # ── Next tiles to scan ────────────────────────────────────────────
        # Mirror the tiered scanning priority: unscanned tiles first (NULL last_scanned_at),
        # then hotspot before normal, then oldest last_scanned_at within each group.
        # Use nulls_first=True so never-scanned tiles (Tier 1) appear at the top.
        from django.db.models import F
        next_tiles_qs = (
            ScanTile.objects
            .filter(is_active=True)
            .order_by('priority', F('last_scanned_at').asc(nulls_first=True))
            [:5]
        )
        next_tiles = []
        for tile in next_tiles_qs:
            try:
                centroid = tile.geometry.centroid
                next_tiles.append({
                    'name':     tile.name,
                    'priority': tile.priority,
                    'lat':      round(centroid.y, 6),
                    'lng':      round(centroid.x, 6),
                })
            except Exception:
                pass

        # ── Failed jobs today with tile location (for failure map) ────────
        failed_jobs_qs = (
            auto_jobs_today
            .filter(status='failed', scan_tile__isnull=False)
            .select_related('scan_tile')
        )
        failed_jobs_today = []
        for job in failed_jobs_qs:
            tile = job.scan_tile
            try:
                centroid = tile.geometry.centroid
                failed_jobs_today.append({
                    'tile_name':      tile.name,
                    'tile_lat':       round(centroid.y, 6),
                    'tile_lng':       round(centroid.x, 6),
                    'failure_reason': job.failure_reason or 'Unknown error',
                })
            except Exception:
                pass

        # ── Detections today ──────────────────────────────────────────────
        # Use detection_date (not job__created_at) because deduplication keeps
        # the original job FK but updates detection_date to the current scan's
        # end_date, so job__created_at__date misses deduped sites.
        detections_today = DetectedSite.objects.filter(
            job__source='automated',
            detection_date=today,
        ).count()

        # ── 14-day daily detection counts (for sparkline) ─────────────────
        by_day_qs = (
            DetectedSite.objects
            .filter(
                job__source='automated',
                detection_date__gte=fourteen_days_ago,
            )
            .values('detection_date')
            .annotate(count=Count('id'))
            .order_by('detection_date')
        )
        # Fill in missing days with 0
        by_day_map = {row['detection_date']: row['count'] for row in by_day_qs}
        detections_by_day = [
            {'date': str(today - timedelta(days=13 - i)), 'count': by_day_map.get(today - timedelta(days=13 - i), 0)}
            for i in range(14)
        ]

        # ── Top 5 regions by all-time automated detections ────────────────
        by_region_qs = (
            DetectedSite.objects
            .filter(job__source='automated', region__isnull=False)
            .values('region__name')
            .annotate(count=Count('id'))
            .order_by('-count')
            [:5]
        )
        detections_by_region = [
            {'region': row['region__name'], 'count': row['count']}
            for row in by_region_qs
        ]

        # ── Tile stats ────────────────────────────────────────────────────
        total_tiles   = ScanTile.objects.filter(is_active=True).count()
        hotspot_tiles = ScanTile.objects.filter(is_active=True, priority=ScanTile.Priority.HOTSPOT).count()
        scanned_tiles = ScanTile.objects.filter(is_active=True, last_scanned_at__isnull=False).count()

        # ── Per-org scan states (2 queries, not N+1) ──────────────────────
        from apps.accounts.models import Organisation
        all_orgs    = list(Organisation.objects.all().order_by('name'))
        cfg_map     = {cfg.organisation_id: cfg.is_enabled
                       for cfg in OrgScanConfig.objects.all()}
        org_statuses = [
            {'id': str(org.pk), 'is_enabled': cfg_map.get(org.pk, True)}
            for org in all_orgs
        ]
        active_orgs  = sum(1 for o in org_statuses if o['is_enabled'])
        total_orgs   = len(org_statuses)

        # ── System status ─────────────────────────────────────────────────
        # Derived from per-org active count rather than a single global flag.
        within_window = config.is_within_window()
        rate_limited  = config.is_rate_limited_today()

        if active_orgs == 0:
            system_status = 'paused'
        elif rate_limited:
            system_status = 'rate_limited'
        elif not within_window:
            status_hour = now.astimezone().hour
            system_status = 'waiting' if status_hour < config.window_start_hour else 'done'
        else:
            system_status = 'running'

        return JsonResponse({
            'system_status':    system_status,
            'is_enabled':       active_orgs > 0,
            'within_window':    within_window,
            'rate_limited':     rate_limited,
            'window_start':     config.window_start_hour,
            'window_end':       config.window_end_hour,
            'tiles_scanned_today':   config.tiles_scanned_today,
            'total_tiles':      total_tiles,
            'hotspot_tiles':    hotspot_tiles,
            'scanned_tiles':    scanned_tiles,
            'total_auto_today':      total_auto_today,
            'completed_auto_today':  completed_auto_today,
            'failed_auto_today':     failed_auto_today,
            'running_auto_today':    running_auto_today,
            'detections_today':      detections_today,
            'server_time':           now.strftime('%H:%M'),
            # Org states for AJAX sync
            'active_orgs':           active_orgs,
            'total_orgs':            total_orgs,
            'org_statuses':          org_statuses,
            # Other fields
            'running_jobs':          running_jobs,
            'next_tiles':            next_tiles,
            'failed_jobs_today':     failed_jobs_today,
            'detections_by_day':     detections_by_day,
            'detections_by_region':  detections_by_region,
        })


@method_decorator(login_required, name='dispatch')
class ScanningToggleAPI(View):
    """POST /scanning/api/toggle/ — pause or resume ALL organisations at once."""

    def post(self, request):
        if not (request.user.is_authenticated and hasattr(request.user, 'profile') and request.user.profile.role == 'system_admin'):
            return JsonResponse({'error': 'System Administrator access required.'}, status=403)
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            body = {}

        action = body.get('action')  # 'pause' | 'resume'
        if action not in ('pause', 'resume'):
            return JsonResponse({'error': 'action must be pause or resume'}, status=400)

        from apps.accounts.models import Organisation
        enabled = (action == 'resume')

        # Bulk-update all existing OrgScanConfig records.
        OrgScanConfig.objects.all().update(is_enabled=enabled)

        # Create missing configs for orgs that don't have one yet.
        existing_ids = set(OrgScanConfig.objects.values_list('organisation_id', flat=True))
        new_cfgs = [
            OrgScanConfig(organisation=org, is_enabled=enabled)
            for org in Organisation.objects.exclude(pk__in=existing_ids)
        ]
        if new_cfgs:
            OrgScanConfig.objects.bulk_create(new_cfgs)

        total  = Organisation.objects.count()
        active = total if enabled else 0
        logger.info(f'Bulk scan {"resume" if enabled else "pause"} by {request.user}')
        return JsonResponse({'active_orgs': active, 'total_orgs': total})


@method_decorator(login_required, name='dispatch')
class ScanningConfigAPI(View):
    """PATCH /scanning/api/config/ — update time window (system_admin only)."""

    def patch(self, request):
        if not (request.user.is_authenticated and hasattr(request.user, 'profile') and request.user.profile.role == 'system_admin'):
            return JsonResponse({'error': 'System Administrator access required.'}, status=403)
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            body = {}

        config  = AutoScanConfig.get()
        updated = []

        for field in ('window_start_hour', 'window_end_hour'):
            if field in body:
                try:
                    val = int(body[field])
                    if 0 <= val <= 23:
                        setattr(config, field, val)
                        updated.append(field)
                except (TypeError, ValueError):
                    pass

        if updated:
            config.save(update_fields=updated)

        return JsonResponse({
            'window_start_hour': config.window_start_hour,
            'window_end_hour':   config.window_end_hour,
        })


@method_decorator(login_required, name='dispatch')
class OrgScanToggleAPI(View):
    """POST /scanning/api/org-toggle/<org_id>/ — pause or resume scanning for one org."""

    def post(self, request, org_id):
        if not (request.user.is_authenticated and hasattr(request.user, 'profile') and request.user.profile.role == 'system_admin'):
            return JsonResponse({'error': 'System Administrator access required.'}, status=403)
        from apps.accounts.models import Organisation
        try:
            org = Organisation.objects.get(pk=org_id)
        except Organisation.DoesNotExist:
            return JsonResponse({'error': 'Organisation not found.'}, status=404)
        try:
            body   = json.loads(request.body)
            action = body.get('action')
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON.'}, status=400)

        cfg = OrgScanConfig.get_for_org(org)
        if action == 'pause':
            cfg.is_enabled = False
        elif action == 'resume':
            cfg.is_enabled = True
        else:
            return JsonResponse({'error': 'action must be pause or resume'}, status=400)
        cfg.save(update_fields=['is_enabled'])
        return JsonResponse({'org_id': str(org.pk), 'is_enabled': cfg.is_enabled})


@method_decorator(login_required, name='dispatch')
class OrgScanConfigAPI(View):
    """PATCH /scanning/api/org-config/<org_id>/ — update time window for one org."""

    def patch(self, request, org_id):
        if not (request.user.is_authenticated and hasattr(request.user, 'profile') and request.user.profile.role == 'system_admin'):
            return JsonResponse({'error': 'System Administrator access required.'}, status=403)
        from apps.accounts.models import Organisation
        try:
            org = Organisation.objects.get(pk=org_id)
        except Organisation.DoesNotExist:
            return JsonResponse({'error': 'Organisation not found.'}, status=404)
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON.'}, status=400)

        cfg     = OrgScanConfig.get_for_org(org)
        updated = []
        for field in ('window_start_hour', 'window_end_hour'):
            if field in body:
                try:
                    val = int(body[field])
                    if 0 <= val <= 23:
                        setattr(cfg, field, val)
                        updated.append(field)
                except (TypeError, ValueError):
                    pass
        if updated:
            cfg.save(update_fields=updated)
        return JsonResponse({
            'org_id':            str(org.pk),
            'is_enabled':        cfg.is_enabled,
            'window_start_hour': cfg.window_start_hour,
            'window_end_hour':   cfg.window_end_hour,
        })


@method_decorator(login_required, name='dispatch')
class ScanningRecentTilesAPI(View):
    """GET /scanning/api/recent-tiles/ — last N scanned tiles as GeoJSON.
    Optional ?date=YYYY-MM-DD to filter to tiles scanned on a specific day.
    """

    def get(self, request):
        if not (hasattr(request.user, 'profile') and request.user.profile.role in ('system_admin', 'agency_admin')):
            return JsonResponse({'error': 'Administrator access required.'}, status=403)
        limit     = min(int(request.GET.get('limit', 100)), 500)
        date_str  = request.GET.get('date')

        qs = ScanTile.objects.filter(is_active=True, last_scanned_at__isnull=False)

        if date_str:
            try:
                from datetime import date as date_cls
                filter_date = date_cls.fromisoformat(date_str)
                qs = qs.filter(last_scanned_at__date=filter_date)
            except ValueError:
                pass

        tiles = qs.order_by('-last_scanned_at')[:limit]

        features = []
        for tile in tiles:
            try:
                geom = json.loads(tile.geometry.geojson)
            except Exception:
                continue
            features.append({
                'type': 'Feature',
                'geometry': geom,
                'properties': {
                    'id':              str(tile.id),
                    'name':            tile.name,
                    'priority':        tile.priority,
                    'scan_count':      tile.scan_count,
                    'last_scanned_at': tile.last_scanned_at.isoformat() if tile.last_scanned_at else None,
                },
            })

        return JsonResponse({'type': 'FeatureCollection', 'features': features})


@method_decorator(login_required, name='dispatch')
class ScanningDetectionsAPI(View):
    """GET /scanning/api/detections/ — automated detections as GeoJSON for the map.
    Optional ?date=YYYY-MM-DD to filter to detections from a specific day.
    """

    def get(self, request):
        if not (hasattr(request.user, 'profile') and request.user.profile.role in ('system_admin', 'agency_admin')):
            return JsonResponse({'error': 'Administrator access required.'}, status=403)
        from apps.detections.models import DetectedSite
        from django.conf import settings as django_settings

        limit    = min(int(request.GET.get('limit', 200)), 1000)
        date_str = request.GET.get('date')

        qs = DetectedSite.objects.filter(job__source='automated', centroid__isnull=False)

        if date_str:
            try:
                from datetime import date as date_cls
                filter_date = date_cls.fromisoformat(date_str)
                qs = qs.filter(detection_date=filter_date)
            except ValueError:
                pass

        # Order by detection_date DESC so recently-confirmed sites (including
        # deduplicated ones whose detection_date was updated today) appear first
        # and are not cut off by the limit.
        sites = qs.select_related('job', 'region').order_by('-detection_date', '-confidence_score')[:limit]

        media = django_settings.MEDIA_URL

        def img_url(path):
            return (media + path) if path else None

        features = []
        for site in sites:
            centroid = site.centroid
            lat, lng = centroid.y, centroid.x
            job = site.job

            patch_images = None
            if job and any([job.img_false_color, job.img_prediction_mask,
                            job.img_probability_heatmap, job.img_overlay]):
                patch_images = {
                    'false_color':         img_url(job.img_false_color),
                    'prediction_mask':     img_url(job.img_prediction_mask),
                    'probability_heatmap': img_url(job.img_probability_heatmap),
                    'overlay':             img_url(job.img_overlay),
                }

            features.append({
                'type': 'Feature',
                'id':   str(site.id),
                'geometry': {'type': 'Point', 'coordinates': [lng, lat]},
                'properties': {
                    'id':               str(site.id),
                    'confidence_pct':   round(site.confidence_score * 100, 1),
                    'area_hectares':    round(site.area_hectares, 2),
                    'legal_status':     site.legal_status,
                    'detection_date':   str(site.detection_date),
                    'region':           site.region.name if site.region else None,
                    'patch_images':     patch_images,
                    'google_earth_url': (
                        f'https://earth.google.com/web/@{lat:.6f},{lng:.6f},500a,400d,35y,0h,0t,0r'
                    ),
                },
            })

        return JsonResponse({'type': 'FeatureCollection', 'features': features})


@method_decorator(login_required, name='dispatch')
class ScanningTileDetailAPI(View):
    """GET /scanning/api/tile-detail/?lat=X&lng=Y
    Returns scan history and detections for the tile containing the given point.
    """

    def get(self, request):
        if not (hasattr(request.user, 'profile') and request.user.profile.role in ('system_admin', 'agency_admin')):
            return JsonResponse({'error': 'Administrator access required.'}, status=403)
        from apps.jobs.models import Job
        from apps.detections.models import DetectedSite
        from django.contrib.gis.geos import Point

        try:
            lat = float(request.GET['lat'])
            lng = float(request.GET['lng'])
        except (KeyError, ValueError, TypeError):
            return JsonResponse({'error': 'lat and lng query params required'}, status=400)

        point = Point(lng, lat, srid=4326)
        tile  = ScanTile.objects.filter(geometry__contains=point).first()

        if not tile:
            return JsonResponse({'error': 'No tile found at this location'}, status=404)

        # Last 20 automated jobs for this tile (via scan_tile FK)
        jobs = (
            Job.objects
            .filter(scan_tile=tile, source='automated')
            .order_by('-created_at')
            [:20]
        )

        # Detections are queried spatially — centroid within the tile geometry.
        # This catches cases where the job's scan_tile FK may not be set but the
        # detection centroid still falls inside this tile's bounds.
        det_sites = (
            DetectedSite.objects
            .filter(job__source='automated', centroid__within=tile.geometry)
            .select_related('job')
            .order_by('-detection_date', '-confidence_score')
            [:30]
        )

        scan_history = [
            {
                'date':   job.created_at.strftime('%Y-%m-%d %H:%M'),
                'job_id': str(job.id),
                'status': job.status,
            }
            for job in jobs
        ]

        detections = [
            {
                'confidence_pct':  round(ds.confidence_score * 100, 1),
                'area_hectares':   round(ds.area_hectares, 2),
                'legal_status':    ds.legal_status,
                'detection_date':  str(ds.detection_date),
            }
            for ds in det_sites
        ]

        return JsonResponse({
            'id':              str(tile.id),
            'name':            tile.name,
            'priority':        tile.priority,
            'scan_count':      tile.scan_count,
            'last_scanned_at': tile.last_scanned_at.isoformat() if tile.last_scanned_at else None,
            'scan_history':    scan_history,
            'detections':      detections,
        })


@method_decorator(login_required, name='dispatch')
class ScanningForceScanAPI(View):
    """POST /scanning/api/force-scan/ (staff only)
    Immediately queues a high-priority scan job for a given tile.
    Body: {tile_id: "uuid"}
    """

    def post(self, request):
        if not (request.user.is_authenticated and hasattr(request.user, 'profile') and request.user.profile.role in ('system_admin', 'agency_admin')):
            return JsonResponse({'error': 'Administrator access required.'}, status=403)

        try:
            body    = json.loads(request.body)
            tile_id = body['tile_id']
        except (json.JSONDecodeError, KeyError, ValueError):
            return JsonResponse({'error': 'tile_id required in JSON body'}, status=400)

        try:
            tile = ScanTile.objects.get(id=tile_id)
        except (ScanTile.DoesNotExist, Exception):
            return JsonResponse({'error': 'Tile not found'}, status=404)

        try:
            from apps.jobs.services import JobService
            from apps.core.tasks import run_detection_task

            now        = timezone.now()
            end_date   = now.date()
            start_date = end_date.replace(year=end_date.year - 2)

            # Agency admin force-scans are tagged with their org so the job
            # appears in their org-scoped dashboard view.
            org = getattr(getattr(request.user, 'profile', None), 'organisation', None)

            job = JobService.create_job(
                aoi_geometry=tile.geometry,
                start_date=str(start_date),
                end_date=str(end_date),
                source='automated',
                scan_tile_id=str(tile.id),
                organisation=org,
            )
            run_detection_task.apply_async(
                args=[str(job.id)],
                queue='priority',
                priority=9,
            )
            logger.info(f'Force scan queued for tile {tile.name} by {request.user} → job {job.id}')
            return JsonResponse({'job_id': str(job.id), 'tile': tile.name})
        except Exception as exc:
            logger.error(f'Force scan failed for tile {tile_id}: {exc}')
            return JsonResponse({'error': str(exc)}, status=500)


@method_decorator(login_required, name='dispatch')
class ScanningExportAPI(View):
    """GET /scanning/api/export/?format=geojson|csv
    Downloads today's automated detections as a file.
    """

    def get(self, request):
        if not (hasattr(request.user, 'profile') and request.user.profile.role in ('system_admin', 'agency_admin')):
            return JsonResponse({'error': 'Administrator access required.'}, status=403)
        from apps.detections.models import DetectedSite

        fmt   = request.GET.get('format', 'geojson').lower()
        today = timezone.now().date()

        sites = (
            DetectedSite.objects
            .filter(job__source='automated', detection_date=today, centroid__isnull=False)
            .select_related('region')
            .order_by('-detection_date', '-confidence_score')
        )

        filename_base = f'geowatch-detections-{today}'

        if fmt == 'csv':
            output = StringIO()
            writer = csv.writer(output)
            writer.writerow(['lat', 'lng', 'confidence_pct', 'area_hectares',
                             'legal_status', 'detection_date', 'region'])
            for site in sites:
                c = site.centroid
                writer.writerow([
                    round(c.y, 6), round(c.x, 6),
                    round(site.confidence_score * 100, 1),
                    round(site.area_hectares, 2),
                    site.legal_status,
                    str(site.detection_date),
                    site.region.name if site.region else '',
                ])
            response = HttpResponse(output.getvalue(), content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="{filename_base}.csv"'
            return response

        # Default: GeoJSON
        features = []
        for site in sites:
            c = site.centroid
            features.append({
                'type': 'Feature',
                'id':   str(site.id),
                'geometry': {'type': 'Point', 'coordinates': [round(c.x, 6), round(c.y, 6)]},
                'properties': {
                    'confidence_pct':  round(site.confidence_score * 100, 1),
                    'area_hectares':   round(site.area_hectares, 2),
                    'legal_status':    site.legal_status,
                    'detection_date':  str(site.detection_date),
                    'region':          site.region.name if site.region else None,
                },
            })

        response = JsonResponse({'type': 'FeatureCollection', 'features': features})
        response['Content-Disposition'] = f'attachment; filename="{filename_base}.geojson"'
        return response
