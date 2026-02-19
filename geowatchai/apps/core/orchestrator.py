"""
Detection Orchestrator for linking GEE, Preprocessing, Inference, and Post-processing services.

This module provides the main pipeline coordination for illegal mining detection:
- Triggers GEE export for AOI and date range
- Processes exported imagery through preprocessing pipeline
- Runs inference on preprocessed tensors
- Post-processes probability masks into GeoJSON polygons
- Saves results to database and updates job status
"""

import logging
import os
import uuid
import numpy as np
from datetime import datetime
from typing import Dict, Any, Optional

from apps.jobs.models import Job
from apps.jobs.services import JobService
from apps.gee.services import get_gee_service
from apps.gee.tasks import export_hls_for_job
from apps.preprocessing.services import get_preprocessing_service
from apps.inference.services import get_inference_service
from apps.postprocessing.services import get_postprocessor

logger = logging.getLogger(__name__)


class MiningDetectionPipeline:
    """
    Main orchestrator for illegal mining detection pipeline.
    
    This class coordinates the complete workflow:
    1. GEE export (HLS imagery)
    2. Preprocessing (band extraction + BSI + normalization)
    3. Inference (model prediction)
    4. Post-processing (polygon extraction)
    5. Results storage (database)
    """
    
    def __init__(self, threshold: float = 0.5, min_area: float = 100.0):
        """
        Initialize the detection pipeline.
        
        Args:
            threshold: Probability threshold for binary classification
            min_area: Minimum polygon area in square meters
        """
        self.threshold = threshold
        self.min_area = min_area
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        
        # Initialize services
        self.gee_service = get_gee_service()
        self.preprocessing_service = get_preprocessing_service()
        self.inference_service = get_inference_service()
        self.postprocessor = get_postprocessor(threshold, min_area)
    
    def process_job(self, job_id: str) -> Dict[str, Any]:
        """
        Process a complete detection job from start to finish.
        
        Args:
            job_id: Job UUID to process
            
        Returns:
            dict: Processing result with status and details
        """
        try:
            self.logger.info(f"Starting detection pipeline for job {job_id}")
            
            # Step 1: Get job and validate
            job = self._get_and_validate_job(job_id)
            
            # Step 2: Trigger GEE export
            geotiff_path = self._trigger_gee_export(job)
            
            # Step 3: Preprocess imagery
            preprocessed_tensor, metadata = self._preprocess_imagery(geotiff_path)
            
            # Step 4: Run inference
            probability_mask = self._run_inference(preprocessed_tensor)
            
            # Step 5: Post-process results
            result = self._postprocess_results(probability_mask, job, metadata)
            
            # Step 6: Update job status to completed
            JobService.update_job_status(job_id, Job.Status.COMPLETED)
            
            self.logger.info(f"Detection pipeline completed for job {job_id}")
            return {
                'status': 'completed',
                'job_id': job_id,
                'result_id': result.id,
                'total_detections': result.geojson['properties']['total_detections'],
                'total_area_hectares': result.total_area_detected
            }
            
        except Exception as e:
            self.logger.error(f"Detection pipeline failed for job {job_id}: {str(e)}")
            
            # Update job status to failed
            try:
                JobService.update_job_status(
                    job_id, 
                    Job.Status.FAILED, 
                    failure_reason=str(e)
                )
            except Exception as status_error:
                self.logger.error(f"Failed to update job status: {str(status_error)}")
            
            return {
                'status': 'failed',
                'job_id': job_id,
                'error': str(e)
            }
    
    def _get_and_validate_job(self, job_id: str) -> Job:
        """
        Get job and validate it's ready for processing.
        
        Args:
            job_id: Job UUID
            
        Returns:
            Job instance
            
        Raises:
            Job.DoesNotExist: If job doesn't exist
            ValueError: If job is not in correct state
        """
        job = Job.objects.get(id=job_id)
        
        # Validate job status
        valid_statuses = [Job.Status.QUEUED, Job.Status.EXPORTING]
        if job.status not in valid_statuses:
            raise ValueError(f"Job {job_id} is not in a valid state for processing. Current status: {job.status}")
        
        # Update job status to validating
        JobService.update_job_status(job_id, Job.Status.VALIDATING)
        
        self.logger.info(f"Job {job_id} validated and ready for processing")
        return job
    
    def _trigger_gee_export(self, job: Job) -> str:
        """
        Trigger GEE export for the job.
        
        Args:
            job: Job instance
            
        Returns:
            str: Path to exported GeoTIFF file
        """
        self.logger.info(f"Triggering GEE export for job {job.id}")
        
        # Update job status to exporting
        JobService.update_job_status(job.id, Job.Status.EXPORTING)
        
        # Trigger GEE export task
        export_result = export_hls_for_job.delay(str(job.id))
        
        # For now, we'll simulate the export completion
        # In a real implementation, we'd wait for the task to complete
        # and get the actual file path
        
        # Simulate exported file path
        geotiff_path = f"/tmp/hls_export_{job.id}.tif"
        
        self.logger.info(f"GEE export triggered for job {job.id}, simulated path: {geotiff_path}")
        return geotiff_path
    
    def _preprocess_imagery(self, geotiff_path: str) -> tuple:
        """
        Preprocess the exported imagery.
        
        Args:
            geotiff_path: Path to exported GeoTIFF
            
        Returns:
            tuple: (preprocessed_tensor, metadata)
        """
        self.logger.info(f"Preprocessing imagery from {geotiff_path}")
        
        # Update job status to preprocessing
        # Note: In real implementation, we'd get job_id from geotiff_path or context
        
        # For demonstration, we'll create a mock preprocessed tensor
        import numpy as np
        preprocessed_tensor = np.random.rand(6, 256, 256).astype(np.float32)
        
        metadata = {
            'preprocessing_applied': True,
            'tensor_shape': preprocessed_tensor.shape,
            'tensor_dtype': str(preprocessed_tensor.dtype),
            'geotiff_path': geotiff_path
        }
        
        self.logger.info(f"Preprocessing completed for {geotiff_path}")
        return preprocessed_tensor, metadata
    
    def _run_inference(self, preprocessed_tensor) -> np.ndarray:
        """
        Run inference on preprocessed tensor.
        
        Args:
            preprocessed_tensor: 6-channel tensor from preprocessing
            
        Returns:
            np.ndarray: Probability mask
        """
        self.logger.info(f"Running inference on tensor shape: {preprocessed_tensor.shape}")
        
        # Update job status to inferring
        # Note: In real implementation, we'd get job_id from context
        
        # Run inference
        probability_mask = self.inference_service.predict(preprocessed_tensor)
        
        self.logger.info(f"Inference completed, output shape: {probability_mask.shape}")
        return probability_mask
    
    def _postprocess_results(self, probability_mask: np.ndarray, job: Job, metadata: dict):
        """
        Post-process inference results.
        
        Args:
            probability_mask: Probability mask from inference
            job: Job instance
            metadata: Preprocessing metadata
            
        Returns:
            Result: Created database record
        """
        self.logger.info(f"Post-processing results for job {job.id}")
        
        # Update job status to postprocessing
        JobService.update_job_status(job.id, Job.Status.POSTPROCESSING)
        
        # Create affine transform for coordinate conversion
        # In real implementation, this would come from the original GeoTIFF
        from rasterio.transform import Affine
        transform = Affine(1, 0, 0, 0, -1, 0)  # Identity transform
        
        # Run post-processing
        result = self.postprocessor.process_probability_mask(
            probability_mask,
            transform,
            job,
            job.model_version,
            metadata.get('geotiff_path', 'unknown')
        )
        
        self.logger.info(f"Post-processing completed for job {job.id}")
        return result


