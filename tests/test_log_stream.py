"""Live log framing, authentication and bounded-resource regression tests."""
import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from docker.errors import DockerException

import test_dashboard_api as api
from test_dashboard_api import dash, fake
import log_stream


@pytest.fixture()
def client():
    yield from api.client.__wrapped__()


class Source:
    def __init__(self, chunks=()):
        self.chunks = chunks
        self.closed = False

    def __iter__(self):
        return iter(self.chunks)

    def close(self):
        self.closed = True


def unpack(text):
    return text.splitlines()[0][7:], json.loads(text.splitlines()[1][6:])


def test_split_unicode_and_identical_records_are_preserved():
    data = '2026-11-17T10:00:00.123456789Z ආයුබෝවන්\nrepeat\nrepeat\n'.encode()
    chunks = [data[:33], data[33:35], data[35:38], data[38:]]
    assert list(log_stream.log_lines(chunks)) == [
        '2026-11-17T10:00:00.123456789Z ආයුබෝවන්', 'repeat', 'repeat']


def test_long_line_is_bounded_and_next_line_survives():
    lines = list(log_stream.log_lines([b'a' * 20000, b'\nnext\npartial']))
    assert lines[0] == 'a' * 16384 + ' [line truncated at 16384 bytes]'
    assert lines[1:] == ['next', 'partial']


def test_source_close_and_slot_release_are_idempotent():
    release = MagicMock()
    source = Source([b'first\nsecond\n'])
    sub = log_stream.LogSubscription(source, {'name': 'staff-portal'}, lambda: True, release)
    events = list(sub.events())
    assert [unpack(e)[0] for e in events] == ['reset', 'log', 'log', 'end']
    assert [unpack(e)[1]['line'] for e in events[1:3]] == ['first', 'second']
    assert source.closed
    sub.close()
    release.assert_called_once()


def test_revoked_session_gets_no_log_lines():
    source = Source([b'private\n'])
    sub = log_stream.LogSubscription(source, {}, lambda: False, lambda: None)
    assert [unpack(e)[0] for e in sub.events()] == ['reset', 'auth_expired']
    assert source.closed


def test_open_failure_releases_capacity(monkeypatch):
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(log_stream, 'STREAM_SLOTS', slots)
    c = fake('staff-portal')
    c.logs.side_effect = DockerException('not available')
    with pytest.raises(DockerException):
        log_stream.subscribe(c, 100, lambda: True)
    assert slots.acquire(blocking=False)


def test_capacity_is_bounded_and_never_opens_an_extra_source(monkeypatch):
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(log_stream, 'STREAM_SLOTS', slots)
    c = fake('staff-portal')
    c.logs.return_value = Source()
    sub = log_stream.subscribe(c, 10, lambda: True)
    with pytest.raises(log_stream.LogCapacityError):
        log_stream.subscribe(c, 10, lambda: True)
    assert c.logs.call_count == 1
    sub.close()
    second = log_stream.subscribe(c, 10, lambda: True)
    second.close()


def test_disconnect_cancels_blocking_reader_and_releases_slot():
    closed = threading.Event()

    class IdleSource:
        def __iter__(self):
            closed.wait(timeout=2)
            return iter(())

        def close(self):
            closed.set()

    release = MagicMock()
    sub = log_stream.LogSubscription(IdleSource(), {}, lambda: True, release)
    gen = sub.events()
    assert unpack(next(gen))[0] == 'reset'
    gen.close()
    assert closed.is_set() and not sub.reader.is_alive()
    release.assert_called_once()


def test_heartbeats_recheck_session_and_release_idle_stream(monkeypatch):
    monkeypatch.setattr(log_stream, 'HEARTBEAT_SECONDS', .01)
    closed = threading.Event()

    class IdleSource:
        def __iter__(self):
            closed.wait(timeout=1)
            return iter(())

        def close(self):
            closed.set()

    authorized = MagicMock(side_effect=[True, True, False])
    sub = log_stream.LogSubscription(IdleSource(), {}, authorized, lambda: None)
    events = [unpack(x)[0] for x in sub.events()]
    assert events[0] == 'reset' and 'heartbeat' in events and events[-1] == 'auth_expired'
    assert closed.is_set()


def test_source_error_is_a_stream_event_not_fake_log_content():
    class BrokenSource(Source):
        def __iter__(self):
            raise RuntimeError('sensitive internal error')

    source = BrokenSource()
    sub = log_stream.LogSubscription(source, {}, lambda: True, lambda: None)
    events = list(sub.events())
    assert [unpack(e)[0] for e in events] == ['reset', 'problem']
    assert 'sensitive internal error' not in ''.join(events)
    assert source.closed


def test_stream_requires_admin_and_project_membership(client):
    anon = dash.app.test_client()
    assert anon.get('/api/containers/nginx-web/logs/stream').status_code == 401
    assert client[0].get('/api/containers/foreign/logs/stream').status_code == 404
    assert client[0].get('/api/containers/missing/logs/stream').status_code == 404


def test_route_tails_original_timestamps_and_closes(client):
    browser, c = client
    source = Source([b'2026-11-17T10:00:00.123456789Z first\n'])
    c.logs.return_value = source
    response = browser.get('/api/containers/nginx-web/logs/stream?tail=9999')
    try:
        assert response.mimetype == 'text/event-stream'
        assert response.headers['X-Accel-Buffering'] == 'no'
        assert response.headers['Cache-Control'] == 'no-store'
        assert b'2026-11-17T10:00:00.123456789Z first' in response.data
        c.logs.assert_called_once_with(tail=500, timestamps=True, stdout=True, stderr=True,
                                       stream=True, follow=True)
    finally:
        response.close()
    assert source.closed


def test_stream_tail_validation_and_capacity_response(client):
    assert client[0].get('/api/containers/nginx-web/logs/stream?tail=no').status_code == 400
    with patch.object(dash, 'subscribe', side_effect=log_stream.LogCapacityError('busy')):
        assert client[0].get('/api/containers/nginx-web/logs/stream').status_code == 503


def test_snapshot_identity_and_resolved_name(client):
    d = client[0].get('/api/containers/nginx-web/logs').json
    assert d['timestamp_basis'] == 'Docker UTC' and d['container_id'] == 'abc123'
    assert d['lines'] == ['2026 line1', '2026 line2']


def test_gateway_stream_and_readable_log_rotation_are_configured():
    import yaml
    root = Path(__file__).resolve().parents[1]
    cfg = yaml.safe_load((root / 'docker-compose.yml').read_text())
    for svc in cfg['services'].values():
        assert svc['logging'] == {'driver': 'json-file', 'options': {
            'max-size': '10m', 'max-file': '3'}}
    nginx = (root / 'services/reverse-proxy/default.conf').read_text()
    assert nginx.count('proxy_set_header X-Request-ID $request_id;') == 8
    assert nginx.count('set $dashboard http://dashboard:5000;\n') == 2
    assert 'log_format zonelog escape=json' in nginx
    assert '"$request"' not in nginx
    assert 'log-queries=extra' in (root / 'services/dns/dnsmasq.conf').read_text()
