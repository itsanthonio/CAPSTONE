"""
Convert InspectorAssignment.alert_id from a bare UUIDField to a proper
ForeignKey to detections.Alert.

The database column name (alert_id) is unchanged, so we use
SeparateDatabaseAndState to update Django's state without dropping and
recreating the column — we just add the FK constraint.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_inspectorassignment_outcome_visitdate_photos'),
        ('detections', '0002_remove_region_detections__region__19a43f_idx_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            # Tell Django's migration state: old field gone, new FK added.
            state_operations=[
                migrations.RemoveField(
                    model_name='inspectorassignment',
                    name='alert_id',
                ),
                migrations.AddField(
                    model_name='inspectorassignment',
                    name='alert',
                    field=models.ForeignKey(
                        db_column='alert_id',
                        on_delete=models.deletion.CASCADE,
                        related_name='assignments',
                        to='detections.alert',
                    ),
                ),
            ],
            # In the DB: the column already exists with the right values;
            # just add the FK constraint.
            database_operations=[
                migrations.RunSQL(
                    sql="""
                        ALTER TABLE accounts_inspectorassignment
                        ADD CONSTRAINT accounts_inspectorassignment_alert_fk
                        FOREIGN KEY (alert_id)
                        REFERENCES detections_alert (id)
                        ON DELETE CASCADE
                        DEFERRABLE INITIALLY DEFERRED;
                    """,
                    reverse_sql="""
                        ALTER TABLE accounts_inspectorassignment
                        DROP CONSTRAINT IF EXISTS accounts_inspectorassignment_alert_fk;
                    """,
                ),
            ],
        ),
    ]
