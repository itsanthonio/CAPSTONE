"""
API ViewSets for connecting Frontend to Detection Orchestrator.

This module provides DRF ViewSets for:
- Job creation with immediate async processing
- Job status tracking with progress monitoring
- Result retrieval in GeoJSON format
"""

import logging
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated, BasePermission


from apps.accounts.permissions import IsAdminRole, IsSystemAdmin, IsAgencyAdmin, OrgScopedMixin

from apps.jobs.models import Job
from apps.results.models import Result
from apps.api.serializers import (
    JobSerializer,
    ResultSerializer,
    JobCreateSerializer,
    StatusSerializer
)

logger = logging.getLogger(__name__)


def _audit(user, action, obj_id, **detail):
    """Write a single AuditLog row. Silently no-ops if the table isn't ready."""
    try:
        from apps.detections.models import AuditLog
        AuditLog.objects.create(
            user=user if (user and user.is_authenticated) else None,
            action=action,
            object_id=str(obj_id),
            detail=detail,
        )
    except Exception:
        pass


def _is_admin(user):
    """Return True if the user has any admin role."""
    try:
        return user.is_authenticated and user.profile.role in ('system_admin', 'agency_admin')
    except Exception:
        return False


def _invalidate_stats_cache():
    """Delete all dashboard_stats cache entries (all org scopes + global)."""
    from django.core.cache import cache
    from apps.accounts.models import Organisation
    org_ids = ['global'] + [str(pk) for pk in Organisation.objects.values_list('pk', flat=True)]
    keys = [
        f'dashboard_stats_{w}_{td}_{oid}'
        for w in range(2, 53)
        for td in (7, 30, 90, 365)
        for oid in org_ids
    ]
    cache.delete_many(keys)


from rest_framework.throttling import UserRateThrottle


class JobCreateThrottle(UserRateThrottle):
    """Tight per-user throttle for job creation — each job burns GEE quota."""
    scope = 'job_create'


class JobViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    org_field = 'organisation'
    """
    ViewSet for Job model with creation and status tracking.

    Provides endpoints for:
    - Creating new detection jobs
    - Retrieving job status with progress
    - Listing all jobs
    """

    queryset = Job.objects.all()
    serializer_class = JobSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        # Job UUIDs are unguessable and only returned to the creator at scan
        # time, so bypassing org-scoped filtering for direct pk lookups is safe.
        # This ensures status polling never 404s regardless of org scoping.
        from django.shortcuts import get_object_or_404
        obj = get_object_or_404(Job, pk=self.kwargs['pk'])
        self.check_object_permissions(self.request, obj)
        return obj

    def get_throttles(self):
        if self.action == 'create':
            return [JobCreateThrottle()]
        return super().get_throttles()

    def get_serializer_class(self):
        """
        Return appropriate serializer based on action.

        Returns:
            Serializer class
        """
        if self.action == 'create':
            return JobCreateSerializer
        return JobSerializer

    def create(self, request, *args, **kwargs):
        """
        Create new job and trigger detection pipeline.

        Args:
            request: HTTP request object

        Returns:
            Response: Created job with immediate processing
        """
        try:
            serializer = self.get_serializer_class()(data=request.data)
            serializer.is_valid(raise_exception=True)

            # Create job within transaction
            with transaction.atomic():
                user = request.user if request.user.is_authenticated else None
                org = getattr(getattr(user, 'profile', None), 'organisation', None) if user else None
                job = serializer.save(created_by=user, organisation=org)

            logger.info(f"Created new job {job.id} via API")
            _audit(request.user, 'job.created', job.id, status=job.status, source=job.source)

            # Trigger detection pipeline asynchronously
            try:
                # Import here to avoid circular imports
                from apps.core.tasks import run_detection_task
                # Manual scans get priority=9 (highest) so they always jump
                # ahead of automated tile scans (priority=5) in the queue
                task_result = run_detection_task.apply_async(
                    args=[str(job.id)],
                    queue='priority',
                    priority=9,
                )

                logger.info(f"Triggered detection pipeline for job {job.id}")

                # Return immediate response with job details
                response_serializer = JobSerializer(job)
                return Response(
                    response_serializer.data,
                    status=status.HTTP_201_CREATED,
                    headers={
                        'X-Task-ID': str(task_result.id),
                        'X-Job-ID': str(job.id)
                    }
                )

            except Exception as e:
                logger.exception(f"Failed to trigger pipeline for job {job.id}")

                # Update job status to failed
                job.status = Job.Status.FAILED
                job.save()

                return Response(
                    {
                        'error': 'Failed to start detection pipeline.',
                        'job_id': str(job.id)
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

        except Exception as e:
            logger.exception("Job creation failed")
            return Response(
                {'error': 'Job creation failed. Please check your request and try again.'},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['get'])
    def status(self, request, pk=None):
        """
        Get detailed status for a specific job.

        Args:
            request: HTTP request object
            pk: Job UUID

        Returns:
            Response: Job status with progress details
        """
        try:
            job = get_object_or_404(Job, id=pk)
            serializer = JobSerializer(job)

            # Add additional status information
            data = serializer.data
            data['pipeline_stages'] = {
                'validating': job.status == Job.Status.VALIDATING,
                'exporting': job.status == Job.Status.EXPORTING,
                'preprocessing': job.status == Job.Status.PREPROCESSING,
                'inferring': job.status == Job.Status.INFERRING,
                'postprocessing': job.status == Job.Status.POSTPROCESSING,
                'storing': job.status == Job.Status.STORING,
                'completed': job.status == Job.Status.COMPLETED,
                'failed': job.status == Job.Status.FAILED
            }

            return Response(data)

        except Job.DoesNotExist:
            return Response(
                {
                    'error': 'Job not found',
                    'job_id': pk
                },
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Failed to get job status: {str(e)}")
            return Response(
                {
                    'error': 'Failed to retrieve job status',
                    'details': str(e)
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['get'])
    def active(self, request):
        """
        Return all in-progress jobs for the current user so the live map
        can resume polling after the user navigates away and comes back.
        """
        terminal = {Job.Status.COMPLETED, Job.Status.FAILED, Job.Status.CANCELLED}
        qs = Job.objects.filter(
            created_by=request.user,
            source=Job.Source.MANUAL,
        ).exclude(status__in=terminal).order_by('-created_at')
        data = [
            {
                'id': str(j.id),
                'name': j.name,
                'status': j.status,
                'aoi_geometry': j.aoi_geometry.geojson,
            }
            for j in qs
        ]
        return Response(data)


class ResultViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    org_field = 'job__organisation'
    """
    ViewSet for Result model with GeoJSON output.

    Provides endpoints for:
    - Retrieving detection results for completed jobs
    - Listing all results
    """

    serializer_class = ResultSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """
        Filter results by job_id if provided.

        Returns:
            QuerySet: Filtered results
        """
        self.queryset = Result.objects.all()
        queryset = super().get_queryset()  # applies OrgScopedMixin
        job_id = self.request.query_params.get('job_id')
        if job_id:
            queryset = queryset.filter(job__id=job_id)
        return queryset

    def retrieve(self, request, *args, **kwargs):
        """
        Retrieve result with GeoJSON validation.

        Args:
            request: HTTP request object

        Returns:
            Response: Result with GeoJSON FeatureCollection
        """
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance)

            # Validate GeoJSON structure
            geojson_data = serializer.data.get('geojson', {})
            if not geojson_data.get('type') == 'FeatureCollection':
                logger.warning(f"Invalid GeoJSON structure for result {instance.id}")

            return Response(serializer.data)

        except Exception as e:
            logger.error(f"Failed to retrieve result: {str(e)}")
            return Response(
                {
                    'error': 'Failed to retrieve result',
                    'details': str(e)
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def get_serializer(self, *args, **kwargs):
        kwargs.setdefault('context', {'request': self.request})
        return ResultSerializer(*args, **kwargs)

    @action(detail=True, methods=['get'])
    def by_job(self, request, job_id=None):
        """
        Get all results for a specific job.

        Args:
            request: HTTP request object
            job_id: Job UUID

        Returns:
            Response: List of results for the job
        """
        try:
            # Validate job exists and is completed
            job = get_object_or_404(Job, id=job_id)

            if job.status != Job.Status.COMPLETED:
                return Response(
                    {
                        'error': 'Job not completed',
                        'job_id': job_id,
                        'current_status': job.status,
                        'required_status': 'COMPLETED'
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Get results for this job
            results = Result.objects.filter(job_id=job_id)
            serializer = ResultSerializer(results, many=True)

            return Response({
                'job_id': job_id,
                'job_status': job.status,
                'total_results': len(results),
                'results': serializer.data
            })

        except Job.DoesNotExist:
            return Response(
                {
                    'error': 'Job not found',
                    'job_id': job_id
                },
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Failed to get results for job {job_id}: {str(e)}")
            return Response(
                {
                    'error': 'Failed to retrieve results',
                    'details': str(e)
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class DetectedSiteViewSet(OrgScopedMixin, viewsets.ReadOnlyModelViewSet):
    org_field = 'job__organisation'
    """
    Read-only ViewSet for DetectedSite.
    Provides site detail and timelapse frames for the map panel.
    """
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        from apps.detections.models import DetectedSite
        self.queryset = DetectedSite.objects.select_related(
            'intersecting_concession', 'region', 'model_run', 'satellite_imagery', 'job'
        ).all()
        return super().get_queryset()

    def list(self, request, *args, **kwargs):
        """Return detected sites as a GeoJSON FeatureCollection.

        Optional pagination: ?page=1&per_page=200
        Without those params returns up to 2 000 sites (suitable for the map).
        """
        import json as _json
        from django.db.models import Subquery, OuterRef
        from apps.detections.models import Region as _Region
        qs = self.get_queryset().filter(geometry__isnull=False).order_by('-created_at')

        # Annotate each site with the district name and parent Ghana region name
        # from the admin_district spatial layer.
        _district_name = Subquery(
            _Region.objects.filter(
                geometry__contains=OuterRef('centroid'),
                is_active=True,
                region_type='admin_district',
            ).values('name')[:1]
        )
        _district_parent = Subquery(
            _Region.objects.filter(
                geometry__contains=OuterRef('centroid'),
                is_active=True,
                region_type='admin_district',
            ).values('district')[:1]
        )
        qs = qs.annotate(_district_name=_district_name, _district_parent=_district_parent)

        page     = request.query_params.get('page')
        per_page = min(int(request.query_params.get('per_page', 200)), 500)
        total    = qs.count()

        if page is not None:
            page = max(1, int(page))
            qs   = qs[(page - 1) * per_page: page * per_page]
        else:
            qs = qs[:2000]  # map mode: all sites up to 2 000

        features = []
        for site in qs:
            centroid = site.centroid
            features.append({
                'type': 'Feature',
                'geometry': _json.loads(site.geometry.geojson),
                'properties': {
                    'site_id': str(site.id),
                    'confidence_score': site.confidence_score,
                    'area_hectares': round(site.area_hectares, 2),
                    'legal_status': site.legal_status,
                    'detection_date': str(site.detection_date),
                    'region': site._district_parent or None,
                    'district': site._district_name or None,
                    'lat': round(centroid.y, 4) if centroid else None,
                    'lng': round(centroid.x, 4) if centroid else None,
                }
            })
        resp = {'type': 'FeatureCollection', 'features': features, 'total': total}
        if page is not None:
            resp['page']        = page
            resp['per_page']    = per_page
            resp['total_pages'] = max(1, (total + per_page - 1) // per_page)
        return Response(resp)

    def retrieve(self, request, *args, **kwargs):
        from apps.detections.models import DetectedSite
        site = self.get_queryset().filter(pk=kwargs['pk']).first()
        if not site:
            return Response({'error': 'Not found.'}, status=404)

        concession = None
        if site.intersecting_concession:
            concession = {
                'license_number': site.intersecting_concession.license_number,
                'name': site.intersecting_concession.concession_name,
                'holder': site.intersecting_concession.holder_name,
                'license_type': site.intersecting_concession.get_license_type_display(),
                'overlap_pct': site.concession_overlap_pct,
            }

        # Build patch image URLs — prefer per-site crops, fall back to job-level
        from django.conf import settings
        def _media_url(rel_path):
            if not rel_path:
                return None
            return request.build_absolute_uri(settings.MEDIA_URL + rel_path)

        patch_images = None
        # Try site-level images first (per-polygon crop)
        if site.img_false_color:
            patch_images = {
                'false_color':         _media_url(site.img_false_color),
                'prediction_mask':     _media_url(site.img_prediction_mask),
                'probability_heatmap': _media_url(site.img_probability_heatmap),
                'overlay':             _media_url(site.img_overlay),
            }
        # Fall back to whole-AOI job images for older scans
        if not patch_images:
            job = getattr(site, 'job', None)
            if job and job.img_false_color:
                patch_images = {
                    'false_color':         _media_url(job.img_false_color),
                    'prediction_mask':     _media_url(job.img_prediction_mask),
                    'probability_heatmap': _media_url(job.img_probability_heatmap),
                    'overlay':             _media_url(job.img_overlay),
                }

        # Spatial district lookup — fetch name (district) and district field (Ghana region)
        district = None
        region = None
        if site.centroid:
            from apps.detections.models import Region as _Region
            dist_obj = _Region.objects.filter(
                geometry__contains=site.centroid,
                is_active=True,
                region_type='admin_district',
            ).values('name', 'district').first()
            if dist_obj:
                district = dist_obj['name']
                region = dist_obj['district'] or None

        return Response({
            'id': str(site.id),
            'scan_date': site.created_at.date().isoformat(),
            'detection_date': site.detection_date.isoformat(),
            'confidence_score': round(site.confidence_score, 4),
            'confidence_pct': round(site.confidence_score * 100, 1),
            'area_hectares': round(site.area_hectares, 2),
            'legal_status': site.legal_status,
            'legal_status_display': site.get_legal_status_display(),
            'status': site.status,
            'status_display': site.get_status_display(),
            'recurrence_count': site.recurrence_count,
            'first_detected_at': site.first_detected_at.isoformat() if site.first_detected_at else None,
            'concession': concession,
            'region': region,
            'district': district,
            'lat': round(site.centroid.y, 4) if site.centroid else None,
            'lng': round(site.centroid.x, 4) if site.centroid else None,
            'patch_images': patch_images,
        })

    @action(detail=True, methods=['get'], url_path='timelapse')
    def timelapse(self, request, pk=None):
        """Return ordered timelapse frames for a detected site."""
        from apps.detections.models import DetectedSite, SiteTimelapse
        site = self.get_queryset().filter(pk=pk).first()
        if not site:
            return Response({'error': 'Not found.'}, status=404)
        frames = SiteTimelapse.objects.filter(
            detected_site=site
        ).order_by('year').values(
            'year', 'acquisition_period', 'thumbnail_url',
            'cloud_cover_pct', 'mean_ndvi', 'mean_bsi'
        )
        return Response({
            'site_id': str(site.id),
            'frames': list(frames),
            'frame_count': len(frames),
        })

    @action(detail=True, methods=['get'], url_path='history')
    def history(self, request, pk=None):
        """Return all detection snapshots for a site, oldest first."""
        from apps.detections.models import DetectedSite, DetectionSnapshot
        from django.conf import settings
        site = self.get_queryset().filter(pk=pk).first()
        if not site:
            return Response({'error': 'Not found.'}, status=404)

        def _media_url(rel_path):
            if not rel_path:
                return None
            return request.build_absolute_uri(settings.MEDIA_URL + rel_path)

        snapshots = DetectionSnapshot.objects.filter(site=site).order_by('occurrence_number')
        data = []
        for snap in snapshots:
            images = None
            if snap.img_false_color or snap.img_prediction_mask or snap.img_probability_heatmap or snap.img_overlay:
                images = {
                    'false_color':         _media_url(snap.img_false_color),
                    'prediction_mask':     _media_url(snap.img_prediction_mask),
                    'probability_heatmap': _media_url(snap.img_probability_heatmap),
                    'overlay':             _media_url(snap.img_overlay),
                }
            elif snap.job:
                job = snap.job
                if job.img_false_color:
                    images = {
                        'false_color':         _media_url(job.img_false_color),
                        'prediction_mask':     _media_url(job.img_prediction_mask),
                        'probability_heatmap': _media_url(job.img_probability_heatmap),
                        'overlay':             _media_url(job.img_overlay),
                    }
            data.append({
                'occurrence_number': snap.occurrence_number,
                'detection_date': snap.detection_date.isoformat(),
                'confidence_score': round(snap.confidence_score, 4),
                'confidence_pct': round(snap.confidence_score * 100, 1),
                'area_hectares': round(snap.area_hectares, 2),
                'job_id': str(snap.job_id) if snap.job_id else None,
                'images': images,
            })
        return Response({
            'site_id': str(site.id),
            'total_detections': len(data),
            'snapshots': data,
        })


class ConcessionGeoJSONView(viewsets.ReadOnlyModelViewSet):
    """
    Returns legal concessions as GeoJSON for map display.
    Lightweight — only geometry + label fields, no heavy data.
    """
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        from apps.detections.models import LegalConcession
        return LegalConcession.objects.filter(is_active=True).only(
            'id', 'license_number', 'concession_name', 'holder_name',
            'license_type', 'geometry'
        )

    def list(self, request, *args, **kwargs):
        from apps.detections.models import LegalConcession
        from django.contrib.gis.serializers.geojson import Serializer as GeoJSONSerializer
        import json

        qs = self.get_queryset()

        features = []
        for c in qs:
            if c.geometry:
                features.append({
                    'type': 'Feature',
                    'geometry': json.loads(c.geometry.geojson),
                    'properties': {
                        'id': str(c.id),
                        'license_number': c.license_number,
                        'name': c.concession_name,
                        'holder': c.holder_name,
                        'license_type': c.get_license_type_display(),
                    }
                })

        return Response({
            'type': 'FeatureCollection',
            'features': features,
        })


class RegionGeoJSONView(viewsets.ViewSet):
    """
    Returns Region boundaries (water bodies, protected forests) as GeoJSON.
    GET /api/regions/?type=water_body
    GET /api/regions/?type=protected_forest
    """
    permission_classes = [IsAuthenticated]

    def list(self, request, *args, **kwargs):
        from apps.detections.models import Region
        import json

        region_type = request.query_params.get('type')
        qs = Region.objects.filter(is_active=True)
        if region_type:
            qs = qs.filter(region_type=region_type)

        features = []
        for r in qs:
            if r.geometry:
                features.append({
                    'type': 'Feature',
                    'geometry': json.loads(r.geometry.geojson),
                    'properties': {
                        'id': str(r.id),
                        'name': r.name,
                        'region_type': r.region_type,
                        'district': r.district,
                    }
                })

        return Response({'type': 'FeatureCollection', 'features': features})


class AlertViewSet(viewsets.ViewSet):
    """
    Alert listing and status-change actions.
    GET  /api/alerts/          — list with filter params: status, severity, alert_type
    GET  /api/alerts/{id}/     — detail (any authenticated user)
    POST /api/alerts/{id}/acknowledge/  — admin only
    POST /api/alerts/{id}/dismiss/      — admin only
    POST /api/alerts/{id}/dispatch/     — admin only
    POST /api/alerts/{id}/resolve/      — admin or assigned inspector
    """
    # Default: admin-only.  list/retrieve override to IsAuthenticated below.
    permission_classes = [IsAdminRole]

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [IsAuthenticated()]
        if self.action == 'resolve':
            # resolve is handled per-object inside the method; require login only
            return [IsAuthenticated()]
        return [IsAdminRole()]

    def get_queryset(self):
        from apps.detections.models import Alert
        from django.db.models import Q
        qs = Alert.objects.select_related(
            'detected_site', 'detected_site__region', 'detected_site__job', 'assigned_to'
        )
        status   = self.request.query_params.get('status')
        severity = self.request.query_params.get('severity')
        atype    = self.request.query_params.get('alert_type')
        source   = self.request.query_params.get('source')
        if status:   qs = qs.filter(status=status)
        if severity: qs = qs.filter(severity=severity)
        if atype:    qs = qs.filter(alert_type=atype)
        if source in ('manual', 'automated'):
            qs = qs.filter(detected_site__job__source=source)
        # Role-based scoping
        try:
            role = self.request.user.profile.role
            if role == 'agency_admin':
                org = self.request.user.profile.organisation
                if org is None:
                    return qs.none()
                qs = qs.filter(
                    Q(detected_site__job__organisation=org) |
                    Q(detected_site__job__organisation__isnull=True, detected_site__job__source='automated')
                )
            elif role == 'inspector':
                # Inspectors only see alerts assigned to them
                qs = qs.filter(assigned_to=self.request.user)
        except Exception:
            pass
        return qs

    def list(self, request, *args, **kwargs):
        from apps.detections.models import Alert
        qs      = self.get_queryset()

        ALLOWED_ORDERINGS = {
            '-created_at', 'created_at',
            '-detected_site__confidence_score', 'detected_site__confidence_score',
        }
        ordering = request.query_params.get('ordering', '-created_at')
        if ordering in ALLOWED_ORDERINGS:
            qs = qs.order_by(ordering)

        page    = int(request.query_params.get('page', 1))
        per_page = min(int(request.query_params.get('per_page', 20)), 500)
        total   = qs.count()
        alerts  = qs[(page - 1) * per_page: page * per_page]

        from apps.detections.models import Region as _Region

        def _site_region(site):
            if not site.centroid:
                return None
            dist_obj = _Region.objects.filter(
                geometry__contains=site.centroid,
                is_active=True,
                region_type='admin_district',
            ).values('district').first()
            return (dist_obj['district'] or None) if dist_obj else None

        def fmt(a):
            site = a.detected_site
            centroid = site.centroid
            return {
                'id': str(a.id),
                'short_id': str(a.id)[:8].upper(),
                'title': a.title,
                'description': a.description,
                'alert_type': a.alert_type,
                'alert_type_display': a.get_alert_type_display(),
                'severity': a.severity,
                'severity_display': a.get_severity_display(),
                'status': a.status,
                'status_display': a.get_status_display(),
                'created_at': a.created_at.strftime('%Y-%m-%d %H:%M'),
                'acknowledged_at': a.acknowledged_at.strftime('%Y-%m-%d %H:%M') if a.acknowledged_at else None,
                'resolved_at': a.resolved_at.strftime('%Y-%m-%d %H:%M') if a.resolved_at else None,
                'site': {
                    'id': str(site.id),
                    'confidence_pct': round(site.confidence_score * 100, 1),
                    'area_hectares': round(site.area_hectares, 2),
                    'legal_status': site.legal_status,
                    'detection_date': str(site.detection_date),
                    'recurrence_count': site.recurrence_count,
                    'region': _site_region(site),
                    'lat': round(centroid.y, 4) if centroid else None,
                    'lng': round(centroid.x, 4) if centroid else None,
                },
                'assigned_to': a.assigned_to.get_full_name() or a.assigned_to.username if a.assigned_to else None,
                'source': a.detected_site.job.source,
            }

        return Response({
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': max(1, (total + per_page - 1) // per_page),
            'results': [fmt(a) for a in alerts],
        })

    def retrieve(self, request, *args, **kwargs):
        from apps.detections.models import Alert
        from apps.accounts.models import InspectorAssignment
        a = self.get_queryset().select_related(
            'detected_site', 'detected_site__region', 'detected_site__job'
        ).filter(pk=kwargs['pk']).first()
        if not a:
            return Response({'error': 'Not found.'}, status=404)
        site = a.detected_site
        centroid = site.centroid

        # Pull structured field verification data from InspectorAssignment
        field_verification = None
        try:
            assignment = InspectorAssignment.objects.filter(
                alert_id=a.id,
                status=InspectorAssignment.Status.RESOLVED
            ).select_related('inspector__user').order_by('-completed_at').first()
            if assignment:
                from django.conf import settings as _settings
                photo_urls = [
                    f"{_settings.MEDIA_URL}{p}"
                    for p in (assignment.evidence_photos or [])
                ]
                field_verification = {
                    'outcome': assignment.outcome,
                    'outcome_display': dict(InspectorAssignment.Outcome.choices).get(
                        assignment.outcome, assignment.outcome
                    ),
                    'inspector_name': (
                        assignment.inspector.user.get_full_name()
                        or assignment.inspector.user.username
                    ),
                    'visit_date': assignment.visit_date.isoformat() if assignment.visit_date else None,
                    'notes': assignment.notes,
                    'completed_at': (
                        assignment.completed_at.strftime('%Y-%m-%d %H:%M')
                        if assignment.completed_at else None
                    ),
                    'photos': photo_urls,
                }
        except Exception:
            pass

        # Build patch image URLs — prefer per-site crops, fall back to job-level
        from django.conf import settings as _settings
        def _murl(rel):
            return request.build_absolute_uri(_settings.MEDIA_URL + rel) if rel else None

        patch_images = None
        if site.img_false_color:
            patch_images = {
                'false_color':         _murl(site.img_false_color),
                'prediction_mask':     _murl(site.img_prediction_mask),
                'probability_heatmap': _murl(site.img_probability_heatmap),
                'overlay':             _murl(site.img_overlay),
            }
        if not patch_images:
            job = getattr(site, 'job', None)
            if job and job.img_false_color:
                patch_images = {
                    'false_color':         _murl(job.img_false_color),
                    'prediction_mask':     _murl(job.img_prediction_mask),
                    'probability_heatmap': _murl(job.img_probability_heatmap),
                    'overlay':             _murl(job.img_overlay),
                }

        # Spatial region lookup
        from apps.detections.models import Region as _Region
        site_region = None
        if centroid:
            dist_obj = _Region.objects.filter(
                geometry__contains=centroid,
                is_active=True,
                region_type='admin_district',
            ).values('district').first()
            if dist_obj:
                site_region = dist_obj['district'] or None

        return Response({
            'id': str(a.id),
            'title': a.title,
            'description': a.description,
            'alert_type': a.alert_type,
            'alert_type_display': a.get_alert_type_display(),
            'severity': a.severity,
            'severity_display': a.get_severity_display(),
            'status': a.status,
            'status_display': a.get_status_display(),
            'created_at': a.created_at.isoformat(),
            'assigned_to': a.assigned_to.get_full_name() or a.assigned_to.username if a.assigned_to else None,
            'assigned_to_id': a.assigned_to.username if a.assigned_to else None,
            'field_verification': field_verification,
            'site': {
                'id': str(site.id),
                'confidence_pct': round(site.confidence_score * 100, 1),
                'area_hectares': round(site.area_hectares, 2),
                'legal_status': site.legal_status,
                'detection_date': str(site.detection_date),
                'recurrence_count': site.recurrence_count,
                'region': site_region,
                'lat': round(centroid.y, 4) if centroid else None,
                'lng': round(centroid.x, 4) if centroid else None,
                'patch_images': patch_images,
            },
        })

    def _change_status(self, request, pk, new_status, extra=None):
        from apps.detections.models import Alert
        from django.utils import timezone
        a = self.get_queryset().filter(pk=pk).first()
        if not a:
            return Response({'error': 'Not found.'}, status=404)
        prev_status = a.status
        a.status = new_status
        if new_status == 'acknowledged':
            a.acknowledged_at = timezone.now()
        elif new_status == 'resolved':
            a.resolved_at = timezone.now()
            a.resolution_notes = request.data.get('resolution_notes', '')
        a.save()
        _invalidate_stats_cache()
        _audit(request.user, f'alert.{new_status}', a.id,
               prev_status=prev_status, severity=a.severity)
        return Response({'id': str(a.id), 'status': a.status})

    @action(detail=True, methods=['post'])
    def acknowledge(self, request, pk=None):
        return self._change_status(request, pk, 'acknowledged')

    @action(detail=True, methods=['post'])
    def dismiss(self, request, pk=None):
        return self._change_status(request, pk, 'dismissed')

    @action(detail=True, methods=['post'])
    def dispatch_alert(self, request, pk=None):
        return self._change_status(request, pk, 'dispatched')

    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        from apps.detections.models import Alert
        a = self.get_queryset().filter(pk=pk).first()
        if not a:
            return Response({'error': 'Not found.'}, status=404)

        # Admins can resolve anything; inspectors can only resolve their own dispatched alerts
        if not _is_admin(request.user):
            if a.assigned_to != request.user:
                return Response(
                    {'error': 'You can only resolve alerts assigned to you.'},
                    status=403
                )

        if a.status == 'dispatched':
            return Response(
                {'error': 'This alert has an inspector dispatched. Resolution must come from their field report.'},
                status=400
            )
        return self._change_status(request, pk, 'resolved')

    @action(detail=True, methods=['post'])
    def assign_inspector(self, request, pk=None):
        """Assign an inspector to an alert"""
        if not _is_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=403)
        
        from apps.detections.models import Alert
        from apps.accounts.models import UserProfile, InspectorAssignment

        alert = self.get_queryset().filter(pk=pk).first()
        if not alert:
            return Response({'error': 'Not found.'}, status=404)
        inspector_id = request.data.get('inspector_id')
        
        if not inspector_id:
            return Response({'error': 'Inspector ID is required.'}, status=400)
        
        try:
            inspector_profile = UserProfile.objects.get(id=inspector_id, role=UserProfile.Role.INSPECTOR)

            # Agency admins can only assign inspectors from their own org
            if request.user.profile.role == 'agency_admin':
                requester_org = request.user.profile.organisation
                if inspector_profile.organisation != requester_org:
                    return Response({'error': 'You can only assign inspectors from your organisation.'}, status=403)

            # Enforce capacity / SLA config (same rules as create_assignment)
            from apps.accounts.views import _resolve_assignment_config
            from datetime import timedelta
            from django.utils import timezone as _tz
            sla_days, max_pending = _resolve_assignment_config(inspector_profile)
            current_pending = InspectorAssignment.objects.filter(
                inspector=inspector_profile,
                status=InspectorAssignment.Status.PENDING,
            ).count()
            if current_pending >= max_pending:
                return Response({
                    'error': f'Inspector already has {current_pending} pending assignment(s) (maximum {max_pending}).'
                }, status=400)
            due_date = (_tz.now() + timedelta(days=sla_days)).date()

            # Update alert
            alert.assigned_to = inspector_profile.user
            alert.status = 'dispatched'
            alert.save()

            # Create inspector assignment (get_or_create guards the unique_together)
            from django.db import IntegrityError
            try:
                assignment, _ = InspectorAssignment.objects.get_or_create(
                    inspector=inspector_profile,
                    alert_id=alert.id,
                    defaults={
                        'status': InspectorAssignment.Status.PENDING,
                        'due_date': due_date,
                    },
                )
            except IntegrityError:
                return Response(
                    {'error': 'This inspector is already assigned to this alert.'},
                    status=409
                )
            
            _audit(request.user, 'alert.assign_inspector', alert.id, 
                   inspector_id=inspector_id, inspector_name=inspector_profile.user.get_full_name())
            
            return Response({
                'id': str(alert.id),
                'status': alert.status,
                'assigned_to': inspector_profile.user.get_full_name() or inspector_profile.user.username
            })
            
        except UserProfile.DoesNotExist:
            return Response({'error': 'Inspector not found.'}, status=404)
        except Exception as e:
            return Response({'error': str(e)}, status=500)

    @action(detail=True, methods=['put', 'patch'])
    def update(self, request, pk=None):
        """Update alert details and associated site information"""
        if not _is_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=403)
        
        from apps.detections.models import Alert
        from apps.accounts.models import InspectorAssignment, UserProfile
        from django.utils import timezone
        from django.contrib.gis.geos import Point

        alert = self.get_queryset().filter(pk=pk).first()
        if not alert:
            return Response({'error': 'Not found.'}, status=404)
        prev_status = alert.status
        prev_assigned_to = alert.assigned_to_id
        
        # Update alert fields
        alert_allowed_fields = ['title', 'description', 'severity', 'alert_type', 'status']
        updated_fields = []
        
        for field in alert_allowed_fields:
            if field in request.data:
                setattr(alert, field, request.data[field])
                updated_fields.append(field)
        
        # Handle status-specific timestamp updates
        if 'status' in request.data:
            if request.data['status'] == 'acknowledged' and prev_status != 'acknowledged':
                alert.acknowledged_at = timezone.now()
            elif request.data['status'] == 'resolved' and prev_status != 'resolved':
                alert.resolved_at = timezone.now()
                alert.resolution_notes = request.data.get('resolution_notes', '')
        
        # Handle inspector assignment
        if 'assigned_to_id' in request.data:
            new_assigned_username = request.data['assigned_to_id']
            if new_assigned_username:
                try:
                    # Find user by username and verify they are an inspector
                    from django.contrib.auth.models import User
                    user = User.objects.get(username=new_assigned_username)
                    inspector_profile = UserProfile.objects.get(
                        user=user, 
                        role=UserProfile.Role.INSPECTOR
                    )
                    alert.assigned_to = user
                    updated_fields.append('assigned_to')
                    
                    # Close existing pending assignments (preserve evidence records)
                    from django.utils import timezone as _tz
                    InspectorAssignment.objects.filter(
                        alert=alert, status=InspectorAssignment.Status.PENDING
                    ).update(
                        status=InspectorAssignment.Status.RESOLVED,
                        notes='Superseded by reassignment.',
                        completed_at=_tz.now(),
                    )

                    # Create new assignment for the new inspector (skip if already exists)
                    InspectorAssignment.objects.get_or_create(
                        inspector=inspector_profile,
                        alert=alert,
                        defaults={'status': InspectorAssignment.Status.PENDING},
                    )
                        
                except (User.DoesNotExist, UserProfile.DoesNotExist):
                    return Response({'error': 'Invalid inspector username.'}, status=400)
            else:
                alert.assigned_to = None
                updated_fields.append('assigned_to')
                # Close (not delete) pending assignments when unassigning — preserve evidence
                from django.utils import timezone as _tz
                InspectorAssignment.objects.filter(
                    alert=alert, status=InspectorAssignment.Status.PENDING
                ).update(
                    status=InspectorAssignment.Status.RESOLVED,
                    notes='Closed by unassignment.',
                    completed_at=_tz.now(),
                )
        
        # Update associated DetectedSite if site data is provided
        site_updated = False
        if any(field in request.data for field in ['latitude', 'longitude', 'confidence_score', 'area_hectares']):
            site = alert.detected_site
            
            # Update location if provided
            if 'latitude' in request.data and 'longitude' in request.data:
                try:
                    lat = float(request.data['latitude'])
                    lng = float(request.data['longitude'])
                    point = Point(lng, lat, srid=4326)
                    site.centroid = point
                    site_updated = True
                except (ValueError, TypeError):
                    return Response({'error': 'Invalid coordinates provided.'}, status=400)
            
            # Update other site fields
            site_fields = ['confidence_score', 'area_hectares']
            for field in site_fields:
                if field in request.data:
                    setattr(site, field, float(request.data[field]))
                    site_updated = True
            
            if site_updated:
                site.save()
        
        if updated_fields or site_updated:
            alert.save()
            _audit(request.user, 'alert.update', alert.id, 
                   updated_fields=updated_fields, site_updated=site_updated)
            
            # Return updated alert data
            return Response({
                'id': str(alert.id),
                'title': alert.title,
                'description': alert.description,
                'alert_type': alert.alert_type,
                'alert_type_display': alert.get_alert_type_display(),
                'severity': alert.severity,
                'severity_display': alert.get_severity_display(),
                'status': alert.status,
                'status_display': alert.get_status_display(),
                'created_at': alert.created_at.isoformat(),
                'assigned_to': alert.assigned_to.get_full_name() or alert.assigned_to.username if alert.assigned_to else None,
                'site': {
                    'lat': alert.detected_site.centroid.y if alert.detected_site.centroid else None,
                    'lng': alert.detected_site.centroid.x if alert.detected_site.centroid else None,
                    'confidence_pct': round(alert.detected_site.confidence_score * 100, 1),
                    'area_hectares': round(alert.detected_site.area_hectares, 2),
                }
            })
        
        return Response({'error': 'No valid fields to update.'}, status=400)

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Return alert counts by status and severity."""
        from apps.detections.models import Alert
        from django.db.models import Count, Q
        qs = Alert.objects.all()
        try:
            role = request.user.profile.role
            if role == 'agency_admin':
                org = request.user.profile.organisation
                if org is None:
                    qs = Alert.objects.none()
                else:
                    qs = qs.filter(
                        Q(detected_site__job__organisation=org) |
                        Q(detected_site__job__organisation__isnull=True, detected_site__job__source='automated')
                    )
            elif role == 'inspector':
                qs = qs.filter(assigned_to=request.user)
        except Exception:
            pass
        rows = qs.values('status', 'severity').annotate(cnt=Count('id'))
        by_status, by_severity = {}, {}
        for row in rows:
            by_status[row['status']] = by_status.get(row['status'], 0) + row['cnt']
            by_severity[row['severity']] = by_severity.get(row['severity'], 0) + row['cnt']
        return Response({
            'total':        sum(by_status.values()),
            'open':         by_status.get('open', 0),
            'acknowledged': by_status.get('acknowledged', 0),
            'dispatched':   by_status.get('dispatched', 0),
            'resolved':     by_status.get('resolved', 0),
            'dismissed':    by_status.get('dismissed', 0),
            'critical':     by_severity.get('critical', 0),
            'high':         by_severity.get('high', 0),
        })

    @action(detail=True, methods=['delete'])
    def delete(self, request, pk=None):
        """Delete an alert (admin only)"""
        if not _is_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=403)
        
        from apps.detections.models import Alert
        alert = self.get_queryset().filter(pk=pk).first()
        if not alert:
            return Response({'error': 'Not found.'}, status=404)

        # Store info for audit before deletion
        alert_info = {
            'id': str(alert.id),
            'title': alert.title,
            'severity': alert.severity,
            'status': alert.status
        }

        # Block deletion if any assignments exist — evidence must be preserved
        from apps.accounts.models import InspectorAssignment
        if InspectorAssignment.objects.filter(alert=alert).exists():
            return Response(
                {'error': 'Cannot delete an alert that has inspector assignments. '
                          'Resolve or reassign all assignments first.'},
                status=409
            )

        alert.delete()
        _audit(request.user, 'alert.delete', alert.id, alert_info=alert_info)

        return Response({'message': 'Alert deleted successfully.'})

    @action(detail=False, methods=['post'])
    def create(self, request):
        """Manually create an alert with associated detection site"""
        if not _is_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=403)
        
        from apps.detections.models import Alert, DetectedSite, Region
        from apps.accounts.models import InspectorAssignment, UserProfile
        from django.contrib.gis.geos import Point, Polygon
        from django.utils import timezone
        import uuid
        
        try:
            data = request.data
            
            # Validate required fields
            required_fields = ['title', 'latitude', 'longitude', 'severity', 'alert_type']
            for field in required_fields:
                if field not in data or not data[field]:
                    return Response({'error': f'{field} is required.'}, status=400)
            
            # Create geometry from lat/lng
            try:
                lat = float(data['latitude'])
                lng = float(data['longitude'])
                point = Point(lng, lat, srid=4326)
                
                # Create a small polygon around the point (100m x 100m)
                buffer_distance = 0.001  # ~100m
                coords = [
                    (lng - buffer_distance, lat - buffer_distance),
                    (lng + buffer_distance, lat - buffer_distance),
                    (lng + buffer_distance, lat + buffer_distance),
                    (lng - buffer_distance, lat + buffer_distance),
                    (lng - buffer_distance, lat - buffer_distance)
                ]
                polygon = Polygon(coords, srid=4326)
                
            except (ValueError, TypeError):
                return Response({'error': 'Invalid coordinates provided.'}, status=400)
            
            # Create DetectedSite first
            detected_site = DetectedSite.objects.create(
                geometry=polygon,
                centroid=point,
                confidence_score=float(data.get('confidence_score', 0.8)),
                area_hectares=float(data.get('area_hectares', 0.01)),
                detection_date=timezone.now().date(),
                status='reviewed',  # Manually created sites are pre-reviewed
                legal_status=data.get('legal_status', 'unknown'),
                region_id=data.get('region_id'),
                recurrence_count=0
            )
            
            # Create Alert
            alert = Alert.objects.create(
                detected_site=detected_site,
                alert_type=data['alert_type'],
                severity=data['severity'],
                status=data.get('status', 'open'),
                title=data['title'],
                description=data.get('description', ''),
                assigned_to_id=data.get('assigned_to_id') if data.get('assigned_to_id') else None
            )
            
            # Create InspectorAssignment if inspector is assigned
            if data.get('assigned_to_id') and alert.status == 'dispatched':
                try:
                    inspector_profile = UserProfile.objects.get(
                        user_id=data['assigned_to_id'], 
                        role=UserProfile.Role.INSPECTOR
                    )
                    InspectorAssignment.objects.create(
                        inspector=inspector_profile,
                        alert=alert,
                        status=InspectorAssignment.Status.PENDING
                    )
                except UserProfile.DoesNotExist:
                    # If inspector not found, don't fail the whole operation
                    pass
            
            _audit(request.user, 'alert.create', alert.id, 
                   title=alert.title, severity=alert.severity)
            
            return Response({
                'id': str(alert.id),
                'title': alert.title,
                'severity': alert.severity,
                'status': alert.status,
                'alert_type': alert.alert_type,
                'message': 'Alert created successfully.'
            })
            
        except Exception as e:
            return Response({'error': str(e)}, status=500)

    @action(detail=False, methods=['post'])
    def bulk_action(self, request):

        from apps.detections.models import Alert
        from django.utils import timezone

        ids    = request.data.get('ids', [])
        verb   = request.data.get('action', '')

        VALID_ACTIONS = ('acknowledged', 'dismissed', 'dispatched')
        if verb not in VALID_ACTIONS:
            return Response({'error': 'Invalid action.'}, status=400)
        if not ids:
            return Response({'error': 'No alert IDs provided.'}, status=400)

        # Each action is only valid from certain source statuses
        SOURCE_STATUSES = {
            'acknowledged': ['open'],
            'dispatched':   ['acknowledged'],
            'dismissed':    ['open', 'acknowledged'],
        }
        qs = self.get_queryset().filter(id__in=ids, status__in=SOURCE_STATUSES[verb])

        now = timezone.now()
        if verb == 'acknowledged':
            updated = qs.update(status='acknowledged', acknowledged_at=now)
        else:
            updated = qs.update(status=verb)

        _invalidate_stats_cache()
        _audit(request.user, f'alert.bulk_{verb}', '',
               alert_ids=ids, updated=updated)
        return Response({'updated': updated})

    @action(detail=False, methods=['post'])
    def bulk_assign_inspector(self, request):

        from apps.detections.models import Alert
        from apps.accounts.models import InspectorAssignment, UserProfile
        from django.utils import timezone
        from datetime import timedelta
        from django.conf import settings as _settings

        alert_ids          = request.data.get('alert_ids', [])
        inspector_username = request.data.get('inspector_username', '')

        if not alert_ids:
            return Response({'error': 'No alert IDs provided.'}, status=400)
        if not inspector_username:
            return Response({'error': 'inspector_username is required.'}, status=400)

        try:
            inspector = UserProfile.objects.get(
                user__username=inspector_username,
                role=UserProfile.Role.INSPECTOR,
            )
        except UserProfile.DoesNotExist:
            return Response({'error': 'Inspector not found.'}, status=404)

        # Agency admins can only assign inspectors from their own org
        try:
            if request.user.profile.role == 'agency_admin':
                requester_org = request.user.profile.organisation
                if inspector.organisation != requester_org:
                    return Response({'error': 'You can only assign inspectors from your organisation.'}, status=403)
        except Exception:
            pass

        # Availability check
        if not inspector.is_available:
            return Response({'error': 'Inspector is not currently available for assignments.'}, status=400)

        # Resolve config: org override → system config → fallback
        from apps.accounts.views import _resolve_assignment_config
        sla_days, max_pending = _resolve_assignment_config(inspector)

        current_pending = InspectorAssignment.objects.filter(
            inspector=inspector,
            status=InspectorAssignment.Status.PENDING,
        ).count()
        remaining_capacity = max_pending - current_pending
        if remaining_capacity <= 0:
            return Response({
                'error': f'Inspector already has {current_pending} pending assignment(s) (maximum {max_pending}).'
            }, status=400)

        due_date = (timezone.now() + timedelta(days=sla_days)).date()

        # Fetch and route-optimise alerts using nearest-neighbour sort
        alerts = list(
            self.get_queryset().filter(
                id__in=alert_ids, status__in=['open', 'acknowledged']
            ).select_related('detected_site')
        )

        # Separate alerts with/without centroids then do greedy nearest-neighbour
        with_coords  = [(a, a.detected_site.centroid.x, a.detected_site.centroid.y)
                        for a in alerts
                        if a.detected_site and a.detected_site.centroid]
        without_coords = [a for a in alerts
                          if not (a.detected_site and a.detected_site.centroid)]

        if len(with_coords) > 1:
            sorted_alerts = []
            remaining = list(with_coords)
            current = remaining.pop(0)
            sorted_alerts.append(current[0])
            while remaining:
                cx, cy = current[1], current[2]
                nearest_idx = min(
                    range(len(remaining)),
                    key=lambda i: (remaining[i][1] - cx) ** 2 + (remaining[i][2] - cy) ** 2
                )
                current = remaining.pop(nearest_idx)
                sorted_alerts.append(current[0])
            alerts = sorted_alerts + without_coords
        else:
            alerts = [item[0] for item in with_coords] + without_coords

        # Cap the list to the inspector's remaining capacity (partial success)
        skipped = max(0, len(alerts) - remaining_capacity)
        alerts = alerts[:remaining_capacity]

        created = 0
        with transaction.atomic():
            for alert in alerts:
                InspectorAssignment.objects.create(
                    alert_id=alert.id,
                    inspector=inspector,
                    status=InspectorAssignment.Status.PENDING,
                    due_date=due_date,
                )
                alert.status = 'dispatched'
                alert.assigned_to = inspector.user
                alert.save(update_fields=['status', 'assigned_to'])
                _audit(request.user, 'alert.assigned', alert.id,
                       inspector=inspector_username)
                created += 1

        _invalidate_stats_cache()

        # Push a single in-app notification summarising all newly assigned alerts
        try:
            from apps.notifications.services import push_notification
            n = created
            push_notification(
                user=inspector.user,
                title=f'New field assignment{"s" if n > 1 else ""} — {n} alert{"s" if n > 1 else ""} assigned',
                body='Open your inspector dashboard to view and submit field reports.',
                link='/dashboard/inspector/',
                kind='assignment',
            )
        except Exception:
            pass

        response_data = {'assigned': created, 'inspector': inspector_username}
        if skipped:
            response_data['skipped'] = skipped
            response_data['warning'] = (
                f'{skipped} alert(s) not assigned — inspector quota reached ({max_pending} max).'
            )
        return Response(response_data)


from django.contrib.auth.decorators import login_required
from django.http import JsonResponse

@login_required
def my_assignments_notifications(request):
    """Returns pending assignment count + 5 most recent for the notification bell (inspector role)."""
    try:
        from apps.accounts.models import InspectorAssignment
        profile = request.user.profile
        qs = InspectorAssignment.objects.filter(
            inspector=profile,
            status=InspectorAssignment.Status.PENDING,
        ).select_related('inspector__user').order_by('-assigned_at')

        total = qs.count()
        items = []
        for a in qs[:5]:
            items.append({
                'id': str(a.id),
                'alert_id': str(a.alert_id),
                'region': None,
                'assigned_at': a.assigned_at.strftime('%Y-%m-%d %H:%M') if a.assigned_at else None,
            })
        return JsonResponse({'count': total, 'items': items})
    except Exception as exc:
        return JsonResponse({'count': 0, 'items': [], 'error': str(exc)})


from django.views.decorators.http import require_GET

@require_GET
def geocode_proxy(request):
    """
    Geocoding with a three-tier lookup:
      1. Local GhanaPlace table (GeoNames bulk import + cached Google results) — instant
      2. Google Geocoding API (if GOOGLE_MAPS_API_KEY set) — best coverage, result cached
      3. Nominatim fallback — no key required
    """
    import requests as _requests
    from decouple import config as _config
    from apps.scanning.models import GhanaPlace
    from django.db.models import Q, Case, When, IntegerField

    q = request.GET.get('q', '').strip()
    if not q or len(q) < 2:
        return JsonResponse({'results': []})

    # ── 1. Local database ────────────────────────────────────────────────────
    local_qs = GhanaPlace.objects.filter(
        Q(name__icontains=q) | Q(ascii_name__icontains=q)
    ).annotate(
        # Exact / starts-with matches first, then contains
        rank=Case(
            When(name__iexact=q,        then=0),
            When(name__istartswith=q,   then=1),
            When(ascii_name__istartswith=q, then=2),
            default=3,
            output_field=IntegerField(),
        )
    ).order_by('rank', '-population')[:8]

    if local_qs.exists():
        results = []
        for p in local_qs:
            label = p.name
            if p.region:
                label += f', {p.region}'
            label += ', Ghana'
            results.append({'display_name': label, 'lat': p.latitude, 'lon': p.longitude})
        return JsonResponse({'results': results})

    # ── 2. Google Places Text Search ─────────────────────────────────────────
    # Uses Places API (not Geocoding) so results have proper place names like
    # "Ashesi University" rather than street addresses like "1 University Ave".
    api_key = _config('GOOGLE_MAPS_API_KEY', default='')
    if api_key:
        try:
            url = (
                'https://maps.googleapis.com/maps/api/place/textsearch/json'
                f'?query={q}+Ghana&key={api_key}&region=gh&language=en'
            )
            resp = _requests.get(url, timeout=5)
            data = resp.json()
            results = []
            to_cache = []
            for item in data.get('results', [])[:6]:
                loc = item['geometry']['location']
                name = item.get('name', q)
                formatted_address = item.get('formatted_address', '')
                # Build display_name so frontend shows name as title and address as subtitle
                display_name = f"{name}, {formatted_address}" if formatted_address else name
                addr_parts = [p.strip() for p in formatted_address.split(',')]
                region_str = addr_parts[-2] if len(addr_parts) >= 2 else ''
                results.append({'display_name': display_name, 'lat': loc['lat'], 'lon': loc['lng']})
                to_cache.append(GhanaPlace(
                    name=name, ascii_name=name,
                    latitude=loc['lat'], longitude=loc['lng'],
                    source='google', region=region_str,
                ))
            if to_cache:
                GhanaPlace.objects.bulk_create(to_cache, ignore_conflicts=True)
            if results:
                return JsonResponse({'results': results})
        except Exception:
            pass

    # ── 3. Nominatim fallback ────────────────────────────────────────────────
    try:
        gh_url = (
            f'https://nominatim.openstreetmap.org/search'
            f'?q={q}&format=json&limit=6&countrycodes=gh&accept-language=en'
        )
        resp = _requests.get(gh_url, timeout=5, headers={'User-Agent': 'SankofaWatch/1.0'})
        data = resp.json()
        if not data:
            global_url = (
                f'https://nominatim.openstreetmap.org/search'
                f'?q={q}&format=json&limit=6&accept-language=en'
            )
            resp = _requests.get(global_url, timeout=5, headers={'User-Agent': 'SankofaWatch/1.0'})
            data = resp.json()
        results = [{'display_name': p['display_name'], 'lat': p['lat'], 'lon': p['lon']} for p in data]
        return JsonResponse({'results': results})
    except Exception as exc:
        return JsonResponse({'results': [], 'error': str(exc)})


@login_required
def parse_aoi_file(request):
    """
    Parse an uploaded zipped shapefile and return its features as GeoJSON.
    GeoJSON files are handled client-side; this endpoint only handles .zip shapefiles.
    Returns up to 20 features with name, area_ha, geometry, valid flag, and reason.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)

    upload = request.FILES.get('file')
    if not upload:
        return JsonResponse({'error': 'No file uploaded.'}, status=400)

    import tempfile, os, json as _json
    import geopandas as _gpd
    from django.contrib.gis.geos import GEOSGeometry

    MAX_FEATURES = 20
    MIN_HA = 10
    MAX_HA = 6000

    try:
        suffix = '.zip' if upload.name.endswith('.zip') else '.geojson'
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            for chunk in upload.chunks():
                tmp.write(chunk)
            tmp_path = tmp.name

        try:
            gdf = _gpd.read_file(tmp_path)
        finally:
            os.unlink(tmp_path)

        if gdf.empty:
            return JsonResponse({'error': 'File contains no features.'}, status=400)

        # Reproject to WGS-84 if needed
        if gdf.crs and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(epsg=4326)

        # Identify name column
        name_col = next(
            (c for c in ['name', 'Name', 'NAME', 'DISTRICT', 'label', 'LABEL', 'id', 'ID']
             if c in gdf.columns),
            None
        )

        features = []
        for i, row in gdf.iterrows():
            if len(features) >= MAX_FEATURES:
                break
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue

            # Area in hectares (reproject to equal-area for accuracy)
            try:
                import pyproj
                from shapely.ops import transform as _transform
                project = pyproj.Transformer.from_crs(
                    'EPSG:4326', 'EPSG:3857', always_xy=True
                ).transform
                area_m2 = _transform(project, geom).area
                area_ha = round(area_m2 / 10000, 2)
            except Exception:
                area_ha = 0

            valid = MIN_HA <= area_ha <= MAX_HA
            reason = None
            if area_ha < MIN_HA:
                reason = f'Too small ({area_ha} ha, min {MIN_HA} ha)'
            elif area_ha > MAX_HA:
                reason = f'Too large ({area_ha} ha, max {MAX_HA} ha)'

            label = str(row[name_col]).strip() if name_col else f'Area {i + 1}'
            if not label or label == 'None':
                label = f'Area {i + 1}'

            geojson_geom = _json.loads(row.geometry.to_json())

            features.append({
                'name': label,
                'area_ha': area_ha,
                'geometry': geojson_geom,
                'valid': valid,
                'reason': reason,
            })

        return JsonResponse({'features': features, 'total': len(gdf)})

    except Exception as e:
        return JsonResponse({'error': f'Could not parse file: {e}'}, status=400)


@login_required
def session_ping(request):
    """
    Lightweight keep-alive endpoint called by the idle-timeout JS when the
    user clicks "Stay logged in".  Because SESSION_SAVE_EVERY_REQUEST is True,
    any authenticated request automatically extends the session; this one just
    makes the intent explicit and returns the remaining TTL so the frontend can
    confirm the session was refreshed.
    """
    from django.conf import settings as _settings
    age = getattr(_settings, 'SESSION_COOKIE_AGE', 28800)
    return JsonResponse({'ok': True, 'session_age': age})
