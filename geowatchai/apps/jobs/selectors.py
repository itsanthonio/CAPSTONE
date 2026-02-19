from typing import Optional, List
from django.db import models
from .models import Job


class JobSelector:
    """Database read operations for Job model following Anti-Vibe guardrails"""
    
    @staticmethod
    def get_job_by_id(job_id: str) -> Optional[Job]:
        """
        Get job by ID with proper error handling
        
        Args:
            job_id: Job UUID
            
        Returns:
            Optional[Job]: Job instance or None if not found
        """
        try:
            return Job.objects.get(id=job_id)
        except Job.DoesNotExist:
            return None
    
    @staticmethod
    def get_jobs_by_status(status: Job.Status) -> List[Job]:
        """
        Get jobs filtered by status with index optimization
        
        Args:
            status: Job status to filter by
            
        Returns:
            List[Job]: List of jobs with specified status
        """
        return Job.objects.filter(status=status).order_by('-created_at')
    
    @staticmethod
    def get_recent_jobs(limit: int = 50) -> List[Job]:
        """
        Get recent jobs with limit and index optimization
        
        Args:
            limit: Maximum number of jobs to return
            
        Returns:
            List[Job]: List of recent jobs
        """
        return Job.objects.all().order_by('-created_at')[:limit]
    
    @staticmethod
    def get_jobs_by_date_range(start_date, end_date) -> List[Job]:
        """
        Get jobs within date range with proper indexing
        
        Args:
            start_date: Start date filter
            end_date: End date filter
            
        Returns:
            List[Job]: Jobs within date range
        """
        return Job.objects.filter(
            created_at__gte=start_date,
            created_at__lte=end_date
        ).order_by('-created_at')
    
    @staticmethod
    def get_job_statistics() -> dict:
        """
        Get job statistics for dashboard
        
        Returns:
            dict: Job statistics by status
        """
        stats = Job.objects.values('status').annotate(
            count=models.Count('id')
        ).order_by('status')
        
        return {stat['status']: stat['count'] for stat in stats}
