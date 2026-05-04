from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('detections', '0003_alert_escalated_at_auditlog'),
    ]

    operations = [
        migrations.AddField(
            model_name='alert',
            name='inspection_count',
            field=models.PositiveIntegerField(
                default=0,
                help_text='Number of inconclusive field inspections submitted for this alert',
            ),
        ),
    ]
