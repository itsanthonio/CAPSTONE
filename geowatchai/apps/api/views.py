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
from apps.core.orchestrator import process_detection_job

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
    
    def get_serializer(self, instance):
        """
        Get appropriate serializer for result instance.
        
        Args:
            instance: Result instance
            
        Returns:
            ResultSerializer: Configured serializer
        """
        return ResultSerializer(
            instance,
            context={'request': self.request}
        )
    
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
