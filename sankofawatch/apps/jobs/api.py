import logging
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from .models import Job
from .services import JobService
from .selectors import JobSelector
from .serializers import JobSerializer, JobCreateSerializer, JobStatusSerializer


logger = logging.getLogger(__name__)


class JobViewSet(viewsets.ModelViewSet):
    """Job API endpoints following Anti-Vibe guardrails"""

    serializer_class = JobSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Scope jobs by the requesting user's organisation.
        System admins see all jobs; everyone else sees their org's jobs
        plus automated scans (created_by=None, source='automated')."""
        user = self.request.user
        try:
            profile = user.profile
        except Exception:
            return Job.objects.none()
        from apps.accounts.models import UserProfile
        if profile.role == UserProfile.Role.SYSTEM_ADMIN:
            return Job.objects.all()
        org = profile.organisation
        from django.db.models import Q
        return Job.objects.filter(
            Q(organisation=org) | Q(created_by=None, source=Job.Source.AUTOMATED)
        )

    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'create':
            return JobCreateSerializer
        return JobSerializer

    def create(self, request, *args, **kwargs):
        """Create new job with validation and business logic"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            profile = request.user.profile
            org = profile.organisation
        except Exception:
            org = None

        try:
            job = JobService.create_job(
                aoi_geometry=serializer.validated_data['aoi_geometry'],
                start_date=serializer.validated_data['start_date'],
                end_date=serializer.validated_data['end_date'],
                name=serializer.validated_data.get('name', ''),
                created_by=request.user,
                organisation=org,
            )
            
            logger.info(f"Created job {job.id} for user {request.user}")
            response_serializer = JobSerializer(job)
            return Response(response_serializer.data, status=status.HTTP_201_CREATED)
            
        except ValueError as e:
            logger.error(f"Job creation failed: {str(e)}")
            return Response(
                {'error': str(e)}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.error(f"Unexpected error creating job: {str(e)}")
            return Response(
                {'error': 'Internal server error'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def update_status(self, request, pk=None):
        """Update job status with proper validation"""
        job = get_object_or_404(Job, pk=pk)
        serializer = JobStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        success = JobService.update_job_status(
            job_id=str(job.id),
            new_status=serializer.validated_data['status'],
            failure_reason=serializer.validated_data.get('failure_reason')
        )
        
        if success:
            logger.info(f"Updated job {job.id} status to {serializer.validated_data['status']}")
            return Response({'status': 'updated'})
        else:
            return Response(
                {'error': 'Failed to update job status'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=False, methods=['get'])
    def active(self, request):
        """Return in-progress jobs created by the current user, for map restoration."""
        terminal = {Job.Status.COMPLETED, Job.Status.FAILED, Job.Status.CANCELLED}
        jobs = self.get_queryset().filter(
            created_by=request.user,
        ).exclude(status__in=terminal).order_by('created_at')
        serializer = self.get_serializer(jobs, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def statistics(self, request):
        """Get job statistics for dashboard"""
        stats = JobSelector.get_job_statistics()
        return Response(stats)
    
    @action(detail=False, methods=['get'])
    def recent(self, request):
        """Get recent jobs with limit"""
        limit = min(int(request.query_params.get('limit', 50)), 100)
        jobs = JobSelector.get_recent_jobs(limit=limit)
        serializer = self.get_serializer(jobs, many=True)
        return Response(serializer.data)
