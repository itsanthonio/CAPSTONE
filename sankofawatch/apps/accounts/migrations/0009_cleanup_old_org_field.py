from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0008_data_migration'),
    ]

    operations = [
        # Remove the old organization CharField
        migrations.RemoveField(
            model_name='userprofile',
            name='organization',
        ),
        # Remove 'admin' from role choices now that all data is migrated
        migrations.AlterField(
            model_name='userprofile',
            name='role',
            field=models.CharField(
                choices=[
                    ('system_admin', 'System Administrator'),
                    ('agency_admin', 'Agency Administrator'),
                    ('inspector', 'Inspector'),
                ],
                default='inspector',
                max_length=20,
            ),
        ),
    ]
