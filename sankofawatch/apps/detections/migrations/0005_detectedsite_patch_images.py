from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('detections', '0004_alert_inspection_count'),
    ]

    operations = [
        migrations.AddField(
            model_name='detectedsite',
            name='img_false_color',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='detectedsite',
            name='img_prediction_mask',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='detectedsite',
            name='img_probability_heatmap',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='detectedsite',
            name='img_overlay',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
    ]
