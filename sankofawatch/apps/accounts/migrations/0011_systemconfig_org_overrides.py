from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0010_add_alert_severity_report_days'),
    ]

    operations = [
        migrations.CreateModel(
            name='SystemConfig',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sla_days', models.IntegerField(default=5, help_text='Default days from assignment creation to SLA deadline')),
                ('max_pending_assignments', models.IntegerField(default=10, help_text='Default maximum pending assignments per inspector before they are blocked')),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'System Configuration',
            },
        ),
        migrations.AddField(
            model_name='organisation',
            name='sla_days_override',
            field=models.IntegerField(
                blank=True,
                null=True,
                help_text='Override the system-wide SLA days for inspectors in this organisation. Leave blank to use the system default.',
            ),
        ),
        migrations.AddField(
            model_name='organisation',
            name='max_pending_override',
            field=models.IntegerField(
                blank=True,
                null=True,
                help_text='Override the system-wide max pending assignments for inspectors in this organisation. Leave blank to use the system default.',
            ),
        ),
    ]
