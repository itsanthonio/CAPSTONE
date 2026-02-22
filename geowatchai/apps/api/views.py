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
from rest_framework.permissions import AllowAny

from apps.jobs.models import Job
from apps.results.models import Result
from apps.api.serializers import (
    JobSerializer,
    ResultSerializer,
    JobCreateSerializer,
    StatusSerializer
)

logger = logging.getLogger(__name__)


class JobViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Job model with creation and status tracking.

    Provides endpoints for:
    - Creating new detection jobs
    - Retrieving job status with progress
    - Listing all jobs
    """

    queryset = Job.objects.all()
    serializer_class = JobSerializer
    permission_classes = [AllowAny]

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
                job = serializer.save()

            logger.info(f"Created new job {job.id} via API")

            # Trigger detection pipeline asynchronously
            try:
                # Import here to avoid circular imports
                from apps.core.tasks import run_detection_task
                task_result = run_detection_task.delay(str(job.id))

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
                logger.error(f"Failed to trigger pipeline for job {job.id}: {str(e)}")

                # Update job status to failed
                job.status = Job.Status.FAILED
                job.save()

                return Response(
                    {
                        'error': 'Failed to start detection pipeline',
                        'details': str(e),
                        'job_id': str(job.id)
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

        except Exception as e:
            logger.error(f"Job creation failed: {str(e)}")
            return Response(
                {
                    'error': 'Job creation failed',
                    'details': str(e)
                },
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


class ResultViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Result model with GeoJSON output.

    Provides endpoints for:
    - Retrieving detection results for completed jobs
    - Listing all results
    """

    serializer_class = ResultSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        """
        Filter results by job_id if provided.

        Returns:
            QuerySet: Filtered results
        """
        queryset = Result.objects.all()
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


class DetectedSiteViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only ViewSet for DetectedSite.
    Provides site detail and timelapse frames for the map panel.
    """
    permission_classes = [AllowAny]

    def get_queryset(self):
        from apps.detections.models import DetectedSite
        return DetectedSite.objects.select_related(
            'intersecting_concession', 'region', 'model_run', 'satellite_imagery'
        ).all()

    def list(self, request, *args, **kwargs):
        """Return all detected sites as a GeoJSON FeatureCollection for map + sidebar."""
        import json as _json
        qs = self.get_queryset().filter(geometry__isnull=False).order_by('-created_at')[:500]
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
                    'region': site.region.name if site.region else None,
                    'lat': round(centroid.y, 4) if centroid else None,
                    'lng': round(centroid.x, 4) if centroid else None,
                }
            })
        return Response({'type': 'FeatureCollection', 'features': features})

    def retrieve(self, request, *args, **kwargs):
        from apps.detections.models import DetectedSite
        site = get_object_or_404(DetectedSite, pk=kwargs['pk'])

        concession = None
        if site.intersecting_concession:
            concession = {
                'license_number': site.intersecting_concession.license_number,
                'name': site.intersecting_concession.concession_name,
                'holder': site.intersecting_concession.holder_name,
                'license_type': site.intersecting_concession.get_license_type_display(),
                'overlap_pct': site.concession_overlap_pct,
            }

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
            'region': site.region.name if site.region else None,
            'lat': round(site.centroid.y, 4) if site.centroid else None,
            'lng': round(site.centroid.x, 4) if site.centroid else None,
        })

    @action(detail=True, methods=['get'], url_path='timelapse')
    def timelapse(self, request, pk=None):
        """Return ordered timelapse frames for a detected site."""
        from apps.detections.models import DetectedSite, SiteTimelapse
        site = get_object_or_404(DetectedSite, pk=pk)
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


class ConcessionGeoJSONView(viewsets.ReadOnlyModelViewSet):
    """
    Returns legal concessions as GeoJSON for map display.
    Lightweight — only geometry + label fields, no heavy data.
    """
    permission_classes = [AllowAny]

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


class AlertViewSet(viewsets.ViewSet):
    """
    Alert listing and status-change actions.
    GET  /api/alerts/          — list with filter params: status, severity, alert_type
    GET  /api/alerts/{id}/     — detail
    POST /api/alerts/{id}/acknowledge/
    POST /api/alerts/{id}/dismiss/
    POST /api/alerts/{id}/dispatch/
    """
    permission_classes = [AllowAny]

    def get_queryset(self):
        from apps.detections.models import Alert
        qs = Alert.objects.select_related(
            'detected_site', 'detected_site__region', 'assigned_to'
        )
        status   = self.request.query_params.get('status')
        severity = self.request.query_params.get('severity')
        atype    = self.request.query_params.get('alert_type')
        if status:   qs = qs.filter(status=status)
        if severity: qs = qs.filter(severity=severity)
        if atype:    qs = qs.filter(alert_type=atype)
        return qs

    def list(self, request, *args, **kwargs):
        from apps.detections.models import Alert
        qs      = self.get_queryset()
        page    = int(request.query_params.get('page', 1))
        per_page = min(int(request.query_params.get('per_page', 20)), 10000)
        total   = qs.count()
        alerts  = qs[(page - 1) * per_page: page * per_page]

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
                    'region': site.region.name if site.region else None,
                    'lat': round(centroid.y, 4) if centroid else None,
                    'lng': round(centroid.x, 4) if centroid else None,
                },
                'assigned_to': a.assigned_to.get_full_name() or a.assigned_to.username if a.assigned_to else None,
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
        a = get_object_or_404(Alert, pk=kwargs['pk'])
        site = a.detected_site
        centroid = site.centroid
        return Response({
            'id': str(a.id),
            'title': a.title,
            'description': a.description,
            'alert_type_display': a.get_alert_type_display(),
            'severity': a.severity,
            'severity_display': a.get_severity_display(),
            'status': a.status,
            'status_display': a.get_status_display(),
            'created_at': a.created_at.isoformat(),
            'resolution_notes': a.resolution_notes,
            'site': {
                'id': str(site.id),
                'confidence_pct': round(site.confidence_score * 100, 1),
                'area_hectares': round(site.area_hectares, 2),
                'legal_status': site.legal_status,
                'detection_date': str(site.detection_date),
                'recurrence_count': site.recurrence_count,
                'region': site.region.name if site.region else None,
                'lat': round(centroid.y, 4) if centroid else None,
                'lng': round(centroid.x, 4) if centroid else None,
            },
        })

    def _change_status(self, request, pk, new_status, extra=None):
        from apps.detections.models import Alert
        from django.utils import timezone
        a = get_object_or_404(Alert, pk=pk)
        a.status = new_status
        if new_status == 'acknowledged':
            a.acknowledged_at = timezone.now()
        elif new_status == 'resolved':
            a.resolved_at = timezone.now()
            a.resolution_notes = request.data.get('resolution_notes', '')
        a.save()
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
        return self._change_status(request, pk, 'resolved')