from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_userpreferences'),
    ]

    operations = [
        migrations.CreateModel(
            name='Organisation',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=100, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Organisation',
                'verbose_name_plural': 'Organisations',
                'ordering': ['name'],
            },
        ),
        migrations.AddField(
            model_name='userprofile',
            name='organisation',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='members',
                to='accounts.organisation',
            ),
        ),
        migrations.AlterField(
            model_name='userprofile',
            name='role',
            field=models.CharField(
                choices=[
                    ('system_admin', 'System Administrator'),
                    ('agency_admin', 'Agency Administrator'),
                    ('inspector', 'Inspector'),
                    ('admin', 'Admin'),  # kept temporarily for existing data
                ],
                default='inspector',
                max_length=20,
            ),
        ),
    ]
