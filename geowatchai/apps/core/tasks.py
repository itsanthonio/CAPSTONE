"""
Celery tasks for the core orchestrator module.

This module provides async tasks for triggering the detection pipeline.
"""

import logging
from celery import shared_task
from .orchestrator import trigger_detection_pipeline

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def run_detection_task(self, job_id: str, threshold: float = 0.5, min_area: float = 100.0):
    """
    Async task to run detection pipeline for a job.
    
    Args:
        job_id: Job UUID to process
        threshold: Probability threshold for binary classification
        min_area: Minimum polygon area in square meters
        
    Returns:
        dict: Processing result
    """
    try:
        logger.info(f"Starting detection pipeline for job {job_id}")
        
        # Trigger the detection pipeline
        result = trigger_detection_pipeline(job_id, threshold, min_area)
        
        logger.info(f"Detection pipeline completed for job {job_id}: {result['status']}")
        return result
        
    except Exception as exc:
        logger.error(f"Detection pipeline failed for job {job_id}: {str(exc)}")
        
        # Retry the task if possible
        if self.request.retries < self.max_retries:
            logger.info(f"Retrying detection pipeline for job {job_id} (attempt {self.request.retries + 1})")
            raise self.retry(countdown=60 * (2 ** self.request.retries), exc=exc)
        
        # Mark as failed after max retries
        logger.error(f"Detection pipeline permanently failed for job {job_id}")
        raise exc
