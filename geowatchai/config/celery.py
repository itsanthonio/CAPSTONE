"""
Celery configuration for Geo Vigil Guard project.

This module configures Celery with Redis as broker and result backend.
"""

import os
import multiprocessing
from celery import Celery

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Use spawn method instead of fork to avoid macOS fork safety issues
multiprocessing.set_start_method('spawn', force=True)

# Configure Celery for macOS
os.environ.setdefault('CELERYD_POOL', 'solo')
os.environ.setdefault('CELERYD_POOL_RESTARTS', 'True')
os.environ.setdefault('CELERY_WORKER_POOL', 'solo')

app = Celery('geo-vigil-guard')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Configure worker settings for macOS compatibility
app.conf.update(
    worker_pool='solo',
    worker_pool_restarts=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    worker_disable_rate_limits=True,
)

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

@app.task(bind=True)
def debug_task(self):
    """Debug task to verify Celery is working."""
    print(f'Request: {self.request!r}')
