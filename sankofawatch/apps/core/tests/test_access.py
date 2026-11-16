"""Role-based access regression tests."""
import pytest
from django.urls import reverse


def _login(client, django_user_model, role):
    from apps.accounts.models import Organisation
    org, _ = Organisation.objects.get_or_create(name='Org1')
    u = django_user_model.objects.create_user(role, f'{role}@example.com', 'pw-12345-xyz')
    u.profile.role = role
    u.profile.organisation = org
    u.profile.save()
    client.force_login(u)
    return u


@pytest.mark.django_db
@pytest.mark.parametrize('name', ['uploads:upload_concessions', 'uploads:upload_districts',
                                  'uploads:upload_water_bodies', 'uploads:upload_protected_forests'])
def test_uploads_post_system_admin_only(client, django_user_model, name):
    _login(client, django_user_model, 'agency_admin')
    assert client.post(reverse(name)).status_code == 403


@pytest.mark.django_db
def test_uploads_page_blocked_for_agency_admin_allowed_for_system_admin(client, django_user_model):
    _login(client, django_user_model, 'agency_admin')
    assert client.get(reverse('uploads:data_uploads')).status_code in (302, 403)
    client.logout()
    _login(client, django_user_model, 'system_admin')
    assert client.get(reverse('uploads:data_uploads')).status_code == 200


@pytest.mark.django_db
def test_inspector_list_admin_only(client, django_user_model):
    _login(client, django_user_model, 'inspector')
    assert client.get('/accounts/api/inspectors/').status_code == 403
    client.logout()
    _login(client, django_user_model, 'agency_admin')
    assert client.get('/accounts/api/inspectors/').status_code == 200


@pytest.mark.django_db
def test_inspector_cannot_start_or_change_jobs(client, django_user_model):
    _login(client, django_user_model, 'inspector')
    assert client.get('/api/jobs/').status_code == 200          # read still works
    assert client.post('/api/jobs/', {}, content_type='application/json').status_code == 403
    assert client.delete('/api/jobs/00000000-0000-0000-0000-000000000000/').status_code == 403


@pytest.mark.django_db
def test_alerts_api_job_filter(client, django_user_model):
    _login(client, django_user_model, 'system_admin')
    for q in ('00000000-0000-0000-0000-000000000000', 'not-a-uuid'):
        r = client.get('/api/alerts/', {'job': q})
        assert r.status_code == 200 and r.json()['total'] == 0


@pytest.mark.django_db
def test_org_create_rename_delete_are_audited(client, django_user_model):
    import json
    from apps.detections.models import AuditLog
    _login(client, django_user_model, 'system_admin')
    url = reverse('dashboard:organisation_management')
    post = lambda body: client.post(url, json.dumps(body), content_type='application/json',
                                    HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    org_id = post({'action': 'create', 'name': 'Audit Org'}).json()['id']
    post({'action': 'rename', 'org_id': org_id, 'name': 'Audit Org 2'})
    post({'action': 'delete', 'org_id': org_id})
    actions = list(AuditLog.objects.filter(action__startswith='org.').order_by('timestamp').values_list('action', flat=True))
    assert actions == ['org.created', 'org.renamed', 'org.deleted']


@pytest.mark.django_db
def test_audit_object_and_detail_filters(client, django_user_model):
    from apps.detections.models import AuditLog
    u = _login(client, django_user_model, 'system_admin')
    AuditLog.objects.create(user=u, action='org.created', object_id='abc123', detail={'name': 'Zebra Org'})
    AuditLog.objects.create(user=u, action='org.created', object_id='xyz789', detail={'name': 'Other'})
    get = lambda q: client.get('/dashboard/audit/', q, HTTP_X_REQUESTED_WITH='XMLHttpRequest').json()['total']
    assert get({'object': 'abc'}) == 1
    assert get({'detail': 'zebra'}) == 1
    assert get({'detail': 'nothing-like-this'}) == 0


@pytest.mark.django_db
def test_geocode_requires_login_and_matches_words_in_any_order(client, django_user_model):
    from apps.scanning.models import GhanaPlace
    assert client.get('/api/geocode/?q=ashesi').status_code == 302
    GhanaPlace.objects.create(name='Ashesi University College', ascii_name='Ashesi University College',
                              latitude=5.76, longitude=-0.22, source='osm', region='Eastern')
    _login(client, django_user_model, 'inspector')
    from unittest.mock import patch
    with patch('requests.get', side_effect=RuntimeError('offline')):
        res = client.get('/api/geocode/?q=university ashesi').json()['results']
    assert res and res[0]['display_name'].startswith('Ashesi University College')
