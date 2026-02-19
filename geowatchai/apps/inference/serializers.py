from rest_framework import serializers


class InferenceRequestSerializer(serializers.Serializer):
    """Serializer for inference requests following Anti-Vibe snake_case naming"""
    
    image_tiles = serializers.ListField(
        child=serializers.ImageField(),
        required=True,
        help_text="List of image tiles for inference"
    )
    
    def validate_image_tiles(self, value):
        """Validate image tiles"""
        if not value:
            raise serializers.ValidationError("At least one image tile is required")
        
        if len(value) > 100:  # Reasonable batch size limit
            raise serializers.ValidationError("Maximum 100 tiles per batch")
        
        return value


class InferenceResponseSerializer(serializers.Serializer):
    """Serializer for inference responses"""
    
    confidence_scores = serializers.ListField(
        child=serializers.FloatField(min_value=0.0, max_value=1.0),
        required=True,
        help_text="Confidence scores for each tile"
    )
    processing_time_ms = serializers.IntegerField(
        required=True,
        help_text="Processing time in milliseconds"
    )
    model_info = serializers.DictField(
        required=True,
        help_text="Model information"
    )
