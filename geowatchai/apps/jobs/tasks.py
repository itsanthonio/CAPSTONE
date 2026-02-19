import logging
from celery import shared_task
from .services import JobService
from .models import Job
from apps.gee.tasks import export_hls_for_job


logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def process_detection_job(self, job_id: str):
    """
    Async task to process HLS detection job
    
    Args:
        job_id: Job UUID to process
        
    Returns:
        dict: Processing result
    """
    try:
        logger.info(f"Starting async processing for job {job_id}")
        
        # Update job status to validating
        JobService.update_job_status(job_id, Job.Status.VALIDATING)
        
        # Step 1: Validate AOI and parameters
        job = Job.objects.get(id=job_id)
        
        # Basic validation already done in JobService.create_job
        # Additional validation can be added here
        
        # Step 2: Export imagery from GEE (trigger GEE task)
        logger.info(f"Triggering GEE export for job {job_id}")
        export_hls_for_job.delay(job_id)
        
        # The rest of the pipeline will be triggered by GEE task completion:
        # GEE export → preprocessing → inference → postprocessing → storing
        
        logger.info(f"GEE export triggered for job {job_id}")
        return {'status': 'exporting', 'job_id': job_id}
        
    except Job.DoesNotExist:
        logger.error(f"Job {job_id} not found")
        return {'status': 'failed', 'job_id': job_id, 'error': 'Job not found'}
        
    except Exception as exc:
        logger.error(f"Failed to process job {job_id}: {str(exc)}")
        
        # Update job status to failed
        JobService.update_job_status(
            job_id, 
            Job.Status.FAILED, 
            failure_reason=str(exc)
        )
        
        # Retry if configured
        if self.request.retries < self.max_retries:
            logger.info(f"Retrying job {job_id}, attempt {self.request.retries + 1}")
            raise self.retry(countdown=60 * (2 ** self.request.retries))
        
        return {'status': 'failed', 'job_id': job_id, 'error': str(exc)}
