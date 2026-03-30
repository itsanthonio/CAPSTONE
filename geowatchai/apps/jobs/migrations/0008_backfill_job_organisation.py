from django.db import migrations


def backfill_job_organisation(apps, schema_editor):
    """
    For every existing job that has a created_by user, copy that user's
    organisation onto job.organisation. Automated jobs (created_by=None)
    stay null — they are intentionally visible to all orgs.
    """
    Job = apps.get_model('jobs', 'Job')
    UserProfile = apps.get_model('accounts', 'UserProfile')

    # Build user_id -> organisation_id map in one query
    profile_map = {
        p.user_id: p.organisation_id
        for p in UserProfile.objects.filter(organisation__isnull=False)
    }

    to_update = []
    for job in Job.objects.filter(created_by__isnull=False, organisation__isnull=True):
        org_id = profile_map.get(job.created_by_id)
        if org_id:
            job.organisation_id = org_id
            to_update.append(job)

    if to_update:
        Job.objects.bulk_update(to_update, ['organisation'], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0007_add_organisation_to_job'),
    ]

    operations = [
        migrations.RunPython(backfill_job_organisation, migrations.RunPython.noop),
    ]
