import hashlib
import logging
from datetime import timedelta
from typing import Optional, Dict, Any
from django.db import transaction
from django.utils import timezone
from django.contrib.gis.geos import Polygon
from .models import Job


logger = logging.getLogger(__name__)


class JobService:
    """Business logic for job management following Anti-Vibe guardrails"""
    
    @staticmethod
    def create_job(aoi_geometry: Polygon, start_date: str, end_date: str, 
                  model_version: str = "v1.0.0", 
                  preprocessing_version: str = "v1.0.0") -> Job:
        """
        Create a new detection job with AOI deduplication
        
        Args:
            aoi_geometry: PostGIS Polygon for area of interest
            start_date: Start date for imagery analysis
            end_date: End date for imagery analysis
            model_version: ML model version identifier
            preprocessing_version: Preprocessing pipeline version
            
        Returns:
            Job: Created job instance
            
        Raises:
            ValueError: If AOI geometry is invalid
        """
        # Validate AOI geometry (Anti-Vibe 31.1.1)
        if not aoi_geometry.valid:
            raise ValueError("Invalid AOI geometry provided")
        
        # Generate deterministic AOI hash for deduplication (Anti-Vibe 31.6.3)
        aoi_hash = JobService._generate_aoi_hash(aoi_geometry, start_date, end_date)
        
        # Check for existing concurrent job with same parameters
        existing_job = Job.objects.filter(
            aoi_hash=aoi_hash,
            status__in=[Job.Status.QUEUED, Job.Status.VALIDATING, Job.Status.EXPORTING,
                       Job.Status.PREPROCESSING, Job.Status.INFERRING, Job.Status.POSTPROCESSING]
        ).first()
        
        if existing_job:
            # Check if existing job is stale (older than 10 minutes)
            job_age = timezone.now() - existing_job.created_at
            if job_age > timedelta(minutes=10):
                logger.info(f"Found stale job {existing_job.id} ({job_age}), creating new job")
                # Don't return stale job, create new one instead
            else:
                logger.info(f"Returning existing job {existing_job.id} for duplicate AOI")
                return existing_job
        
        # Create new job with concurrency lock
        with transaction.atomic():
            job = Job.objects.create(
                aoi_geometry=aoi_geometry,
                aoi_hash=aoi_hash,
                start_date=start_date,
                end_date=end_date,
                model_version=model_version,
                preprocessing_version=preprocessing_version
            )
            
            logger.info(f"Created new job {job.id} with status {job.status}")
            return job
    
    @staticmethod
    def update_job_status(job_id: str, new_status: Job.Status, 
                        failure_reason: Optional[str] = None) -> bool:
        """
        Update job status with proper logging
        
        Args:
            job_id: Job UUID
            new_status: New status value
            failure_reason: Optional failure reason for failed status
            
        Returns:
            bool: True if update successful
        """
        try:
            with transaction.atomic():
                job = Job.objects.select_for_update().get(id=job_id)
                
                # Validate status transition
                if not JobService._is_valid_status_transition(job.status, new_status):
                    logger.error(f"Invalid status transition for job {job_id}: {job.status} -> {new_status}")
                    return False
                
                old_status = job.status
                job.status = new_status
                
                if new_status == Job.Status.FAILED and failure_reason:
                    job.failure_reason = failure_reason
                
                # Update timestamps
                if new_status == Job.Status.VALIDATING and not job.started_at:
                    job.started_at = timezone.now()
                elif new_status in [Job.Status.COMPLETED, Job.Status.FAILED, Job.Status.CANCELLED]:
                    job.completed_at = timezone.now()
                
                job.save()
                
                logger.info(f"Updated job {job_id} status: {old_status} -> {new_status}")
                return True
                
        except Job.DoesNotExist:
            logger.error(f"Job {job_id} not found for status update")
            return False
        except Exception as e:
            logger.error(f"Failed to update job {job_id} status: {str(e)}")
            return False
    
    @staticmethod
    def _generate_aoi_hash(aoi_geometry: Polygon, start_date: str, end_date: str) -> str:
        """Generate deterministic hash for AOI deduplication"""
        # Convert geometry to WKT for consistent hashing
        geometry_wkt = aoi_geometry.wkt
        hash_input = f"{geometry_wkt}:{start_date}:{end_date}"
        return hashlib.sha256(hash_input.encode()).hexdigest()
    
    @staticmethod
    def _is_valid_status_transition(old_status: Job.Status, new_status: Job.Status) -> bool:
        """Validate job status transitions"""
        valid_transitions = {
            Job.Status.QUEUED: [Job.Status.VALIDATING, Job.Status.CANCELLED, Job.Status.FAILED],
            Job.Status.VALIDATING: [Job.Status.EXPORTING, Job.Status.FAILED, Job.Status.CANCELLED],
            Job.Status.EXPORTING: [Job.Status.PREPROCESSING, Job.Status.FAILED, Job.Status.CANCELLED],
            Job.Status.PREPROCESSING: [Job.Status.INFERRING, Job.Status.FAILED, Job.Status.CANCELLED],
            Job.Status.INFERRING: [Job.Status.POSTPROCESSING, Job.Status.FAILED, Job.Status.CANCELLED],
            Job.Status.POSTPROCESSING: [Job.Status.STORING, Job.Status.FAILED, Job.Status.CANCELLED],
            Job.Status.STORING: [Job.Status.COMPLETED, Job.Status.FAILED, Job.Status.CANCELLED],
            Job.Status.COMPLETED: [],  # Terminal state
            Job.Status.FAILED: [],      # Terminal state
            Job.Status.CANCELLED: []    # Terminal state
        }
        
        return new_status in valid_transitions.get(old_status, [])
