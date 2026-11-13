import csv
import io
import json
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/validation/netlab-freshness.py'


class FreshnessTests(unittest.TestCase):
    def run_case(self, up=1, count=1, age=4, samples=1):
        def fetch(url, timeout, context):
            q = parse_qs(urlsplit(url).query)['query'][0]
            value = up if q.startswith('up{') else (age if 'timestamp(up{' in q else 8)
            return io.StringIO(json.dumps({'status': 'success', 'data': {'result': [
                {'value': [100, str(value)]} for _ in range(count)]}}))
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d)/'scripts/validation/netlab-freshness.py'
            fake.parent.mkdir(parents=True)
            fake.write_text(SCRIPT.read_text())
            with (
                patch.object(sys, 'argv', [str(fake), '--samples', str(samples)]),
                patch('urllib.request.urlopen', fetch),
                patch('ssl.create_default_context'),
                patch('builtins.print'),
            ):
                runpy.run_path(str(fake), run_name='__main__')
            with next((Path(d)/'security-reports/performance').glob('*.csv')).open() as stream:
                rows = list(csv.DictReader(stream))
            return rows

    def test_ages_use_query_values_not_evaluation_timestamps(self):
        rows = self.run_case()
        self.assertEqual(float(rows[0]['scrape_age_s']), 4)
        self.assertEqual(float(rows[0]['source_age_s']), 8)

    def test_scrape_down(self):
        with self.assertRaises(RuntimeError):
            self.run_case(up=0)

    def test_no_series(self):
        with self.assertRaises(RuntimeError):
            self.run_case(count=0)

    def test_ambiguous_series(self):
        with self.assertRaises(RuntimeError):
            self.run_case(count=2)

    def test_nan(self):
        with self.assertRaises(RuntimeError):
            self.run_case(age=float('nan'))

    def test_invalid_samples(self):
        with self.assertRaises(SystemExit):
            self.run_case(samples=0)


if __name__ == '__main__':
    unittest.main()
