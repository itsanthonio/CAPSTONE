"""
Celery configuration for Geo Vigil Guard project.

This module configures Celery with Redis as broker and result backend.
"""

import os
import platform
import multiprocessing

# ── PROJ/GDAL path fix ──────────────────────────────────────────────────────
# Celery workers are a separate process from manage.py, so they don't benefit
# from the fix in manage.py.  Same logic must run here before any Django/GDAL
# import so PROJ finds the QGIS proj.db instead of PostgreSQL's old one.
if platform.system() == 'Windows':
    import pathlib as _pathlib
    _proj = r'C:\Program Files\QGIS 3.44.7\share\proj'   # safe default
    _here = _pathlib.Path(__file__).resolve().parent       # config/
    for _candidate in (_here.parent / '.env', _here / '.env'):
        if _candidate.exists():
            for _ln in _candidate.read_text(encoding='utf-8', errors='ignore').splitlines():
                _ln = _ln.strip()
                if _ln.startswith('#') or '=' not in _ln:
                    continue
                _k, _, _v = _ln.partition('=')
                if _k.strip() in ('PROJ_LIB', 'PROJ_DATA'):
                    _proj = _v.strip().strip('"\'')
                    break
            break
    os.environ['PROJ_LIB']  = _proj
    os.environ['PROJ_DATA'] = _proj
# ────────────────────────────────────────────────────────────────────────────

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
    task_acks_late=False,   # Acknowledge immediately — prevents re-queue on connection drop
    worker_disable_rate_limits=True,
)

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

@app.task(bind=True)
def debug_task(self):
    """Debug task to verify Celery is working."""
    print(f'Request: {self.request!r}')
