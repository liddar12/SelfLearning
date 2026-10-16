"""Explicit ASGI capture boundary; memory fixtures are NOT durable SQL proof."""
import copy
import json
from contextlib import contextmanager
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

from services.web import nfl_capture_intake as intake
from services.web.application import create_app
from services.web.security import MAX_LEARNING_JSON
from selflearn_core.evidence_chunks import make_chunk
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.capture_staging import staging_namespace
from selflearn_core.store.supabase_evidence import SupabaseEvidenceStore
from selflearn_core.store.supabase_store import StoreError
from selflearn_core.store.receipt_journal import JournalConflict
from .fakes import FakeStore, TEST_KEY

CORE_TESTS = Path(__file__).resolve().parents[2] / 'packages/selflearn-core/tests'
sys.path.insert(0, str(CORE_TESTS))
try:
    from test_nfl_capture_delivery_job import MemoryDeliveryJournal, PROCESSING, request as fixture_request
    from test_nfl_capture_application import population
    from test_nfl_capture_labels import good_capture, journal_fixture
    from test_supabase_evidence import FakeStorage
finally:
    sys.path.pop(0)

CAPTURE_KEY = 'fixture-only-nfl-outcome-capture-key-' + 'c' * 32
GENERATION_KEY = 'fixture-only-nfl-generation-key-' + 'g' * 32
ENV = {'SELFLEARN_OPERATOR_TOKEN': TEST_KEY, 'SELFLEARN_NFL_INGEST_TOKEN': GENERATION_KEY,
       'SELFLEARN_NFL_OUTCOME_INGEST_TOKEN': CAPTURE_KEY}
DEST, PRODUCER = fingerprint('test-destination'), 'nfl-test-producer'
NS = staging_namespace(destination_id=DEST, producer_id=PRODUCER)
CHUNKS, DELIVERIES = '/api/v1/nfl/capture-chunks', '/api/v1/nfl/capture-deliveries'


class MemoryQuota:
    """Named fake only: ordering/faults, never durable budget acceptance."""
    def __init__(self, *, maximum=64 * 1024 * 1024, events=None):
        self.rows, self.calls, self.maximum = {}, [], maximum
        self.events = events if events is not None else []
        self.lose_reply = False

    def reserve(self, chunk_id, raw_bytes):
        self.calls.append((chunk_id, raw_bytes)); self.events.append('quota')
        if chunk_id not in self.rows:
            total = sum(row['raw_bytes'] for row in self.rows.values()) + raw_bytes
            if total > self.maximum: raise StoreError(429)
            identity = {'version': 1, 'namespace_id': NS, 'chunk_id': chunk_id, 'raw_bytes': raw_bytes}
            self.rows[chunk_id] = {**identity, 'kind': 'selflearn.capture_stage_reservation.v1',
                'reservation_id': fingerprint(identity), 'reserved_total_bytes': total, 'reserved_chunks': len(self.rows) + 1,
                'recorded_utc': '2026-10-16T00:00:00Z', 'scope': 'staging_budget_only', 'quota_reserved': True,
                'learning_accepted': False, 'durable_ack': False}
        if self.lose_reply: raise TimeoutError('SECRET_QUOTA_REPLY')
        return copy.deepcopy(self.rows[chunk_id])


def setup_receiver(journal=None, *, maximum=64 * 1024 * 1024):
    events, transport = [], FakeStorage()
    transport.callback = lambda method, path: events.append('storage-' + method)
    evidence = SupabaseEvidenceStore(transport, bucket='private-evidence', namespace='nfl-test-scope')
    quota, journal = MemoryQuota(maximum=maximum, events=events), journal or MemoryDeliveryJournal()
    receiver = intake.CaptureReceiver(journal, evidence, quota, destination_id=DEST, producer_id=PRODUCER)
    return receiver, journal, transport, quota, events


def client_for(receiver=None, *, env=None, factory=None):
    if factory is None and receiver is not None:
        @contextmanager
        def factory(): yield receiver
    return TestClient(create_app(store=FakeStore(), env=ENV if env is None else env, capture_factory=factory))


