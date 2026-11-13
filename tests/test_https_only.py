"""HTTPS-only topology and real certificate preparation, without Docker or credentials."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_all_browser_ports_terminate_tls_and_backends_are_private():
    services = yaml.safe_load((ROOT / 'docker-compose.yml').read_text())['services']
    assert services['nginx-proxy']['ports'] == [
        '127.0.0.1:8080:8080', '127.0.0.1:8081:8081', '127.0.0.1:8082:8082',
        '127.0.0.1:3000:8083', '127.0.0.1:9090:8084']
    for service in ('prometheus', 'grafana'):
        assert services['nginx-proxy']['depends_on'][service] == {'condition': 'service_healthy'}
    conf = (ROOT / 'services/reverse-proxy/default.conf').read_text()
    for port in range(8080, 8085):
        assert f'listen {port} ssl;' in conf
    assert conf.count('listen ') == 5
    for service in ('dashboard', 'staff-portal', 'guest-portal'):
        assert services[service]['environment']['SESSION_COOKIE_SECURE'] == '1'
    for service in ('dashboard', 'staff-portal', 'guest-portal', 'nginx-web',
                    'prometheus', 'grafana', 'cadvisor'):
        assert not services[service].get('ports')
    assert services['grafana']['environment']['GF_SECURITY_COOKIE_SECURE'] == 'true'
    assert not (ROOT / 'docker-compose.https.yml').exists()
    assert not (ROOT / 'services/reverse-proxy/https.conf').exists()


@pytest.fixture()
def tls_workspace(tmp_path):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    shutil.copy2(ROOT / 'scripts/tls-setup.py', scripts / 'tls-setup.py')
    (tmp_path / '.env').write_text(
        'ADMIN_USERS=admin:KeepThisPassword\nPORTAL_SECRET_KEY=KeepThisLongSecret\n'
        'HTTPS_ADMIN_PORT=8443\nPROXY_PORT=8080\nSESSION_COOKIE_SECURE=0\n')
    home = tmp_path / 'home'
    home.mkdir()
    env = dict(os.environ, HOME=str(home), TLS_CERT_DIR=str(tmp_path / 'certs'),
               TLS_CERT_GID=str(os.getgid()))
    return tmp_path, env


def tls_run(root, env, *args):
    return subprocess.run(['python3', str(root / 'scripts/tls-setup.py'), *args],
                          env=env, capture_output=True, text=True)


def test_tls_setup_generates_checks_preserves_accounts_and_is_idempotent(tls_workspace):
    root, env = tls_workspace
    result = tls_run(root, env)
    assert result.returncode == 0, result.stderr
    values = (root / '.env').read_text()
    assert 'ADMIN_USERS=admin:KeepThisPassword' in values
    assert 'PORTAL_SECRET_KEY=KeepThisLongSecret' in values
    assert 'HTTPS_ADMIN_PORT' not in values and 'PROXY_PORT' not in values
    assert 'SESSION_COOKIE_SECURE' not in values
    cert = Path(env['TLS_CERT_DIR']) / 'netlab.crt'
    key = cert.with_suffix('.key')
    original = (cert.read_bytes(), key.read_bytes(), values)
    assert key.stat().st_mode & 0o777 == 0o640
    assert (root / '.env').stat().st_mode & 0o777 == 0o600
    assert tls_run(root, env, '--check').returncode == 0
    assert tls_run(root, env).returncode == 0
    assert (cert.read_bytes(), key.read_bytes(), (root / '.env').read_text()) == original


def test_partial_pair_is_refused_without_overwriting(tls_workspace):
    root, env = tls_workspace
    directory = Path(env['TLS_CERT_DIR'])
    directory.mkdir()
    (directory / 'netlab.key').write_text('do not overwrite')
    result = tls_run(root, env)
    assert result.returncode != 0
    assert (directory / 'netlab.key').read_text() == 'do not overwrite'


def test_mismatched_pair_is_refused(tls_workspace, tmp_path):
    root, env = tls_workspace
    assert tls_run(root, env).returncode == 0
    key = Path(env['TLS_CERT_DIR']) / 'netlab.key'
    subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-out', str(key),
                    '-pkeyopt', 'rsa_keygen_bits:2048'], check=True, capture_output=True)
    result = tls_run(root, env, '--check')
    assert result.returncode != 0 and 'do not match' in result.stderr


def test_wrong_host_certificate_is_refused(tls_workspace):
    root, env = tls_workspace
    directory = Path(env['TLS_CERT_DIR'])
    directory.mkdir()
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                    '-days', '1', '-keyout', str(directory / 'netlab.key'),
                    '-out', str(directory / 'netlab.crt'), '-subj', '/CN=wrong.example',
                    '-addext', 'subjectAltName=DNS:wrong.example'],
                   check=True, capture_output=True)
    result = tls_run(root, env)
    assert result.returncode != 0 and 'does not cover localhost' in result.stderr


def binding_checker():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'binding_checker', ROOT / 'scripts/https-check.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify_bindings


def valid_containers():
    return [{'Name': '/nginx-proxy', 'HostConfig': {'PortBindings': {
        f'{internal}/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(external)}]
        for internal, external in [(8080, 8080), (8081, 8081), (8082, 8082),
                                   (8083, 3000), (8084, 9090)]}}},
        {'Name': '/prometheus', 'HostConfig': {'PortBindings': {}}},
        {'Name': '/grafana', 'HostConfig': {'PortBindings': {}}}]


def test_live_checker_accepts_tls_gateway_bindings():
    binding_checker()(valid_containers())


@pytest.mark.parametrize('service', [1, 2])
def test_live_checker_rejects_leftover_direct_monitoring_publish(service):
    containers = valid_containers()
    containers[service]['HostConfig']['PortBindings']['9090/tcp'] = [
        {'HostIp': '127.0.0.1', 'HostPort': '9090'}]
    with pytest.raises(RuntimeError, match='Direct monitoring binding still exists'):
        binding_checker()(containers)


def test_live_checker_rejects_unexpected_or_non_loopback_gateway_port():
    containers = valid_containers()
    containers[0]['HostConfig']['PortBindings']['8080/tcp'][0]['HostIp'] = '0.0.0.0'
    with pytest.raises(RuntimeError, match='Gateway binding mismatch'):
        binding_checker()(containers)
