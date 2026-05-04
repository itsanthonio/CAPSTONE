from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scanning', '0005_alter_ghanaplace_source'),
    ]

    operations = [
        migrations.AddField(
            model_name='orgscanconfig',
            name='paused_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
