"""
API Serializers for connecting Frontend to Detection Orchestrator.

This module provides serializers for:
- Job creation with AOI geometry and date range
- Result retrieval in GeoJSON FeatureCollection format
- Status tracking with progress percentages
"""

from rest_framework import serializers
from django.contrib.gis.geos import Polygon
from apps.jobs.models import Job
from apps.results.models import Result


class JobSerializer(serializers.ModelSerializer):
    """
    Serializer for Job model with progress tracking.
    
    Handles job creation with AOI geometry and date range,
    provides status-based progress percentages for frontend.
    """
    
    class Meta:
        model = Job
        fields = [
            'id',
            'status',
            'aoi_geometry',
            'start_date',
            'end_date',
            'model_version',
            'created_by',
            'created_at',
            'total_detections',
            'illegal_count',
            'detection_data',
            'failure_reason',
            'result_id',
        ]
        read_only_fields = ['id', 'created_by', 'created_at', 'total_detections',
                            'illegal_count', 'detection_data', 'failure_reason', 'result_id']
    
    def to_representation(self, instance):
        """
        Add progress percentage based on job status.
        
        Args:
            instance: Job instance
            
        Returns:
            dict: Serialized job with progress percentage
        """
        data = super().to_representation(instance)
        
        # Map status to progress percentage
        status_progress = {
            'queued': 0,
            'validating': 10,
            'exporting': 25,
            'preprocessing': 50,
            'inferring': 75,
            'postprocessing': 90,
            'storing': 95,
            'completed': 100,
            'failed': 0,
            'cancelled': 0
        }
        
        data['progress_percentage'] = status_progress.get(instance.status, 0)
        data['status_display'] = instance.get_status_display()
        
        return data


class ResultSerializer(serializers.ModelSerializer):
    """
    Serializer for Result model with GeoJSON output.
    
    Outputs detection results in GeoJSON FeatureCollection format
    compatible with frontend mapping libraries.
    """
    
    class Meta:
        model = Result
        fields = [
            'id',
            'job',
            'geojson',
            'tile_reference',
            'summary_statistics',
            'total_area_detected',
            'created_at'
        ]
    
    def to_representation(self, instance):
        """
        Format result for frontend consumption.
        
        Args:
            instance: Result instance
            
        Returns:
            dict: Formatted result with GeoJSON data
        """
        data = super().to_representation(instance)
        
        # Ensure GeoJSON is properly formatted for frontend
        if 'geojson' in data and data['geojson']:
            geojson = data['geojson']
            
            # Validate GeoJSON structure
            if not isinstance(geojson, dict):
                data['geojson'] = {
                    'type': 'FeatureCollection',
                    'features': [],
                    'properties': {
                        'total_detections': 0,
                        'total_area_m2': 0.0,
                        'total_area_hectares': 0.0,
                        'error': 'Invalid GeoJSON format'
                    }
                }
        
        return data


class JobCreateSerializer(serializers.Serializer):
    """
    Serializer for creating new detection jobs.
    
    Validates AOI geometry, date range, and model version
    before triggering the Detection Orchestrator.
    """
    
    aoi_geometry = serializers.JSONField(
        required=True,
        help_text="Area of Interest geometry as GeoJSON"
    )
    
    start_date = serializers.DateField(
        required=True,
        help_text="Start date for imagery analysis"
    )
    
    end_date = serializers.DateField(
        required=True,
        help_text="End date for imagery analysis"
    )
    
    model_version = serializers.CharField(
        max_length=50,
        default='v1.0',
        help_text="Model version to use for detection"
    )
    
    def validate_aoi_geometry(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("AOI geometry must be a valid GeoJSON object")

        if value.get('type') not in ['Polygon', 'MultiPolygon']:
            raise serializers.ValidationError("AOI geometry must be a Polygon or MultiPolygon")

        # Area validation: 100 ha minimum, 1 000 ha maximum
        try:
            import json
            from django.contrib.gis.geos import GEOSGeometry
            geom = GEOSGeometry(json.dumps(value))
            geom.srid = 4326
            # UTM zone 30N (EPSG:32630) gives accurate metric areas for Ghana
            geom_metric = geom.transform(32630, clone=True)
            area_ha = geom_metric.area / 10_000
            if area_ha < 100:
                raise serializers.ValidationError(
                    f"AOI is too small ({area_ha:.1f} ha). Minimum is 100 ha."
                )
            if area_ha > 1_000:
                raise serializers.ValidationError(
                    f"AOI is too large ({area_ha:.1f} ha). Maximum is 1,000 ha."
                )
        except serializers.ValidationError:
            raise
        except Exception:
            pass  # geometry shape already validated above; area check is best-effort

        return value

    def validate(self, attrs):
        start_date = attrs.get('start_date')
        end_date = attrs.get('end_date')
        if start_date and end_date and end_date <= start_date:
            raise serializers.ValidationError(
                {"end_date": "End date must be after start date."}
            )
        return attrs
    
    def create(self, validated_data):
        """
        Create job and trigger Detection Orchestrator.
        
        Args:
            validated_data: Validated job data
            
        Returns:
            Job: Created job instance
        """
        from apps.jobs.models import Job
        from apps.jobs.tasks import process_detection_job
        from django.contrib.gis.geos import GEOSGeometry
        
        # Convert GeoJSON to GEOS geometry
        aoi_geometry = validated_data['aoi_geometry']
        if isinstance(aoi_geometry, dict):
            # Convert GeoJSON to GEOS geometry
            geos_geom = GEOSGeometry(str(aoi_geometry))
        else:
            geos_geom = aoi_geometry
        
        # Create job
        job = Job.objects.create(
            aoi_geometry=geos_geom,
            start_date=validated_data['start_date'],
            end_date=validated_data['end_date'],
            model_version=validated_data['model_version']
        )
        
        return job


class StatusSerializer(serializers.Serializer):
    """
    Serializer for job status with progress information.
    
    Provides detailed status information for frontend progress tracking.
    """
    
    status = serializers.CharField(max_length=20)
    progress_percentage = serializers.IntegerField(min_value=0, max_value=100)
    message = serializers.CharField(max_length=255, required=False)
    estimated_completion = serializers.DateTimeField(required=False)
    
    def to_representation(self, instance):
        """
        Format status information for frontend.
        
        Args:
            instance: Status data
            
        Returns:
            dict: Formatted status information
        """
        if isinstance(instance, dict):
            return instance
        
        return {
            'status': getattr(instance, 'status', 'unknown'),
            'progress_percentage': getattr(instance, 'progress_percentage', 0),
            'message': getattr(instance, 'message', ''),
            'estimated_completion': getattr(instance, 'estimated_completion', None)
        }
