"""Read-only capacity oracles; optional real NFL producer fixture is explicit."""
import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from selflearn_core.adapters import nfl_capture_application as P
from selflearn_core.adapters.nfl import adapt_prospective
from selflearn_core.probability_receipts import fingerprint
from test_nfl_capture_delivery_job import MemoryDeliveryJournal, PROCESSING, request, run as deliver
from test_nfl_capture_labels import journal_fixture, good_capture
import test_nfl_capture_evidence as E


def population(*, sizes=(4, 4), missing_first=False):
    journal = MemoryDeliveryJournal(vintages=0)
    stats = E.STATS if missing_first else E.STATS.replace(b',,,NA', b',,,80')
    rows = 3
    for index, size in enumerate(sizes):
        name = 'Fixture Receiver' if index == 0 else f'Other Receiver {index}'
        item = journal_fixture(vintages=1, lines=tuple(float(n + 1) for n in range(size)),
                               player=name, player_id=f'espn-player-{index}')
        journal.snapshot['receipts'] += item.snapshot['receipts']
        journal.snapshot['generation_records'] += item.snapshot['generation_records']
        journal.objects.update(item.objects)
        if index:
            stats += f'2026,6,REG,WR,SEA,{name},,,80\r\n'.encode()
            rows += 1
    return journal, E.fixture(stats=stats, stats_audit=E.csv_audit(rows))


def plan(journal, envelope, objects, **kwargs):
    return P.plan_capture_application(journal, request(envelope, objects), lambda key: E.stream(objects[key]),
                                      clock=lambda: PROCESSING, **kwargs)


def validate_manifest(test, preview):
    manifest = preview['manifest']
    test.assertFalse(preview['durable_ack'])
    test.assertFalse(preview['execution_ready'])
    test.assertEqual(preview['scope'], 'offline_planning_only')
    test.assertEqual(preview['source_authentication'], 'not_performed')
    test.assertEqual(manifest['plan_id'], fingerprint({k: v for k, v in manifest.items() if k != 'plan_id'}))
    batch_groups = [gid for batch in manifest['batches'] for gid in batch['group_ids']]
    held = manifest['held_group_ids']
    test.assertEqual(len(batch_groups), len(set(batch_groups)))
    test.assertEqual(set(batch_groups) | set(held), {g['group_id'] for g in manifest['groups']})
    test.assertFalse(set(batch_groups) & set(held))
    test.assertEqual(manifest['counts']['planned_members'] + manifest['counts']['withheld_members'], manifest['counts']['members'])
    for batch, candidate in zip(manifest['batches'], preview['candidates']):
        test.assertEqual(batch['batch_id'], candidate['batch_id'])
        test.assertEqual(batch['payload_id'], fingerprint(candidate['payload']))
        test.assertEqual(candidate['capacity_model'], P.CAPACITY_MODEL)
        test.assertLessEqual(batch['member_count'], manifest['limits']['members'])
        test.assertLessEqual(batch['observation_count'], manifest['limits']['observations'])
        test.assertLessEqual(batch['revision_count'], manifest['limits']['revisions'])
        test.assertLessEqual(batch['rpc_bytes'], manifest['limits']['rpc_bytes'])


