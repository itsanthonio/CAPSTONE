from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0011_systemconfig_org_overrides'),
        ('scanning', '0002_ghanaplace_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrgScanConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_enabled', models.BooleanField(default=True)),
                ('window_start_hour', models.IntegerField(default=6)),
                ('window_end_hour', models.IntegerField(default=18)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('organisation', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='scan_config',
                    to='accounts.organisation',
                )),
            ],
            options={'verbose_name': 'Org Scan Config'},
        ),
    ]
