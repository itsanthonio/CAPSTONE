from django.db import migrations


OLD_ORG_CHOICES = {
    'epa': 'Environmental Protection Agency',
    'minerals_commission': 'Minerals Commission',
    'cersgis': 'CERSGIS',
    'forestry_commission': 'Forestry Commission',
    'other': 'Other',
}


def migrate_forward(apps, schema_editor):
    Organisation = apps.get_model('accounts', 'Organisation')
    UserProfile = apps.get_model('accounts', 'UserProfile')

    # Create one Organisation per existing choice
    org_map = {}
    for key, display_name in OLD_ORG_CHOICES.items():
        org, _ = Organisation.objects.get_or_create(name=display_name)
        org_map[key] = org

    # Assign organisation FK and update roles
    for profile in UserProfile.objects.select_related('user').all():
        old_org = profile.organization  # old CharField
        if old_org in org_map:
            profile.organisation = org_map[old_org]

        username = profile.user.username
        if username == 'mcnob':
            profile.role = 'system_admin'
            profile.organisation = None  # system admin has no org
        elif username == 'Test':
            profile.role = 'agency_admin'
            profile.organisation = org_map['epa']
        elif profile.role == 'admin':
            # Any other existing admins become agency_admin
            profile.role = 'agency_admin'

        profile.save()


def migrate_backward(apps, schema_editor):
    UserProfile = apps.get_model('accounts', 'UserProfile')
    for profile in UserProfile.objects.all():
        if profile.role in ('system_admin', 'agency_admin'):
            profile.role = 'admin'
            profile.save()


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0007_organisation_role_split'),
    ]

    operations = [
        migrations.RunPython(migrate_forward, migrate_backward),
    ]
