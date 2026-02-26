import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='NotificationInbox',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('kind', models.CharField(
                    choices=[
                        ('assignment', 'New Assignment'),
                        ('alert',      'Alert'),
                        ('sla',        'SLA / Reminder'),
                        ('report',     'Field Report'),
                        ('system',     'System'),
                    ],
                    default='system',
                    max_length=20,
                )),
                ('title',      models.CharField(max_length=200)),
                ('body',       models.CharField(blank=True, max_length=500)),
                ('link',       models.CharField(blank=True, max_length=300)),
                ('is_read',    models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='notifications',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='notificationinbox',
            index=models.Index(fields=['user', 'is_read'], name='notif_user_read_idx'),
        ),
    ]
