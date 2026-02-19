import logging
from celery import shared_task
from .services import get_gee_service
from apps.jobs.models import Job
from apps.jobs.services import JobService


logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def export_hls_for_job(self, job_id: str):
    """
    Async task to export HLS imagery for a job
    
    Args:
        job_id: Job UUID
        
    Returns:
        dict: Export result
    """
    try:
        logger.info(f"Starting HLS export for job {job_id}")
        
        # Get job
        job = Job.objects.get(id=job_id)
        
        # Update job status
        JobService.update_job_status(job_id, Job.Status.EXPORTING)
        
        # Get GEE service
        gee_service = get_gee_service()
        
        # Start export
        export_result = gee_service.export_hls_imagery(job)
        
        if not export_result['success']:
            # Export failed
            JobService.update_job_status(
                job_id, 
                Job.Status.FAILED, 
                failure_reason=export_result['error']
            )
            return {
                'status': 'failed',
                'job_id': job_id,
                'error': export_result['error']
            }
        
        # Store export ID in job (you may need to add this field to Job model)
        export_id = export_result['export_id']
        
        # Start monitoring task
        monitor_hls_export.delay(job_id, export_id)
        
        logger.info(f"HLS export started for job {job_id}, export ID: {export_id}")
        
        return {
            'status': 'exporting',
            'job_id': job_id,
            'export_id': export_id
        }
        
    except Job.DoesNotExist:
        logger.error(f"Job {job_id} not found")
        return {
            'status': 'failed',
            'job_id': job_id,
            'error': 'Job not found'
        }
        
    except Exception as exc:
        logger.error(f"HLS export task failed for job {job_id}: {str(exc)}")
        
        # Update job status to failed
        try:
            JobService.update_job_status(
                job_id, 
                Job.Status.FAILED, 
                failure_reason=str(exc)
            )
        except:
            pass
        
        # Retry if configured
        if self.request.retries < self.max_retries:
            logger.info(f"Retrying HLS export for job {job_id}, attempt {self.request.retries + 1}")
            raise self.retry(countdown=60 * (2 ** self.request.retries))
        
        return {
            'status': 'failed',
            'job_id': job_id,
            'error': str(exc)
        }


@shared_task(bind=True, max_retries=2)
def monitor_hls_export(self, job_id: str, export_id: str):
    """
    Async task to monitor GEE export progress
    
    Args:
        job_id: Job UUID
        export_id: GEE export task ID
        
    Returns:
        dict: Monitoring result
    """
    try:
        logger.info(f"Monitoring HLS export {export_id} for job {job_id}")
        
        # Get GEE service
        gee_service = get_gee_service()
        
        # Monitor export
        monitor_result = gee_service.monitor_export(export_id)
        
        status = monitor_result['status']
        
        if status == 'completed':
            logger.info(f"HLS export completed for job {job_id}")
            
            # Update job status and trigger preprocessing
            JobService.update_job_status(job_id, Job.Status.PREPROCESSING)
            
            # Trigger preprocessing task (to be implemented)
            from apps.preprocessing.tasks import preprocess_imagery
            preprocess_imagery.delay(job_id, monitor_result.get('export_url'))
            
            return {
                'status': 'completed',
                'job_id': job_id,
                'export_url': monitor_result.get('export_url')
            }
            
        elif status in ['failed', 'cancelled', 'timeout', 'error']:
            error_msg = monitor_result.get('error', 'Unknown error')
            logger.error(f"HLS export {status} for job {job_id}: {error_msg}")
            
            # Update job status to failed
            JobService.update_job_status(
                job_id, 
                Job.Status.FAILED, 
                failure_reason=f"Export {status}: {error_msg}"
            )
            
            return {
                'status': status,
                'job_id': job_id,
                'error': error_msg
            }
            
        else:
            # Export still running, continue monitoring
            logger.info(f"HLS export still running for job {job_id}, continuing monitoring")
            
            # Schedule next check with exponential backoff
            retry_delay = min(60 * (2 ** self.request.retries), 300)  # Max 5 minutes
            
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=retry_delay)
            else:
                # Max retries reached, mark as failed
                logger.error(f"Max monitoring retries reached for job {job_id}")
                JobService.update_job_status(
                    job_id, 
                    Job.Status.FAILED, 
                    failure_reason="Export monitoring timeout"
                )
                
                return {
                    'status': 'timeout',
                    'job_id': job_id,
                    'error': 'Export monitoring timeout'
                }
        
    except Exception as exc:
        logger.error(f"Export monitoring error for job {job_id}: {str(exc)}")
        
        # Update job status to failed
        try:
            JobService.update_job_status(
                job_id, 
                Job.Status.FAILED, 
                failure_reason=f"Monitoring error: {str(exc)}"
            )
        except:
            pass
        
        # Retry if configured
        if self.request.retries < self.max_retries:
            logger.info(f"Retrying export monitoring for job {job_id}, attempt {self.request.retries + 1}")
            raise self.retry(countdown=60 * (2 ** self.request.retries))
        
        return {
            'status': 'failed',
            'job_id': job_id,
            'error': str(exc)
        }


@shared_task
def gee_health_check():
    """
    Periodic task to check GEE service health
    
    Returns:
        dict: Health check result
    """
    try:
        gee_service = get_gee_service()
        service_info = gee_service.get_service_info()
        
        if service_info['initialized']:
            logger.info("GEE service health check passed")
            return {
                'status': 'healthy',
                'service_info': service_info
            }
        else:
            logger.warning("GEE service not initialized")
            return {
                'status': 'unhealthy',
                'service_info': service_info
            }
            
    except Exception as e:
        logger.error(f"GEE health check failed: {str(e)}")
        return {
            'status': 'error',
            'error': str(e)
        }