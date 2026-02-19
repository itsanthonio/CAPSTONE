import logging
import time
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .services import get_inference_service
from .serializers import InferenceRequestSerializer, InferenceResponseSerializer


logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def run_inference(request):
    """
    Run inference on image tiles
    
    Args:
        request: HTTP request with image tiles
        
    Returns:
        Response: Inference results with confidence scores
    """
    try:
        # Validate request data
        serializer = InferenceRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # Get inference service (singleton pattern)
        inference_service = get_inference_service()
        
        # Extract image tiles from validated data
        image_tiles = []
        for tile_file in serializer.validated_data['image_tiles']:
            # Convert uploaded file to numpy array
            import numpy as np
            from PIL import Image
            
            image = Image.open(tile_file)
            image_array = np.array(image)
            image_tiles.append(image_array)
        
        # Run inference with timing
        start_time = time.time()
        confidence_scores = inference_service.predict_batch(image_tiles)
        processing_time_ms = int((time.time() - start_time) * 1000)
        
        # Get model info
        model_info = inference_service.get_model_info()
        
        # Prepare response
        response_data = {
            'confidence_scores': confidence_scores,
            'processing_time_ms': processing_time_ms,
            'model_info': model_info
        }
        
        # Validate response
        response_serializer = InferenceResponseSerializer(data=response_data)
        response_serializer.is_valid(raise_exception=True)
        
        logger.info(f"Inference completed for {len(image_tiles)} tiles in {processing_time_ms}ms")
        
        return Response(response_serializer.data, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Inference API error: {str(e)}")
        return Response(
            {'error': 'Inference processing failed'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def model_info(request):
    """
    Get model information
    
    Returns:
        Response: Model metadata and status
    """
    try:
        inference_service = get_inference_service()
        model_info = inference_service.get_model_info()
        
        return Response(model_info, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Model info API error: {str(e)}")
        return Response(
            {'error': 'Failed to get model info'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
