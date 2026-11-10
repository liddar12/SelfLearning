"""Bounded sequential PGlite proof on a complete SYNTHETIC future fixture.

Example (use an already installed, reviewed PGlite module; never downloads):
  SELFLEARN_PGLITE_MODULE=/absolute/path/to/pglite/dist/index.js \
    python tests/postgres/publication_sql_smoke.py --rungs 1000

The subprocess has a 55-second wall limit. This is not native locking,
PostgREST, a historical replay, or deployed publication evidence.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from test_native_publication import publication_fixture, build_publication_attestation
import test_native_concurrency as native


ROOT = Path(__file__).resolve().parents[2]


def module_version(configured):
    # Match the existing harness's explicit local path convention. Package
    # specifiers still execute, but must be described honestly as unlocated.
    from urllib.parse import unquote, urlsplit
    if configured.startswith('file:'):
        candidate = Path(unquote(urlsplit(configured).path))
    else:
        candidate = Path(configured)
    if candidate.is_file():
        for directory in candidate.parents:
            manifest = directory / 'package.json'
            if manifest.is_file():
                value = json.loads(manifest.read_text())
                if value.get('name') == '@electric-sql/pglite':
                    return value['version']
    return 'module manifest not located'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rungs', type=int, default=1, choices=range(1, 1001), metavar='1..1000')
    args = parser.parse_args()
    configured = os.environ.get('SELFLEARN_PGLITE_MODULE')
    if not configured:
        parser.error('SELFLEARN_PGLITE_MODULE must name an already installed module; no SQL was run')
    journal, doc, observation = publication_fixture('synthetic-publication-scale', datetime.now(timezone.utc), count=args.rungs)
    wire = {'object_id': doc['envelope_id'],
        'objects': [{'object_id': oid, 'kind': obj['kind'], 'payload_json': native.canonical(obj['payload'])}
                    for oid, obj in journal.objects.items()],
        'receipts_json': native.canonical(journal.rows), 'observation_json': native.canonical(observation)}
    try:
        completed = subprocess.run(['node', 'tests/postgres/publication_sql_smoke.mjs'],
            input=json.dumps(wire), capture_output=True, text=True, cwd=ROOT, timeout=55, check=False)
    except subprocess.TimeoutExpired as error:
        progress = error.stderr or b''
        sys.stderr.write(progress.decode() if isinstance(progress, bytes) else progress)
        raise SystemExit('Sequential SQL exceeded the 55-second bound; no performance success claimed') from None
    if completed.returncode:
        sys.stderr.write(completed.stderr)
        raise SystemExit(completed.returncode)
    executed = json.loads(completed.stdout)
    record = executed['result']['attestation']
    expected = build_publication_attestation(journal, doc['envelope_id'], observation, record['recorded_utc'])
    if record != expected:
        raise AssertionError('Executed SQL result differs from the independent Python publication join')
    assert record['status'] == 'accepted' and len(record['receipts']) == args.rungs
    print(json.dumps({'evidence': 'sequential SQL only; synthetic future fixture',
        'pglite_version': module_version(configured), 'postgres_version': executed['version'],
        'rungs': args.rungs, 'body_bytes': record['observation']['body_bytes'],
        'result_equals_pure_join': True, 'accepted': len(record['receipts']),
        'canonical_float_metadata': ['-0.0', '1.0', '1e20', '1e-20'],
        'checks': executed['checks'], 'timings': executed['timings'],
        'native_concurrency': 'not executed by this harness'}, indent=2))


if __name__ == '__main__':
    main()
