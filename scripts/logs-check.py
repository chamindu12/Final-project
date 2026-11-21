#!/usr/bin/env python3
"""Verify portal/gateway/DNS records through the authenticated HTTPS log stream.

This measures request-to-SSE observation, not browser rendering latency.
"""
import argparse
import importlib.util
import json
import ssl
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('netlab_auth_check', ROOT / 'scripts/auth-check.py')
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)


@contextmanager
def stream(browser, name):
    response = None
    for attempt in range(7):
        try:
            response = browser.opener.open(Request(
                browser.base + f'/api/containers/{name}/logs/stream?tail=50'), timeout=8)
            break
        except HTTPError as exc:
            if exc.code != 503 or attempt == 6:
                raise RuntimeError(f'Cannot open {name} stream: HTTP {exc.code}') from None
            exc.close()
            time.sleep(.5)  # let an earlier closed stream release its bounded worker slot
    try:
        if response.headers.get_content_type() != 'text/event-stream':
            raise RuntimeError('Proxy did not return an event stream')
        yield response
    finally:
        response.close()


def events(response):
    kind = None
    for raw in response:
        line = raw.decode('utf-8').rstrip('\r\n')
        if line.startswith('event: '):
            kind = line[7:]
        elif line.startswith('data: '):
            yield kind, json.loads(line[6:])


def wait_for_log(messages, predicate, started, limit):
    for kind, data in messages:
        if kind in {'problem', 'auth_expired', 'end'}:
            raise RuntimeError(f'Stream ended before the expected record: {kind}')
        if kind == 'log' and predicate(data['line']):
            elapsed = time.monotonic() - started
            if elapsed > limit:
                raise RuntimeError(f'Record took {elapsed:.3f}s (limit {limit:.3f}s)')
            return elapsed
        if time.monotonic() - started > 8:
            break
    raise RuntimeError('Expected log record was not observed')


def json_record(line):
    _, _, body = line.partition(' ')
    try:
        data = json.loads(body)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ca-file', required=True)
    parser.add_argument('--max-seconds', type=float, default=5,
                        help='request-to-SSE budget; this is not a browser latency measurement')
    options = parser.parse_args()
    if not 0 < options.max_seconds <= 8:
        parser.error('--max-seconds must be greater than 0 and at most 8')
    _, values = auth.configuration()
    context = ssl.create_default_context(cafile=options.ca_file)
    admin = auth.Browser('https://localhost:8080/dashboard', context=context)
    auth.check(admin.request('/api/containers/staff-portal/logs/stream')[0], 401,
               'anonymous stream denied')
    auth.login(admin, 'admin', values)
    try:
        for role, port in [('staff', 8081), ('guest', 8082)]:
            portal = auth.Browser(f'https://localhost:{port}',
                                  pause=.6 if role == 'guest' else 0, context=context)
            auth.login(portal, role, values)
            with stream(admin, role + '-portal') as response:
                messages = events(response)
                kind, data = next(messages)
                if kind != 'reset' or data['name'] != role + '-portal':
                    raise RuntimeError('Wrong container identity in stream')
                started = time.monotonic()
                status, _, headers = portal.request('/api/whoami')
                auth.check(status, 200, role + ' activity request')
                request_id = headers.get('X-Request-ID')
                if not request_id:
                    raise RuntimeError('Gateway request ID header missing')
                elapsed = wait_for_log(messages, lambda line: (
                    json_record(line).get('request_id') == request_id
                    and json_record(line).get('service') == role + '-portal'
                    and json_record(line).get('route') == '/api/whoami'
                    and json_record(line).get('status') == 200
                ), started, options.max_seconds)
                print(f'PASS {role} request correlated with live container record: {elapsed:.3f}s')

        with stream(admin, 'nginx-proxy') as response:
            messages = events(response)
            next(messages)
            started = time.monotonic()
            web = auth.Browser('https://localhost:8080', context=context)
            status, _, headers = web.request('/web/')
            auth.check(status, 200, 'gateway activity request')
            request_id = headers.get('X-Request-ID')
            if not request_id:
                raise RuntimeError('Gateway request ID missing')
            elapsed = wait_for_log(messages, lambda line: (
                json_record(line).get('request_id') == request_id
                and json_record(line).get('event') == 'gateway_request'
            ), started, options.max_seconds)
            print(f'PASS gateway request ID appears in live logs: {elapsed:.3f}s')

        with stream(admin, 'dnsmasq') as response:
            messages = events(response)
            next(messages)
            marker = 'logs-' + uuid.uuid4().hex + '.portal.lab.local'
            started = time.monotonic()
            result = subprocess.run([
                'docker', 'compose', 'exec', '-T', 'staff-client', 'dig', '+short',
                '+time=2', '+tries=1', '@172.22.0.53', '-p', '5353', marker,
            ], cwd=ROOT, text=True, capture_output=True, timeout=5)
            if result.returncode or '172.22.0.11' not in result.stdout:
                raise RuntimeError('DNS activity did not resolve the staff gateway')
            elapsed = wait_for_log(messages, lambda line: marker in line,
                                   started, options.max_seconds)
            print(f'PASS unique DNS query appears in live logs: {elapsed:.3f}s')

        with stream(admin, 'staff-portal') as response:
            messages = events(response)
            next(messages)
            csrf = auth.form_token(admin, '/')
            auth.check(admin.request('/logout', {'csrf_token': csrf})[0], 302, 'admin sign out')
            started = time.monotonic()
            for kind, _ in messages:
                if kind == 'auth_expired':
                    print('PASS open stream terminates after session revocation')
                    break
                if kind in {'end', 'problem'} or time.monotonic() - started > 6:
                    raise RuntimeError('Open stream did not report revoked authentication')
            else:
                raise RuntimeError('Stream closed without reporting revoked authentication')
        print('Live log checks passed (SSE observation; browser timing requires its own capture).')
    finally:
        # Revoke the temporary check session even if a live check failed midway.
        if admin.request('/api/containers')[0] == 200:
            csrf = auth.form_token(admin, '/')
            admin.request('/logout', {'csrf_token': csrf})


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, StopIteration, subprocess.SubprocessError) as error:
        raise SystemExit(f'FAIL {error}') from None
