"""Tests for the system health checks and the health_check management command."""
from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command

from apps.core import health


@pytest.mark.django_db
def test_collect_health_returns_structured_report():
    report = health.collect_health(include_workers=False)
    assert report['overall'] in (health.OK, health.WARN, health.CRIT)
    codes = {c['code'] for c in report['checks']}
    assert {'database', 'disk', 'scans'} <= codes
    for c in report['checks']:
        assert c['level'] in (health.OK, health.WARN, health.CRIT)
        assert c['label'] and c['detail']


@pytest.mark.django_db
def test_failing_check_is_reported_not_raised():
    # a broken dependency must be reported as a warning, never raised
    with mock.patch('shutil.disk_usage', side_effect=OSError('no disk')):
        c = health._disk()
    assert c['level'] == health.WARN and 'no disk' in c['detail']


def test_disk_levels(monkeypatch):
    Usage = type('U', (), {})
    for pct, expected in ((50, health.OK), (88, health.WARN), (95, health.CRIT)):
        u = Usage(); u.total = 100; u.used = pct; u.free = 100 - pct
        monkeypatch.setattr('shutil.disk_usage', lambda _p, u=u: u)
        assert health._disk()['level'] == expected


@pytest.mark.django_db
def test_health_check_command_prints_without_sending(settings):
    settings.OPS_EMAILS = ['ops@example.com']
    out = StringIO()
    with mock.patch('apps.core.management.commands.health_check.send_mail') as send:
        call_command('health_check', '--print', stdout=out)
    assert 'Database' in out.getvalue()
    send.assert_not_called()


@pytest.mark.django_db
def test_health_check_emails_once_then_stays_quiet(settings, tmp_path):
    settings.OPS_EMAILS = ['ops@example.com']
    fake = {'checks': [], 'overall': health.CRIT, 'at': None,
            'issues': [{'code': 'disk', 'label': 'Disk space', 'level': health.CRIT, 'detail': '95% used'}]}
    from apps.core.management.commands import health_check as hc
    with mock.patch.object(hc, 'STATE_FILE', tmp_path / 'state.json'), \
         mock.patch.object(hc, 'collect_health', return_value=fake), \
         mock.patch.object(hc, 'send_mail') as send:
        call_command('health_check', stdout=StringIO())
        call_command('health_check', stdout=StringIO())   # same problem, within 6h: no repeat
    assert send.call_count == 1


@pytest.mark.django_db
def test_warning_needs_two_consecutive_checks(settings, tmp_path):
    settings.OPS_EMAILS = ['ops@example.com']
    warn = {'checks': [], 'overall': health.WARN, 'at': None,
            'issues': [{'code': 'workers', 'label': 'Celery workers', 'level': health.WARN, 'detail': '9 of 15 online'}]}
    from apps.core.management.commands import health_check as hc
    with mock.patch.object(hc, 'STATE_FILE', tmp_path / 'state.json'),          mock.patch.object(hc, 'collect_health', return_value=warn),          mock.patch.object(hc, 'send_mail') as send:
        call_command('health_check', stdout=StringIO())
        assert send.call_count == 0          # first sighting: wait
        call_command('health_check', stdout=StringIO())
        assert send.call_count == 1          # still there on the next run: alert


@pytest.mark.django_db
def test_health_data_endpoint_is_system_admin_only(client, django_user_model):
    from django.urls import reverse
    url = reverse('dashboard:system_health_data')

    assert client.get(url).status_code == 302            # anonymous -> login redirect

    inspector = django_user_model.objects.create_user('insp', 'i@example.com', 'pw-12345-xyz')
    inspector.profile.role = 'inspector'; inspector.profile.save()
    client.force_login(inspector)
    assert client.get(url).status_code == 302            # not allowed

    admin = django_user_model.objects.create_user('sysadm', 'a@example.com', 'pw-12345-xyz')
    admin.profile.role = 'system_admin'; admin.profile.save()
    client.force_login(admin)
    with mock.patch.object(health, '_workers', return_value=health._check('workers', 'Celery workers', health.OK, '15 online')):
        resp = client.get(url)
    assert resp.status_code == 200
    data = resp.json()
    assert data['overall'] in ('ok', 'warn', 'crit') and data['checks'] and 'at' in data


@pytest.mark.django_db
def test_old_system_health_url_redirects_to_dashboard_tab(client, django_user_model):
    from django.urls import reverse
    admin = django_user_model.objects.create_user('sysadm2', 'a2@example.com', 'pw-12345-xyz')
    admin.profile.role = 'system_admin'; admin.profile.save()
    client.force_login(admin)
    resp = client.get(reverse('dashboard:system_health'))
    assert resp.status_code == 302 and resp['Location'].endswith('#health')
