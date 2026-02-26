from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('detections', '0002_remove_region_detections__region__19a43f_idx_and_more'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        # Add escalated_at to Alert
        migrations.AddField(
            model_name='alert',
            name='escalated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        # Create AuditLog
        migrations.CreateModel(
            name='AuditLog',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('action', models.CharField(db_index=True, help_text='Dot-separated action descriptor, e.g. alert.acknowledged', max_length=64)),
                ('object_id', models.CharField(blank=True, db_index=True, help_text='UUID of the primary object affected', max_length=64)),
                ('detail', models.JSONField(default=dict, help_text='Extra context, e.g. previous status, inspector name')),
                ('timestamp', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('user', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='audit_logs',
                    to='auth.user',
                )),
            ],
            options={
                'verbose_name': 'Audit Log',
                'verbose_name_plural': 'Audit Logs',
                'ordering': ['-timestamp'],
                'default_permissions': ('add', 'view'),
            },
        ),
    ]
