import logging
from celery import shared_task
from .services import get_inference_service


logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def run_batch_inference(self, image_tile_paths: list, job_id: str = None):
    """
    Async task to run inference on a batch of image tiles
    
    Args:
        image_tile_paths: List of file paths to image tiles
        job_id: Optional job ID for logging
        
    Returns:
        dict: Inference results
    """
    try:
        logger.info(f"Starting batch inference for {len(image_tile_paths)} tiles")
        
        # Get inference service
        inference_service = get_inference_service()
        
        # Load image tiles
        import numpy as np
        from PIL import Image
        
        image_tiles = []
        for tile_path in image_tile_paths:
            image = Image.open(tile_path)
            image_array = np.array(image)
            image_tiles.append(image_array)
        
        # Run inference
        confidence_scores = inference_service.predict_batch(image_tiles)
        
        results = {
            'status': 'completed',
            'confidence_scores': confidence_scores,
            'tile_count': len(image_tiles),
            'job_id': job_id
        }
        
        logger.info(f"Completed batch inference for job {job_id}")
        return results
        
    except Exception as exc:
        logger.error(f"Batch inference failed for job {job_id}: {str(exc)}")
        
        # Retry if configured
        if self.request.retries < self.max_retries:
            logger.info(f"Retrying batch inference for job {job_id}, attempt {self.request.retries + 1}")
            raise self.retry(countdown=60 * (2 ** self.request.retries))
        
        return {
            'status': 'failed',
            'error': str(exc),
            'job_id': job_id
        }
