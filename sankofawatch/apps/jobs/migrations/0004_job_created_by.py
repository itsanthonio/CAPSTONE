from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0003_job_detection_data_job_illegal_count_job_result_id_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='job',
            name='created_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                help_text='Admin user who triggered this scan',
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='jobs',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