def post(client, path, value, *, token=CAPTURE_KEY):
    return client.post(path, json=value, headers={'Authorization': 'Bearer ' + token})


def upload(client, objects):
    for raw in objects.values():
        response = post(client, CHUNKS, make_chunk(raw))
        assert response.status_code == 200, response.text


@pytest.fixture
def fixed_processing(monkeypatch):
    # Clock only, for future-dated MEMORY fixtures. Actual SQL uses September.
    original = intake.commit_capture_delivery
    monkeypatch.setattr(intake, 'commit_capture_delivery',
        lambda journal, value, reader: original(journal, value, reader, clock=lambda: PROCESSING))


def test_separate_token_explicit_factory_no_auto_enable_and_security_headers():
    receiver, journal, storage, quota, _ = setup_receiver()
    with client_for(receiver) as client:
        for token in (TEST_KEY, GENERATION_KEY, '', 'wrong'):
            response = post(client, CHUNKS, make_chunk(b'bytes'), token=token)
            assert response.status_code == 401 and response.headers['Cache-Control'] == 'no-store'
        assert client.get('/api/status', headers={'Authorization': 'Bearer ' + CAPTURE_KEY}).status_code == 401
        assert post(client, '/api/v1/nfl/objects', {}, token=CAPTURE_KEY).status_code == 401
        response = post(client, CHUNKS, make_chunk(b'bytes'))
        assert response.status_code == 200 and response.headers['Cache-Control'] == 'no-store'
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
    for value in ('', None, TEST_KEY, GENERATION_KEY):
        with client_for(receiver, env={**ENV, 'SELFLEARN_NFL_OUTCOME_INGEST_TOKEN': value}) as client:
            assert post(client, CHUNKS, make_chunk(b'x')).status_code == 503
    with client_for(env={**ENV, 'VERCEL': '1'}) as client:
        response = post(client, CHUNKS, make_chunk(b'x'))
        assert response.status_code == 503 and 'acknowledged' in response.text
    assert journal.delivery_writes == []


def test_quota_reserved_before_storage_only_staging_ack_and_exact_retry():
    receiver, journal, storage, quota, events = setup_receiver()
    dto = make_chunk(b'verified bytes')
    with client_for(receiver) as client:
        first, second = post(client, CHUNKS, dto), post(client, CHUNKS, dto)
    assert first.status_code == second.status_code == 200
    assert events[0] == 'quota' and events.index('quota') < events.index('storage-POST')
    assert len(quota.rows) == len(storage.objects) == 1
    assert first.json()['learning_accepted'] is False and first.json()['durable_ack'] is False
    assert first.json()['scope'] == 'uploaded_only' and journal.delivery_writes == []


def test_exhausted_or_malformed_quota_never_touches_storage():
    receiver, journal, storage, quota, _ = setup_receiver(maximum=2)
    with client_for(receiver) as client:
        response = post(client, CHUNKS, make_chunk(b'three'))
        assert response.status_code == 429 and 'SECRET' not in response.text
    assert storage.calls == []
    for malformed in ({'version': 1}, None, {'durable_ack': True}):
        receiver, _, storage, quota, _ = setup_receiver()
        quota.reserve = lambda *args: malformed
        with client_for(receiver) as client: assert post(client, CHUNKS, make_chunk(b'bytes')).status_code == 503
        assert storage.calls == []


def test_lost_quota_and_lost_storage_reply_never_false_ack_and_keep_charge():
    receiver, _, storage, quota, _ = setup_receiver(); dto = make_chunk(b'charged before upload')
    quota.lose_reply = True
    with client_for(receiver) as client:
        response = post(client, CHUNKS, dto)
        assert response.status_code == 503 and 'SECRET' not in response.text
        assert len(quota.rows) == 1 and storage.calls == []
        quota.lose_reply = False; storage.post_error = TimeoutError('SECRET_STORAGE_POST')
        response = post(client, CHUNKS, dto)
        assert response.status_code == 503 and 'SECRET' not in response.text
        assert len(quota.rows) == len(storage.objects) == 1
        storage.post_error = None
        assert post(client, CHUNKS, dto).status_code == 200
    assert len(quota.rows) == 1


