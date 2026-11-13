"""Deployment must switch the whole stack to TLS and abort before mutation on error."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def setup_driver(tmp_path, monkeypatch, fail_config=False):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    shutil.copy2(ROOT / 'scripts/ui-deploy.sh', scripts / 'ui-deploy.sh')
    (scripts / 'ui-tests.sh').write_text('#!/bin/sh\nexit 0\n')
    (scripts / 'ui-check-config.sh').write_text(
        '#!/bin/sh\nexit ' + ('4' if fail_config else '0') + '\n')
    commands = tmp_path / 'bin'
    commands.mkdir()
    trace = tmp_path / 'calls.txt'
    for name in ('docker', 'python3'):
        executable = commands / name
        executable.write_text(f'#!/bin/sh\nprintf \'{name} %s\\n\' "$*" >> "$UI_TRACE"\n')
        executable.chmod(0o755)
    monkeypatch.setenv('PATH', str(commands) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('UI_TRACE', str(trace))
    return scripts / 'ui-deploy.sh', trace


@pytest.mark.parametrize('arguments', [[], ['https']])
def test_deploy_recreates_all_services_and_checks_tls(tmp_path, monkeypatch, arguments):
    script, trace = setup_driver(tmp_path, monkeypatch)
    result = subprocess.run(['bash', str(script), *arguments], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    lines = trace.read_text().splitlines()
    deployment = next(line for line in lines if ' up -d ' in line)
    assert '--force-recreate' in deployment
    assert '--no-deps' not in deployment  # Monitoring direct bindings must also be removed.
    assert 'docker-compose.https.yml' not in deployment
    assert any(' build dashboard staff-portal' in line for line in lines)
    for checker in ('https-check.py', 'ui-smoke-check.py', 'auth-check.py'):
        assert any(checker in line and '--ca-file' in line for line in lines)


def test_failed_configuration_does_not_build_or_recreate_services(tmp_path, monkeypatch):
    script, trace = setup_driver(tmp_path, monkeypatch, fail_config=True)
    result = subprocess.run(['bash', str(script)], capture_output=True, text=True)
    assert result.returncode == 4
    assert ' up ' not in trace.read_text() and ' build ' not in trace.read_text()


@pytest.mark.parametrize('argument', ['http', 'invalid'])
def test_http_or_invalid_mode_is_rejected_before_deployment(tmp_path, monkeypatch, argument):
    script, trace = setup_driver(tmp_path, monkeypatch)
    result = subprocess.run(['bash', str(script), argument], capture_output=True, text=True)
    assert result.returncode == 2
    assert not trace.exists()
