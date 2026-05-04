from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scanning', '0003_orgscanconfig'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ghanaplace',
            name='feature_code',
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AlterField(
            model_name='ghanaplace',
            name='source',
            field=models.CharField(default='osm', max_length=20),
        ),
    ]
