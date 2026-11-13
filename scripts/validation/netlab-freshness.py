"""Measure source age and Prometheus-owned scrape age at one evaluation time."""
import argparse
import csv
import json
import math
import os
import ssl
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--samples', type=int, default=30)
parser.add_argument('--interval', type=float, default=2)
parser.add_argument('--project', default='netlab')
parser.add_argument('--service', default='nginx-web')
parser.add_argument('--instance', default='cadvisor:8080')
parser.add_argument('--prometheus', default='https://localhost:9090')
parser.add_argument('--ca-file', default=os.environ.get(
    'NETLAB_CA_FILE', str(Path.home() / 'NetLab-certs/netlab.crt')))
args = parser.parse_args()
if args.samples < 1 or args.interval <= 0:
    parser.error('samples must be positive; interval must be greater than zero')

if not args.prometheus.startswith('https://'):
    parser.error('--prometheus must use HTTPS')
context = ssl.create_default_context(cafile=args.ca_file)

selector = (
    'container_last_seen{job="cadvisor",'
    f'instance={json.dumps(args.instance)},'
    f'container_label_com_docker_compose_project={json.dumps(args.project)},'
    f'container_label_com_docker_compose_service={json.dumps(args.service)}}}'
)
up = f'up{{job="cadvisor",instance={json.dumps(args.instance)}}}'


def query(expression, evaluation):
    params = urlencode({'query': expression, 'time': f'{evaluation:.6f}'})
    with urlopen(args.prometheus.rstrip('/') + '/api/v1/query?' + params, timeout=10, context=context) as r:
        data = json.load(r)
    if data.get('status') != 'success':
        raise RuntimeError('Prometheus query failed')
    results = data['data']['result']
    if len(results) != 1:
        raise RuntimeError(f'Expected one series, got {len(results)}: {expression}')
    value = float(results[0]['value'][1])
    if not math.isfinite(value):
        raise RuntimeError('Non-finite measurement')
    return value


root = Path(__file__).resolve().parents[2] / 'security-reports/performance'
root.mkdir(parents=True, exist_ok=True)
stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
path = root / f'monitoring-freshness-{stamp}.csv'
with path.open('w', newline='') as output:
    writer = csv.writer(output)
    writer.writerow(['evaluation_time', 'scrape_up', 'scrape_age_s', 'source_age_s'])
    for index in range(args.samples):
        evaluation = time.time()
        healthy = query(up, evaluation)
        scrape_age = query(f'time() - timestamp({up})', evaluation)
        source_age = query(f'time() - {selector}', evaluation)
        writer.writerow([evaluation, healthy, scrape_age, source_age])
        output.flush()
        if healthy != 1 or scrape_age < 0 or source_age < 0:
            raise RuntimeError(f'Unhealthy scrape or invalid age; inspect {path}')
        print(f'Sample {index + 1}/{args.samples}: scrape={scrape_age:.3f}s, '
              f'source={source_age:.3f}s')
        if index + 1 < args.samples:
            time.sleep(args.interval)
print('Completed:', path)
