"""Portal action audit fields, response accuracy and secret exclusion."""
import io
import json
from datetime import datetime
from unittest.mock import patch

import pytest

from test_portal import load, login, page_token


@pytest.fixture(params=['staff', 'guest'])
def portal(monkeypatch, tmp_path, request):
    mod = load(monkeypatch, request.param, 'alice:StaffPass1', tmp_path / request.param)
    return mod, mod.app.test_client()


def record(mock):
    return json.loads(mock.call_args.args[0])


def test_roles_log_login_success_failure_and_safe_request_fields(portal):
    mod, c = portal
    with patch.object(mod.request_logger, 'info') as output:
        response = login(c, 'alice', 'StaffPass1', headers={
            'X-Request-ID': 'a' * 32, 'X-Real-IP': '172.22.0.10', 'X-Zone': 'staff'})
        data = record(output)
        assert data['event'] == 'login_succeeded'
        assert data['service'] == mod.ROLE + '-portal' and data['role'] == mod.ROLE
        assert data['user'] == 'alice' and data['status'] == response.status_code == 302
        assert data['request_id'] == response.headers['X-Request-ID'] == 'a' * 32
        assert data['response_ms'] >= 0 and data['timestamp'].endswith('Z')
        assert datetime.fromisoformat(data['timestamp'].replace('Z', '+00:00')).tzinfo
        assert 'StaffPass1' not in output.call_args.args[0]
    c.post('/logout', data={'csrf_token': page_token(c)})
    with patch.object(mod.request_logger, 'info') as output:
        response = login(c, 'unknown-secret-name', 'wrong-secret')
        data = record(output)
        assert data['event'] == 'login_failed' and data['status'] == 401
        assert data['level'] == 'warning' and data['user'] is None
        assert 'unknown-secret-name' not in output.call_args.args[0]
        assert 'wrong-secret' not in output.call_args.args[0]


def test_csrf_rejection_and_unauthenticated_api_are_logged(portal):
    mod, c = portal
    with patch.object(mod.request_logger, 'info') as output:
        assert c.post('/login', data={'password': 'do-not-log'}).status_code == 400
        assert record(output)['event'] == 'csrf_rejected'
        assert 'do-not-log' not in output.call_args.args[0]
        assert c.get('/api/whoami').status_code == 401
        assert record(output)['event'] == 'authentication_required'


def test_successful_health_and_static_requests_are_quiet_but_failures_are_not(portal):
    mod, c = portal
    with patch.object(mod.request_logger, 'info') as output:
        assert c.get('/api/health').status_code == 200
        assert c.get('/static/js/portal.js').status_code == 200
        output.assert_not_called()
        assert c.get('/static/missing.js').status_code == 404
        assert record(output)['status'] == 404


def test_no_queries_cookie_values_or_unmatched_path_secrets_in_logs(portal):
    mod, c = portal
    login(c, 'alice', 'StaffPass1')
    cookie = c.get_cookie(mod.ROLE + '_session').value
    csrf = page_token(c)
    with patch.object(mod.request_logger, 'info') as output:
        c.get('/api/whoami?token=secret-query', headers={'X-Request-ID': 'invalid\tvalue'})
        data = record(output)
        assert len(data['request_id']) == 32 and data['request_id'] != 'invalid\tvalue'
        c.get('/unmatched-secret-path?token=secret-query')
        assert record(output)['route'] == '<unmatched>'
        serialized = '\n'.join(x.args[0] for x in output.call_args_list)
        for secret in ['secret-query', 'unmatched-secret-path', cookie, csrf]:
            assert secret not in serialized


def test_logout_actor_is_retained_after_session_is_cleared(portal):
    mod, c = portal
    login(c, 'alice', 'StaffPass1')
    csrf = page_token(c)
    with patch.object(mod.request_logger, 'info') as output:
        assert c.post('/logout', data={'csrf_token': csrf}).status_code == 302
        assert record(output)['event'] == 'logout_succeeded'
        assert record(output)['user'] == 'alice'


def test_guest_limit_rejection_and_stream_start_are_not_reported_as_completion(portal):
    mod, c = portal
    login(c, 'alice', 'StaffPass1')
    with patch.object(mod.request_logger, 'info') as output:
        c.get('/speedtest?mb=1').close()
        assert record(output)['event'] == 'test_download_requested'
        assert 'download_completed' not in output.call_args.args[0]
        assert c.get('/speedtest?mb=999').status_code == 403
        assert record(output)['event'] == 'download_limit_rejected'


def test_staff_notes_uploads_conflicts_and_downloads(monkeypatch, tmp_path):
    mod = load(monkeypatch, 'staff', 'alice:StaffPass1', tmp_path)
    c = mod.app.test_client()
    login(c, 'alice', 'StaffPass1')
    csrf = page_token(c)
    with patch.object(mod.request_logger, 'info') as output:
        c.post('/notes', data={'csrf_token': csrf, 'text': 'private notice content'})
        assert record(output)['event'] == 'note_created'
        assert 'private notice content' not in output.call_args.args[0]
        for expected in ['upload_succeeded', 'upload_conflict']:
            c.post('/files', data={'csrf_token': csrf,
                                   'file': (io.BytesIO(b'private-content'), 'private-name.txt')})
            assert record(output)['event'] == expected
            assert 'private-name' not in output.call_args.args[0]
        assert record(output)['status'] == 409
        c.get('/files/private-name.txt').close()
        assert record(output)['event'] == 'file_download_requested'
        assert record(output)['route'] == '/files/<name>'
        assert 'private-name' not in output.call_args.args[0]
