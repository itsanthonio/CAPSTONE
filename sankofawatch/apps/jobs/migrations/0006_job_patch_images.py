from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0005_job_source_scan_tile'),
    ]

    operations = [
        migrations.AddField(
            model_name='job',
            name='img_false_color',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='job',
            name='img_prediction_mask',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='job',
            name='img_probability_heatmap',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='job',
            name='img_overlay',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
    ]
