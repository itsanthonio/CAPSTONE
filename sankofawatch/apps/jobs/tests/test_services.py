import pytest
from django.contrib.gis.geos import Polygon
from django.test import TestCase
from ..models import Job
from ..services import JobService


class JobServiceTest(TestCase):
    """Test JobService business logic following Anti-Vibe guardrails"""
    
    def setUp(self):
        """Set up test data"""
        self.aoi_geometry = Polygon.from_bbox((-1.0, 6.0, -0.5, 6.5))
        self.start_date = "2024-01-01"
        self.end_date = "2024-01-31"
    
    def test_create_job_success(self):
        """Test successful job creation"""
        job = JobService.create_job(
            aoi_geometry=self.aoi_geometry,
            start_date=self.start_date,
            end_date=self.end_date
        )
        
        self.assertIsInstance(job, Job)
        self.assertEqual(job.status, Job.Status.QUEUED)
        self.assertIsNotNone(job.aoi_hash)
        self.assertEqual(job.start_date.isoformat(), self.start_date)
        self.assertEqual(job.end_date.isoformat(), self.end_date)
    
    def test_create_job_duplicate_aoi(self):
        """Test job creation with duplicate AOI"""
        # Create first job
        job1 = JobService.create_job(
            aoi_geometry=self.aoi_geometry,
            start_date=self.start_date,
            end_date=self.end_date
        )
        
        # Create second job with same parameters
        job2 = JobService.create_job(
            aoi_geometry=self.aoi_geometry,
            start_date=self.start_date,
            end_date=self.end_date
        )
        
        # Should return the same job
        self.assertEqual(job1.id, job2.id)
    
    def test_create_job_invalid_geometry(self):
        """Test job creation with invalid geometry"""
        invalid_geometry = Polygon.from_bbox((-1.0, 6.0, -1.5, 6.5))  # Invalid bbox
        
        with self.assertRaises(ValueError):
            JobService.create_job(
                aoi_geometry=invalid_geometry,
                start_date=self.start_date,
                end_date=self.end_date
            )
    
    def test_update_job_status_success(self):
        """Test successful job status update"""
        job = JobService.create_job(
            aoi_geometry=self.aoi_geometry,
            start_date=self.start_date,
            end_date=self.end_date
        )
        
        success = JobService.update_job_status(
            job_id=str(job.id),
            new_status=Job.Status.VALIDATING
        )
        
        self.assertTrue(success)
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.VALIDATING)
        self.assertIsNotNone(job.started_at)
    
    def test_update_job_status_invalid_transition(self):
        """Test invalid job status transition"""
        job = JobService.create_job(
            aoi_geometry=self.aoi_geometry,
            start_date=self.start_date,
            end_date=self.end_date
        )
        
        # Try to jump from queued to completed (invalid)
        success = JobService.update_job_status(
            job_id=str(job.id),
            new_status=Job.Status.COMPLETED
        )
        
        self.assertFalse(success)
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.QUEUED)