class ApplicationTests(unittest.TestCase):
    def test_full_bind_once_deterministic_complete_groups_no_mutations(self):
        journal, (envelope, objects) = population(sizes=(4, 4, 2))
        original = copy.deepcopy(journal.snapshot)
        reads = []
        def raw(key): reads.append(key); return E.stream(objects[key])
        preview = P.plan_capture_application(journal, request(envelope, objects), raw, clock=lambda: PROCESSING,
                                              max_batch_members=6)
        validate_manifest(self, preview)
        self.assertEqual(preview['status'], 'planned')
        self.assertEqual(preview['manifest']['counts']['members'], 10)
        self.assertEqual(len(reads), 5, 'integrity and full-stat rereads occur once, never once per batch')
        self.assertEqual(journal.reads, 1)
        self.assertEqual(journal.snapshot, original)
        self.assertEqual(journal.delivery_writes, [])
        journal.snapshot['receipts'].reverse()
        journal.snapshot['generation_records'].reverse()
        repeated = plan(journal, envelope, objects, max_batch_members=6)
        self.assertEqual(repeated, preview)

    def test_exact_count_boundary_500_and_501_group_never_fragmented(self):
        journal, (envelope, objects) = population(sizes=(500, 1))
        preview = plan(journal, envelope, objects)
        validate_manifest(self, preview)
        self.assertEqual(sorted(b['member_count'] for b in preview['manifest']['batches']), [1, 500])
        journal, (envelope, objects) = population(sizes=(501, 2))
        preview = plan(journal, envelope, objects)
        validate_manifest(self, preview)
        self.assertEqual(preview['status'], 'partial')
        self.assertEqual(preview['manifest']['counts']['withheld_members'], 501)
        self.assertEqual(preview['manifest']['counts']['planned_members'], 2)
        held = next(g for g in preview['manifest']['groups'] if g['status'] == 'withheld')
        self.assertEqual(len(held['receipt_ids']), 501)
        self.assertIn('complete_group_exceeds_member_limit', held['reason_codes'])

    def test_actual_escaped_wire_limits_are_inclusive_and_drive_packing(self):
        journal, (envelope, objects) = population(sizes=(2, 2))
        first = plan(journal, envelope, objects)
        payload = first['candidates'][0]['payload']
        groups = first['manifest']['groups']
        singles = []
        for group in groups:
            obs = [r for r in payload['observations'] if r['receipt_id'] in group['receipt_ids']]
            # _wire wraps proposals; remove only its explicit version envelope.
            rev = [{k: v for k, v in r.items() if k != 'version'} for r in payload['revisions'] if r['resolution']['receipt_id'] in group['receipt_ids']]
            _, size = P._wire([group], obs, rev, P.MAX_BATCH_BYTES)
            self.assertEqual(P._wire([group], obs, rev, size)[1], size)
            with self.assertRaises(P.CapturePlanError): P._wire([group], obs, rev, size - 1)
            singles.append(size)
        limited = plan(journal, envelope, objects, max_batch_bytes=max(singles))
        validate_manifest(self, limited)
        self.assertEqual(len(limited['manifest']['batches']), 2)
        held = plan(journal, envelope, objects, max_batch_bytes=min(singles) - 1)
        self.assertEqual(held['status'], 'withheld')
        self.assertEqual(held['manifest']['counts']['withheld_members'], 4)
        self.assertEqual(held['candidates'], [])
        for maximum in (P.MAX_MANIFEST_BYTES, P.MAX_BATCH_BYTES):
            body = {'x': 'a' * (maximum - 8)}
            self.assertEqual(len(P._encode(body, maximum)), maximum)
            with self.assertRaises(P.CapturePlanError): P._encode({'x': body['x'] + 'a'}, maximum)
        encoded = P._encode({'x': '"\\🚀'}, 1000)
        self.assertGreater(len(P._encode({'p_batch_json': encoded}, 1000, outer=True)), len(encoded))

    def test_proof_limits_and_kernel_gap_hold_all_rungs_preserve_good_group(self):
        journal, (envelope, objects) = population(sizes=(4, 2))
        for key in ('max_batch_observations', 'max_batch_revisions'):
            preview = plan(journal, envelope, objects, **{key: 2})
            validate_manifest(self, preview)
            self.assertEqual(preview['manifest']['counts']['withheld_members'], 4)
            self.assertEqual(preview['manifest']['counts']['planned_members'], 2)
        original = P.resolve_observed
        def missing(*args, **kwargs):
            result = original(*args, **kwargs)
            result['observation_proposals'] = result['observation_proposals'][1:]
            return result
        with patch.object(P, 'resolve_observed', side_effect=missing):
            preview = plan(journal, envelope, objects)
        validate_manifest(self, preview)
        self.assertEqual(preview['status'], 'partial')
        held = next(g for g in preview['manifest']['groups'] if g['status'] == 'withheld')
        self.assertIn('kernel_group_incomplete', held['reason_codes'])
        self.assertEqual(held['observation_ids'], [])

    def test_missing_NA_alias_period_and_named_zero_masks_never_become_false_VOID(self):
        journal = MemoryDeliveryJournal()
        no_stat = E.STATS.splitlines(keepends=True)
        no_stat = no_stat[0] + b''.join(no_stat[2:])
        variants = [E.fixture(),
            E.fixture(stats=no_stat, stats_audit=E.csv_audit(2), snaps=E.SNAPS.replace(b'Receiver,40', b'Receiver,0')),
            E.fixture(stats=no_stat, stats_audit=E.csv_audit(2), snaps=E.SNAPS.replace(b'Fixture Receiver', b'F. Receiver')),
            E.fixture(stats=no_stat, stats_audit=E.csv_audit(2), snaps=E.SNAPS.replace(b'REG,SEA', b',SEA'))]
        for envelope, objects in variants:
            with self.subTest(capture=envelope['manifest']['capture_id']):
                preview = plan(journal, envelope, objects)
                self.assertEqual(preview['status'], 'withheld')
                self.assertEqual(preview['candidates'], [])
        envelope, objects = E.fixture(stats=no_stat, stats_audit=E.csv_audit(2))
        preview = plan(journal, envelope, objects)
        self.assertEqual(preview['status'], 'planned')
        self.assertTrue(all(r['measurement']['actual'] == 0 and r['measurement']['played'] is True
                            for r in preview['candidates'][0]['payload']['observations']))
        journal, (envelope, objects) = population(missing_first=True)
        preview = plan(journal, envelope, objects)
        self.assertEqual(preview['status'], 'partial')
        self.assertEqual(preview['manifest']['counts']['planned_members'], 4)

    def test_original_delivery_returns_before_clock_raw_or_replanning(self):
        journal = MemoryDeliveryJournal(); envelope, objects = good_capture(); intent = request(envelope, objects)
        original = deliver(journal, intent, objects)['result']
        def forbidden(*args): raise AssertionError('original delivery must not be replanned')
        with patch.object(journal, 'read_observed_snapshot', side_effect=forbidden):
            preview = P.plan_capture_application(journal, intent, forbidden, clock=forbidden)
        self.assertEqual(preview['status'], 'original_delivery_exists')
        self.assertEqual(preview['original_result'], original)
        self.assertIsNone(preview['manifest'])
        self.assertEqual(preview['candidates'], [])

    def test_empty_scope_is_explicit_not_integration_success(self):
        journal = MemoryDeliveryJournal(vintages=0); envelope, objects = good_capture()
        preview = plan(journal, envelope, objects)
        self.assertEqual(preview['status'], 'no_supported_membership')
        self.assertEqual(preview['manifest']['counts']['members'], 0)
        self.assertEqual(preview['candidates'], [])
        self.assertEqual(journal.delivery_writes, [])

    def test_tampered_pointer_late_raw_reread_and_unlocatable_authority_abort(self):
        journal = MemoryDeliveryJournal(); envelope, objects = good_capture(); intent = request(envelope, objects)
        bad = copy.deepcopy(intent); bad['raw_pointers'][0]['chunks'][0]['chunk_id'] = fingerprint('bad')
        with self.assertRaises(ValueError): P.plan_capture_application(journal, bad, lambda key: E.stream(objects[key]), clock=lambda: PROCESSING)
        counts = {}
        def unstable(key):
            counts[key] = counts.get(key, 0) + 1
            return E.stream(objects[key] if counts[key] == 1 else b'corrupt')
        with self.assertRaises(ValueError): P.plan_capture_application(journal, intent, unstable, clock=lambda: PROCESSING)
        journal.snapshot['generation_records'].pop()
        with self.assertRaises(ValueError): plan(journal, envelope, objects)
        self.assertEqual(journal.delivery_writes, [])

    def test_limits_population_guards_and_malformed_protocol_fail_closed(self):
        journal, (envelope, objects) = population()
        for key in ('max_batch_members', 'max_batch_observations', 'max_batch_revisions', 'max_batch_bytes'):
            for value in (True, 0, -1, 500.0, (P.MAX_BATCH_BYTES + 1 if key == 'max_batch_bytes' else 501)):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError): plan(journal, envelope, objects, **{key: value})
        for value in (True, -1, 64 * 1024 * 1024 + 1):
            with self.assertRaises(ValueError): plan(journal, envelope, objects, max_total_bytes=value)
        with self.assertRaises(ValueError): P.plan_capture_application(None, {}, None)
        with patch.object(journal, 'read_observed_snapshot', return_value={}):
            with self.assertRaises(ValueError): plan(journal, envelope, objects)
        for name, value in (('MAX_GROUPS', 1), ('MAX_MEMBERS', 3), ('MAX_MANIFEST_BYTES', 100)):
            with patch.object(P, name, value), self.assertRaises(ValueError): plan(journal, envelope, objects)

    def test_global_unknown_duplicate_proofs_or_members_cannot_be_filtered_out(self):
        journal, (envelope, objects) = population()
        original = P.resolve_observed
        for kind in ('extra_observation', 'duplicate_observation', 'extra_revision', 'duplicate_revision'):
            def broken(*args, _kind=kind, **kwargs):
                result = original(*args, **kwargs)
                if _kind.endswith('observation'):
                    row = copy.deepcopy(result['observation_proposals'][0])
                    if _kind.startswith('extra'): row['receipt_id'] = fingerprint('unknown')
                    result['observation_proposals'].append(row)
                else:
                    row = copy.deepcopy(result['proposals'][0])
                    if _kind.startswith('extra'): row['resolution']['receipt_id'] = fingerprint('unknown')
                    result['proposals'].append(row)
                return result
            with patch.object(P, 'resolve_observed', side_effect=broken), self.assertRaises(ValueError): plan(journal, envelope, objects)
        binder = P.bind_capture_labels
        for kind in ('fence', 'duplicate_group', 'member', 'hash'):
            def malformed(*args, _kind=kind, **kwargs):
                result = binder(*args, **kwargs)
                if _kind == 'fence': result['membership_watermark'] = '999'
                if _kind == 'duplicate_group': result['groups'].append(copy.deepcopy(result['groups'][0]))
                if _kind == 'member': result['groups'][1]['receipt_ids'][0] = result['groups'][0]['receipt_ids'][0]
                if _kind == 'hash': result['membership_id'] = fingerprint('wrong')
                return result
            with patch.object(P, 'bind_capture_labels', side_effect=malformed), self.assertRaises(ValueError): plan(journal, envelope, objects)

    @unittest.skipUnless(os.environ.get('NFL2026_CHECKOUT'), 'Actual producer parity requires explicit NFL2026_CHECKOUT')
    def test_actual_producer_1088_rungs_185_groups_max8_capacity_preview(self):
        checkout = Path(os.environ['NFL2026_CHECKOUT'])
        script = r'''
import base64,copy,datetime as dt,importlib.util,json,math,tempfile
from pathlib import Path
spec=importlib.util.spec_from_file_location('fixture',Path('tests/fixtures/week6_outcomes.py'))
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
with tempfile.TemporaryDirectory() as directory:
 root=Path(directory)
 for vintage in range(4):
  created=f'2026-09-01T{12+vintage:02d}:00:00Z'
  pool=f.cards._fixture_pool(created);pool.update(week=6,base_model_version='fixture-base')
  prototype=copy.deepcopy(pool['players'][0]);players=[]
  group_ids=range(185) if vintage<2 else range(173) if vintage==2 else range(1)
  for index in group_ids:
   player=copy.deepcopy(prototype);player.update(player=f'Receiver {index}',team='SEA',gsis_id=f'espn-{index}')
   for rung in player['rungs']:
    z=(player['mu']-rung['line'])/30;coef={'a':math.log(rung['model_prob']/(1-rung['model_prob'])),'b':0,'c':0}
    features={'market':player['market'],'line':rung['line'],'mu':player['mu'],'sd':30,'z':z,'p_team':player['p_team'],
      'calibration':coef,'correlation_tags':['game:G1']}
    context={'season':2026,'week':6,'base_model_version':'fixture-base','forecast_created_utc':created,
      'source_snapshot_id':f.lr.fingerprint({'created':created}),'source_timestamps':{'fixture':created},'generation_sha':'local-expanded-fixture'}
    source=f.lr.make_receipt({**player,**rung},f.lr.base_probability(coef,z,player['p_team']),None,context,features=features)
    rung.update(model_prob=source['served_prob'],learning_receipt=source)
   players.append(player)
  pool['players']=players
  schedule={'season':2026,'games':[{'game_id':'G1','week':6,'home':'SEA','away':'LAR','status':'STATUS_SCHEDULED','kickoff_utc':f.KICKOFF}]}
  recorded=(f.lr.instant(created)+dt.timedelta(minutes=1)).isoformat().replace('+00:00','Z')
  f.learning_journal.record(pool,schedule,None,[],recorded,str(root))
 bundle=f.bundle(stats_rows=[f.stats_row('80',player_display_name=f'Receiver {i}') for i in range(185)],
   snap_rows=[f.snap_row(player=f'Receiver {i}') for i in range(185)])
 print(json.dumps({'plan':f.plan(root),'capture':{'manifest':bundle['manifest'],'observations':bundle['observations'],
   'raw':{key:base64.b64encode(raw).decode() for key,raw in bundle['raw_objects'].items()}}}))
'''
        produced = subprocess.run([sys.executable, '-c', script], cwd=checkout, capture_output=True, text=True, timeout=60)
        self.assertEqual(produced.returncode, 0, 'Actual NFL producer fixture must complete locally')
        value = json.loads(produced.stdout)
        documents = [row['request']['payload'] for row in value['plan']['generations']]
        adapted = adapt_prospective(journal_docs=documents)
        self.assertEqual(adapted['rejections'], [])
        journal = MemoryDeliveryJournal(vintages=0)
        journal.snapshot['receipts'] = adapted['receipts']
        by_source = {r['source_receipt_id']: r for r in adapted['receipts']}
        for obj in value['plan']['source_objects']:
            journal.objects[obj['object_id']] = {**obj, 'received_utc': '2026-10-15T00:00:00Z'}
        for doc in documents:
            ack = {'version': 1, 'object_id': doc['envelope_id'], 'durable': True, 'rejections': [], 'receipts': [
                {'source_receipt_id': r['receipt_id'], 'receipt_id': by_source[r['receipt_id']]['receipt_id'], 'status': 'recorded'} for r in doc['receipts']]}
            journal.snapshot['generation_records'].append({'object_id': doc['envelope_id'], 'payload': doc, 'ack': ack, 'recorded_utc': '2026-10-15T00:01:00Z'})
        captured = value['capture']
        objects = {key: base64.b64decode(raw, validate=True) for key, raw in captured['raw'].items()}
        envelope = {'version': 1, 'kind': 'nfl.capture_envelope.v1', 'scope': 'uploaded_only', 'manifest': captured['manifest'],
            'observations': captured['observations'], 'raw_objects': [{'raw_sha256': key, 'raw_bytes': len(raw)} for key, raw in sorted(objects.items())]}
        preview = plan(journal, envelope, objects)
        validate_manifest(self, preview)
        manifest = preview['manifest']
        self.assertEqual((manifest['counts']['members'], manifest['counts']['groups']), (1088, 185))
        self.assertEqual(max(len(g['receipt_ids']) for g in manifest['groups']), 8)
        self.assertEqual(manifest['counts']['planned_members'], 1088)
        self.assertEqual(manifest['counts']['withheld_members'], 0)
        self.assertEqual(len(manifest['batches']), 3)
        self.assertEqual(sum(b['observation_count'] for b in manifest['batches']), 1088)
        self.assertEqual(journal.delivery_writes, [])


if __name__ == '__main__': unittest.main()
