import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from .services import get_gee_service
from apps.jobs.models import Job


logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def gee_service_info(request):
    """
    Get GEE service information and status
    
    Returns:
        Response: Service status and configuration
    """
    try:
        gee_service = get_gee_service()
        service_info = gee_service.get_service_info()
        
        return Response(service_info, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"GEE service info API error: {str(e)}")
        return Response(
            {'error': 'Failed to get service info'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def validate_aoi(request):
    """
    Validate AOI geometry for GEE export
    
    Request body:
        geometry: GeoJSON Polygon geometry
        
    Returns:
        Response: Validation result
    """
    try:
        geometry_data = request.data.get('geometry')
        if not geometry_data:
            return Response(
                {'error': 'Geometry is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Convert GeoJSON to PostGIS Polygon
        from django.contrib.gis.geos import GEOSGeometry
        geometry = GEOSGeometry(str(geometry_data))
        
        if not geometry.geom_type == 'Polygon':
            return Response(
                {'error': 'Only Polygon geometries are supported'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate using GEE service
        gee_service = get_gee_service()
        is_valid, error_message = gee_service.validate_aoi(geometry)
        
        if is_valid:
            # Calculate area for response
            area_sq_m = geometry.area
            area_hectares = area_sq_m / 10000
            vertex_count = len(geometry.coords[0])
            
            return Response({
                'valid': True,
                'area_hectares': round(area_hectares, 2),
                'vertex_count': vertex_count
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                'valid': False,
                'error': error_message
            }, status=status.HTTP_400_BAD_REQUEST)
        
    except Exception as e:
        logger.error(f"AOI validation API error: {str(e)}")
        return Response(
            {'error': 'Validation failed'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def trigger_export(request, job_id):
    """
    Manually trigger GEE export for a job
    
    Args:
        job_id: Job UUID
        
    Returns:
        Response: Export trigger result
    """
    try:
        # Get job
        job = Job.objects.get(id=job_id)
        
        # Check if job is in appropriate status
        if job.status not in [Job.Status.QUEUED, Job.Status.VALIDATING]:
            return Response({
                'error': f'Job must be in QUEUED or VALIDATING status, current: {job.status}'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Trigger export
        from .tasks import export_hls_for_job
        task_result = export_hls_for_job.delay(job_id)
        
        return Response({
            'success': True,
            'task_id': task_result.id,
            'job_id': job_id,
            'message': 'Export task triggered'
        }, status=status.HTTP_202_ACCEPTED)
        
    except Job.DoesNotExist:
        return Response(
            {'error': 'Job not found'}, 
            status=status.HTTP_404_NOT_FOUND
        )
        
    except Exception as e:
        logger.error(f"Export trigger API error: {str(e)}")
        return Response(
            {'error': 'Failed to trigger export'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_status(request, export_id):
    """
    Get status of a GEE export task
    
    Args:
        export_id: GEE export task ID
        
    Returns:
        Response: Export status
    """
    try:
        gee_service = get_gee_service()
        status_result = gee_service.monitor_export(export_id, timeout=10)  # Quick check
        
        return Response(status_result, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Export status API error: {str(e)}")
        return Response(
            {'error': 'Failed to get export status'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )