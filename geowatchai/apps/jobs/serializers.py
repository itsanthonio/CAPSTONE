from rest_framework import serializers
from .models import Job


class JobSerializer(serializers.ModelSerializer):
    """Job serializer following Anti-Vibe snake_case naming conventions"""
    
    class Meta:
        model = Job
        fields = [
            'id', 'name', 'status', 'aoi_geometry', 'aoi_hash', 'start_date', 'end_date',
            'model_version', 'preprocessing_version', 'created_at', 'started_at',
            'completed_at', 'failure_reason',
            'total_detections', 'illegal_count', 'result_id', 'detection_data'
        ]
        read_only_fields = ['id', 'aoi_hash', 'created_at', 'started_at', 'completed_at']


class JobCreateSerializer(serializers.ModelSerializer):
    """Job creation serializer with validation"""
    
    class Meta:
        model = Job
        fields = ['name', 'aoi_geometry', 'start_date', 'end_date']

    name = serializers.CharField(required=False, allow_blank=True, default='')
    
    def validate_aoi_geometry(self, value):
        """Validate AOI geometry (Anti-Vibe 31.1.1)"""
        if not value.valid:
            raise serializers.ValidationError("Invalid AOI geometry provided")
        
        # Check for self-intersection
        if value.intersects(value):
            raise serializers.ValidationError("AOI geometry cannot self-intersect")
        
        return value
    
    def validate(self, attrs):
        """Cross-field validation"""
        start_date = attrs.get('start_date')
        end_date = attrs.get('end_date')
        
        if start_date and end_date and start_date > end_date:
            raise serializers.ValidationError("Start date must be before end date")
        
        return attrs


class JobStatusSerializer(serializers.Serializer):
    """Job status update serializer"""
    
    status = serializers.ChoiceField(choices=Job.Status.choices)
    failure_reason = serializers.CharField(required=False, allow_blank=True)
