import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_inspectorassignment_alert_fk'),
    ]

    operations = [
        # SLA fields on InspectorAssignment
        migrations.AddField(
            model_name='inspectorassignment',
            name='due_date',
            field=models.DateField(
                blank=True, null=True,
                help_text='SLA deadline for completing this assignment',
            ),
        ),
        migrations.AddField(
            model_name='inspectorassignment',
            name='sla_reminder_sent',
            field=models.BooleanField(
                default=False,
                help_text='Whether the SLA reminder email has been sent to the inspector',
            ),
        ),
        migrations.AddField(
            model_name='inspectorassignment',
            name='sla_escalated',
            field=models.BooleanField(
                default=False,
                help_text='Whether this overdue assignment has been escalated to admins',
            ),
        ),
        # EvidencePhoto model
        migrations.CreateModel(
            name='EvidencePhoto',
            fields=[
                ('id', models.UUIDField(
                    default=uuid.uuid4, editable=False,
                    primary_key=True, serialize=False,
                )),
                ('file', models.ImageField(
                    upload_to='inspections/%Y/%m/',
                    help_text='Uploaded evidence photo',
                )),
                ('sha256_hash', models.CharField(
                    blank=True, db_index=True, max_length=64,
                    help_text='SHA-256 of the file bytes for deduplication and integrity checks',
                )),
                ('original_name', models.CharField(
                    blank=True, max_length=255,
                    help_text='Original filename as uploaded by the inspector',
                )),
                ('uploaded_at', models.DateTimeField(auto_now_add=True)),
                ('assignment', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='evidence_photo_set',
                    to='accounts.inspectorassignment',
                )),
            ],
            options={
                'verbose_name': 'Evidence Photo',
                'verbose_name_plural': 'Evidence Photos',
                'ordering': ['uploaded_at'],
            },
        ),
    ]
