from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('scanning', '0001_initial'),
        ('jobs', '0004_job_created_by'),
        ('jobs', '0002_job_source_scan_tile'),  # absorb no-op branch so it isn't a leaf
    ]

    operations = [
        migrations.AddField(
            model_name='job',
            name='source',
            field=models.CharField(
                choices=[('manual', 'Manual'), ('automated', 'Automated')],
                db_index=True,
                default='manual',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='job',
            name='scan_tile',
            field=models.ForeignKey(
                blank=True,
                help_text='Automated scan tile that triggered this job (null for manual scans)',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='jobs',
                to='scanning.ScanTile',
            ),
        ),
        # Data migration: tag all existing jobs as 'manual'
        migrations.RunSQL(
            sql="UPDATE jobs_job SET source = 'manual' WHERE source IS NULL OR source = '';",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
