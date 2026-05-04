import uuid
import django.contrib.gis.db.models.fields
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='ScanTile',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=200)),
                ('geometry', django.contrib.gis.db.models.fields.PolygonField(srid=4326)),
                ('priority', models.CharField(
                    choices=[('hotspot', 'Hotspot'), ('normal', 'Normal')],
                    db_index=True,
                    default='normal',
                    max_length=20,
                )),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('last_scanned_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('scan_count', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['priority', 'last_scanned_at'],
            },
        ),
        migrations.AddIndex(
            model_name='scantile',
            index=models.Index(
                fields=['priority', 'is_active', 'last_scanned_at'],
                name='scanning_ti_priorit_idx',
            ),
        ),
        migrations.CreateModel(
            name='AutoScanConfig',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('is_enabled', models.BooleanField(default=True)),
                ('window_start_hour', models.IntegerField(default=6)),
                ('window_end_hour', models.IntegerField(default=18)),
                ('rate_limited_date', models.DateField(blank=True, null=True)),
                ('tiles_scanned_today', models.IntegerField(default=0)),
                ('last_reset_date', models.DateField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Auto Scan Config',
            },
        ),
    ]
