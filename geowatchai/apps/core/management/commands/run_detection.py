"""
Django Management Command to run detection pipeline for a specific job.

Usage:
    python manage.py run_detection --job_id <uuid>

This command triggers the complete detection pipeline:
- GEE export
- Preprocessing 
- Inference
- Post-processing
- Results storage
"""

import logging
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from apps.jobs.models import Job
from apps.core.orchestrator import process_detection_job

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Django management command to run detection pipeline for a specific job.
    
    This command allows manual triggering of the complete illegal mining detection
    workflow for a given job ID.
    """
    
    help = 'Run detection pipeline for a specific job'
    
    def add_arguments(self, parser):
        """
        Add command line arguments.
        
        Args:
            parser: Argument parser instance
        """
        parser.add_argument(
            '--job_id',
            type=str,
            required=True,
            help='UUID of the job to process'
        )
        
        parser.add_argument(
            '--threshold',
            type=float,
            default=0.5,
            help='Probability threshold for binary classification (default: 0.5)'
        )
        
        parser.add_argument(
            '--min_area',
            type=float,
            default=100.0,
            help='Minimum polygon area in square meters (default: 100.0)'
        )
        
        parser.add_argument(
            '--async',
            action='store_true',
            help='Run job asynchronously (as Celery task)'
        )
    
    def handle(self, *args, **options):
        """
        Handle command execution.
        
        Args:
            *args: Positional arguments
            **options: Keyword arguments
        """
        job_id = options['job_id']
        threshold = options['threshold']
        min_area = options['min_area']
        async_execution = options['async']
        
        try:
            self.stdout.write(f"Starting detection pipeline for job {job_id}")
            self.stdout.write(f"Threshold: {threshold}, Min Area: {min_area} m²")
            
            # Validate job exists
            try:
                job = Job.objects.get(id=job_id)
                self.stdout.write(f"Found job: {job.id}")
                self.stdout.write(f"Current status: {job.status}")
                self.stdout.write(f"AOI: {job.aoi_geometry}")
                self.stdout.write(f"Date range: {job.start_date} to {job.end_date}")
                self.stdout.write(f"Model version: {job.model_version}")
            except Job.DoesNotExist:
                raise CommandError(f"Job with ID {job_id} not found")
            
            # Run detection pipeline
            if async_execution:
                self.stdout.write("Running job asynchronously...")
                # Import here to avoid circular imports
                from apps.jobs.tasks import process_detection_job
                task_result = process_detection_job.delay(job_id)
                self.stdout.write(f"Task queued: {task_result.id}")
            else:
                self.stdout.write("Running job synchronously...")
                result = process_detection_job(job_id, threshold, min_area)
                
                if result['status'] == 'completed':
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"✅ Pipeline completed successfully!\n"
                            f"Job ID: {result['job_id']}\n"
                            f"Result ID: {result['result_id']}\n"
                            f"Total detections: {result['total_detections']}\n"
                            f"Total area: {result['total_area_hectares']:.3f} hectares"
                        )
                    )
                else:
                    self.stdout.write(
                        self.style.ERROR(
                            f"❌ Pipeline failed!\n"
                            f"Job ID: {result['job_id']}\n"
                            f"Error: {result['error']}"
                        )
                    )
            
        except CommandError as e:
            self.stdout.write(self.style.ERROR(f"Command error: {str(e)}"))
            raise
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Unexpected error: {str(e)}"))
            raise CommandError(f"Failed to run detection pipeline: {str(e)}")