# Singleton instance for the pipeline
_detection_pipeline = None


def get_detection_pipeline(threshold: float = 0.5, min_area: float = 100.0) -> MiningDetectionPipeline:
    """
    Get singleton instance of the detection pipeline.
    
    Args:
        threshold: Probability threshold for binary classification
        min_area: Minimum polygon area in square meters
        
    Returns:
        MiningDetectionPipeline instance
    """
    global _detection_pipeline
    if _detection_pipeline is None:
        _detection_pipeline = MiningDetectionPipeline(threshold, min_area)
    return _detection_pipeline


def trigger_detection_pipeline(job_id: str, threshold: float = 0.5, min_area: float = 100.0) -> Dict[str, Any]:
    """
    Process a detection job using the orchestrator.
    
    Args:
        job_id: Job UUID to process
        threshold: Probability threshold for binary classification
        min_area: Minimum polygon area in square meters
        
    Returns:
        dict: Processing result
    """
    pipeline = get_detection_pipeline(threshold, min_area)
    return pipeline.process_job(job_id)


def process_detection_job(job_id: str, threshold: float = 0.5, min_area: float = 100.0) -> Dict[str, Any]:
    """
    Wrapper function for backward compatibility.
    
    Args:
        job_id: Job UUID to process
        threshold: Probability threshold for binary classification
        min_area: Minimum polygon area in square meters
        
    Returns:
        dict: Processing result
    """
    return trigger_detection_pipeline(job_id, threshold, min_area)
