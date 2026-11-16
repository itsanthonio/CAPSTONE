from unittest.mock import patch
import requests
from apps.gee.services import GeeService


def _svc():
    s = GeeService.__new__(GeeService)
    s._ee_initialized = False
    s._init_error = ''
    return s


def _cfg(key, default=''):
    return 'proj' if key == 'GEE_PROJECT_ID' else default


def test_login_retries_after_network_blip_then_succeeds():
    s = _svc()
    boom = requests.exceptions.ConnectionError('Network is unreachable')
    with patch('apps.gee.services.config', _cfg), patch('apps.gee.services.time.sleep') as sl, \
         patch('apps.gee.services.ee.Initialize', side_effect=[boom, boom, None]) as init:
        s._authenticate()
    assert s._ee_initialized and init.call_count == 3 and sl.call_count == 2


def test_login_gives_clear_message_when_google_stays_unreachable():
    s = _svc()
    boom = requests.exceptions.ConnectionError('Network is unreachable')
    with patch('apps.gee.services.config', _cfg), patch('apps.gee.services.time.sleep'), \
         patch('apps.gee.services.ee.Initialize', side_effect=boom) as init:
        s._authenticate()
    assert not s._ee_initialized and init.call_count == 5
    assert 'network problem' in s.user_facing_init_error()


def test_bad_credentials_are_not_retried():
    s = _svc()
    with patch('apps.gee.services.config', _cfg), patch('apps.gee.services.time.sleep') as sl, \
         patch('apps.gee.services.ee.Initialize', side_effect=ValueError('Please authorize access')) as init:
        s._authenticate()
    assert init.call_count == 1 and sl.call_count == 0
    assert 'not available' in s.user_facing_init_error()
