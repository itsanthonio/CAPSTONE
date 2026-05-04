from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0012_add_organisation_to_job'),
        ('detections', '0008_inspection_inspector_set_null'),
    ]

    operations = [
        migrations.AlterField(
            model_name='inspectorassignment',
            name='alert',
            field=models.ForeignKey(
                db_column='alert_id',
                on_delete=django.db.models.deletion.PROTECT,
                related_name='assignments',
                to='detections.alert',
            ),
        ),
        migrations.AlterField(
            model_name='inspectorassignment',
            name='inspector',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='inspector_assignments',
                to='accounts.userprofile',
            ),
        ),
        migrations.AlterUniqueTogether(
            name='inspectorassignment',
            unique_together={('alert', 'inspector')},
        ),
    ]
