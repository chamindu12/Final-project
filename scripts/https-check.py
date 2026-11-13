#!/usr/bin/env python3
"""Verify TLS identities, monitoring endpoints and the actual published bindings."""
import argparse
import json
import ssl
import subprocess
from urllib.request import urlopen


def verify_bindings(data):
    expected = {'8080/tcp': '8080', '8081/tcp': '8081', '8082/tcp': '8082',
                '8083/tcp': '3000', '8084/tcp': '9090'}
    for container in data:
        bindings = container['HostConfig'].get('PortBindings') or {}
        if container['Name'] == '/nginx-proxy':
            if set(bindings) != set(expected):
                raise RuntimeError('Gateway has unexpected published listeners')
            for key, value in bindings.items():
                if value != [{'HostIp': '127.0.0.1', 'HostPort': expected[key]}]:
                    raise RuntimeError('Gateway binding mismatch: ' + key)
        elif any(bindings.values()):
            raise RuntimeError('Direct monitoring binding still exists: ' + container['Name'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ca-file', required=True)
    args = parser.parse_args()
    context = ssl.create_default_context(cafile=args.ca_file)
    for port, path in [(8080, '/healthz'), (8081, '/api/health'),
                       (8082, '/api/health'), (3000, '/api/health'), (9090, '/-/healthy')]:
        with urlopen(f'https://localhost:{port}{path}', context=context, timeout=15) as response:
            if response.status != 200:
                raise RuntimeError(f'HTTPS endpoint {port} returned {response.status}')
        print(f'PASS verified HTTPS endpoint localhost:{port}')
    data = json.loads(subprocess.check_output(
        ['docker', 'inspect', 'nginx-proxy', 'prometheus', 'grafana'], text=True))
    verify_bindings(data)
    print('PASS only the intended TLS gateway bindings are published')


if __name__ == '__main__':
    main()
