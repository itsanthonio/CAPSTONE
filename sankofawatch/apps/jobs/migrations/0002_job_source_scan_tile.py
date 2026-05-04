# This file is intentionally empty — superseded by 0005_job_source_scan_tile.py
# It cannot be deleted because Django's migration loader has already seen it.
# It is kept as a no-op to avoid import errors from any cached .pyc files.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('jobs', '0002_alter_job_options'),
    ]
    operations = []
