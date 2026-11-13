#!/usr/bin/env python3
"""Prepare the local lab certificate and persist only TLS settings in .env."""
import argparse
import datetime
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
OBSOLETE = {
    'PROXY_PORT', 'STAFF_PORT', 'GUEST_PORT', 'HTTPS_ADMIN_PORT',
    'HTTPS_STAFF_PORT', 'HTTPS_GUEST_PORT', 'SESSION_COOKIE_SECURE',
}


def run(*args, input=None):
    return subprocess.run(args, input=input, capture_output=True, check=True).stdout


def config(text):
    return dict(line.strip().split('=', 1) for line in text.splitlines()
                if line.strip() and not line.lstrip().startswith('#') and '=' in line)


def atomic_write(path, content):
    fd, temp = tempfile.mkstemp(prefix='.netlab-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='validate without writing')
    options = parser.parse_args()
    env_path = ROOT / '.env'
    if not env_path.is_file():
        raise RuntimeError('Create .env from .env.example and configure accounts first')
    text = env_path.read_text()
    values = config(text)
    directory = Path(os.environ.get('TLS_CERT_DIR', values.get(
        'TLS_CERT_DIR', str(Path.home() / 'NetLab-certs'))).strip("\"'")).expanduser().resolve()
    if any(c in str(directory) for c in "'\n\r"):
        raise RuntimeError('TLS certificate path cannot contain quotes or line breaks')
    gid = int(os.environ.get('TLS_CERT_GID', values.get('TLS_CERT_GID', str(os.getgid()))))
    if gid not in [os.getgid(), *os.getgroups()]:
        raise RuntimeError('TLS_CERT_GID must be one of your Unix groups')
    cert, key = directory / 'netlab.crt', directory / 'netlab.key'
    if cert.exists() != key.exists():
        raise RuntimeError('Only one TLS file exists; restore the matching certificate/key pair')
    if not cert.exists():
        if options.check:
            raise RuntimeError('TLS files missing; run python3 scripts/tls-setup.py')
        directory.mkdir(parents=True, mode=0o750, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.tls-', dir=directory) as temp:
            temporary = Path(temp)
            run('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-sha256', '-nodes',
                '-days', '365', '-keyout', str(temporary / 'netlab.key'),
                '-out', str(temporary / 'netlab.crt'), '-subj', '/CN=localhost',
                '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1')
            os.chmod(temporary / 'netlab.key', 0o640)
            os.replace(temporary / 'netlab.key', key)
            os.replace(temporary / 'netlab.crt', cert)
        print('Created a self-signed localhost lab certificate')
    # Fail rather than silently replace an expired, wrong-host or mismatched pair.
    run('openssl', 'x509', '-in', str(cert), '-noout', '-checkend', '3600')
    for option, identity in [('-checkhost', 'localhost'), ('-checkip', '127.0.0.1')]:
        try:
            result = run('openssl', 'x509', '-in', str(cert), '-noout', option, identity)
        except subprocess.CalledProcessError as error:
            raise RuntimeError('Certificate SAN does not cover ' + identity) from error
        if b'does match certificate' not in result:
            raise RuntimeError('Certificate SAN does not cover ' + identity)
    public = run('openssl', 'x509', '-in', str(cert), '-pubkey', '-noout')
    if public != run('openssl', 'pkey', '-in', str(key), '-pubout'):
        raise RuntimeError('TLS certificate and private key do not match')
    if not options.check:
        for path, mode in [(directory, 0o750), (key, 0o640), (cert, 0o644)]:
            os.chown(path, -1, gid)
            os.chmod(path, mode)
        lines = []
        for line in text.splitlines():
            name = line.split('=', 1)[0].strip()
            if name not in OBSOLETE | {'TLS_CERT_DIR', 'TLS_CERT_GID'}:
                lines.append(line)
        lines += [f"TLS_CERT_DIR='{directory}'", f'TLS_CERT_GID={gid}']
        updated = '\n'.join(lines) + '\n'
        if updated != text:
            backup = Path.home() / 'NetLab-backups' / (
                'tls-env-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
            backup.mkdir(parents=True, mode=0o700)
            atomic_write(backup / '.env', text)
            atomic_write(env_path, updated)
            print('Saved private environment backup:', backup)
    if (key.stat().st_gid != gid or key.stat().st_mode & 0o007
            or not key.stat().st_mode & 0o040):
        raise RuntimeError('Private key must be group-readable and inaccessible to others')
    print('PASS: certificate, localhost identity and key match; HTTPS uses ports 8080/8081/8082')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(f'TLS setup failed: {error}') from error