def test_streamed_3MiB_content_type_duplicates_nonfinite_and_origin_reject_before_storage():
    receiver, _, storage, quota, _ = setup_receiver()
    headers = {'Authorization': 'Bearer ' + CAPTURE_KEY, 'Content-Type': 'application/json'}
    with client_for(receiver) as client:
        assert client.post(CHUNKS, content=b'{}', headers={'Authorization': headers['Authorization']}).status_code == 415
        for raw in (b'{broken', b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e400}', b'[' * 30 + b'1' + b']' * 30):
            assert client.post(CHUNKS, content=raw, headers=headers).status_code == 422
        assert client.post(CHUNKS, content=b' ' * (MAX_LEARNING_JSON + 1), headers=headers).status_code == 413
        def chunks():
            yield b'x' * (MAX_LEARNING_JSON // 2)
            yield b'x' * (MAX_LEARNING_JSON // 2 + 1)
        assert client.post(CHUNKS, content=chunks(), headers=headers).status_code == 413
        response = client.post(CHUNKS, json=make_chunk(b'good'), headers={**headers, 'Origin': 'https://evil.invalid'})
        assert response.status_code == 403 and response.headers['Cache-Control'] == 'no-store'
        assert storage.calls == [] and quota.calls == []
        assert post(client, CHUNKS, make_chunk(b'x' * 300000)).status_code == 200
        response = client.post(CHUNKS, content=json.dumps(make_chunk(b'case insensitive type')),
            headers={'Authorization': headers['Authorization'], 'Content-Type': 'Application/JSON; charset=UTF-8'})
        assert response.status_code == 200


def test_frozen_delivery_scope_rejects_before_bind_and_known_other_namespace_is404(fixed_processing):
    receiver, journal, storage, quota, _ = setup_receiver()
    envelope, objects = good_capture(); intent = fixture_request(envelope, objects)
    with client_for(receiver) as client:
        from selflearn_core.adapters.nfl_capture_delivery_journal import make_capture_delivery_request
        for field, value in (('producer_id', 'other-producer'), ('destination_id', fingerprint('other-destination'))):
            kwargs = {'destination_id': DEST, 'producer_id': PRODUCER, field: value}
            other = make_capture_delivery_request(envelope, intent['raw_pointers'], **kwargs)
            assert post(client, DELIVERIES, other).status_code == 422
        assert journal.reads == 0 and journal.result_reads == [] and storage.calls == []
        upload(client, objects)
        first = post(client, DELIVERIES, intent)
        assert first.status_code == 200, first.text
        original = first.json()['result']
        response = client.get(DELIVERIES + '/' + intent['delivery_id'], headers={'Authorization': 'Bearer ' + CAPTURE_KEY})
        assert response.status_code == 200 and response.json() == original
        wrong_scope = intake.CaptureReceiver(journal, receiver.evidence, quota, destination_id=DEST, producer_id='other-producer')
        with client_for(wrong_scope) as other_client:
            response = other_client.get(DELIVERIES + '/' + intent['delivery_id'], headers={'Authorization': 'Bearer ' + CAPTURE_KEY})
            assert response.status_code == 404 and 'groups' not in response.text
        assert client.get(DELIVERIES + '/bad', headers={'Authorization': 'Bearer ' + CAPTURE_KEY}).status_code == 422


def test_lost_delivery_reply_recovers_original_before_storage_reads_after_new_members(fixed_processing):
    receiver, journal, storage, _, _ = setup_receiver()
    envelope, objects = good_capture(); intent = fixture_request(envelope, objects)
    with client_for(receiver) as client:
        upload(client, objects); journal.lose_delivery_response = True
        assert post(client, DELIVERIES, intent).status_code == 503
        original = copy.deepcopy(journal.results[intent['delivery_id']])
        newer = journal_fixture(vintages=3, watermark='200')
        journal.snapshot, journal.objects = newer.snapshot, newer.objects
        before = len(storage.calls); response = post(client, DELIVERIES, intent)
        assert response.status_code == 200 and response.json()['status'] == 'recovered_original'
        assert response.json()['result'] == original and len(storage.calls) == before


def test_oversize_full_population_and_corrupt_private_chunk_never_ack_outcomes(fixed_processing):
    large, (envelope, objects) = population(sizes=(501,))
    receiver, _, storage, _, _ = setup_receiver(large)
    with client_for(receiver) as client:
        upload(client, objects); response = post(client, DELIVERIES, fixture_request(envelope, objects))
        assert response.status_code == 422 and large.delivery_writes == []
    receiver, journal, storage, _, _ = setup_receiver(); envelope, objects = good_capture()
    with client_for(receiver) as client:
        upload(client, objects); storage.objects[next(iter(storage.objects))] = b'corrupt'
        assert post(client, DELIVERIES, fixture_request(envelope, objects)).status_code == 503
        assert journal.delivery_writes == []


def test_malformed_factory_storage_ack_and_constructor_guards():
    @contextmanager
    def invalid_factory(): yield object()
    with client_for(factory=invalid_factory) as client: assert post(client, CHUNKS, make_chunk(b'bytes')).status_code == 503
    receiver, _, storage, _, _ = setup_receiver()
    receiver.evidence.put_chunk = lambda value: {'version': 1, 'durable_ack': True}
    with client_for(receiver) as client: assert post(client, CHUNKS, make_chunk(b'bytes')).status_code == 503
    dto = make_chunk(b'bytes')
    receiver.evidence.put_chunk = lambda value: {'version': 1, 'kind': 'selflearn.evidence_stage.v1',
        'scope': 'uploaded_only', 'bucket': 'private-evidence', 'key': 'nfl-test-scope/fixture',
        'chunk_id': dto['chunk_id'], 'raw_bytes': dto['raw_bytes'], 'learning_accepted': False, 'durable_ack': False,
        'SECRET_BACKEND': 'must not reach response'}
    with client_for(receiver) as client:
        response = post(client, CHUNKS, dto)
        assert response.status_code == 503 and 'SECRET' not in response.text
    with pytest.raises(ValueError): intake.CaptureReceiver(None, None, None, destination_id=DEST, producer_id=PRODUCER)


def test_unknown_result_malformed_backend_and_conflict_are_bounded_sanitized(monkeypatch):
    receiver, journal, _, _, _ = setup_receiver()
    with client_for(receiver) as client:
        response = client.get(DELIVERIES + '/' + fingerprint('missing'), headers={'Authorization': 'Bearer ' + CAPTURE_KEY})
        assert response.status_code == 404
        journal.get_capture_delivery = lambda _: {'durable': True, 'SECRET_BACKEND': 'never echo'}
        response = client.get(DELIVERIES + '/' + fingerprint('missing'), headers={'Authorization': 'Bearer ' + CAPTURE_KEY})
        assert response.status_code == 503 and 'SECRET' not in response.text
    with pytest.raises(ValueError): receiver.delivery('not a digest')
    receiver, journal, _, _, _ = setup_receiver(); envelope, objects = good_capture()
    intent = fixture_request(envelope, objects)
    monkeypatch.setattr(intake, 'commit_capture_delivery', lambda *a, **k: {'result': {'durable': True}})
    with client_for(receiver) as client:
        response = post(client, DELIVERIES, intent)
        assert response.status_code == 503 and journal.delivery_writes == []
    def conflict(*args): raise JournalConflict('SECRET_IMMUTABLE_CONFLICT')
    monkeypatch.setattr(intake, 'commit_capture_delivery', conflict)
    with client_for(receiver) as client:
        response = post(client, DELIVERIES, intent)
        assert response.status_code == 409 and 'SECRET' not in response.text
