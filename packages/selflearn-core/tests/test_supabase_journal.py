"""Offline tests for the hosted journal contract; no live database writes."""
import copy
import json

import pytest

from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.supabase_journal import (SupabaseReceiptJournal, _canonical,
    _decode, _object_payload, _partition, _revision_envelope)
from selflearn_core.store.receipt_journal import JournalConflict
from selflearn_core.store.supabase_store import StoreConflict, StoreError, SupabaseStore
from test_probability_receipts import receipt, outcome, reseal


ORIGIN = "https://example.supabase.co"
KEY = "sb_secret_offline_fixture"
NOW = "2026-11-05T12:00:00.123456+00:00"


class Scripted:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, copy.deepcopy(kwargs)))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


def test_canonical_text_preserves_true_one_float_and_unicode():
    forms = [_canonical({"x": v}) for v in (True, 1, 1.0)]
    assert len(set(forms)) == 3
    assert _canonical({"z": "é", "a": 1}) == '{"a":1,"z":"\\u00e9"}'
    for encoded in forms:
        assert _canonical(_decode(encoded)) == encoded


@pytest.mark.parametrize("encoded", [
    None, {}, b"{}", '{"a":1,"a":2}', '{"x":NaN}', '{"x":Infinity}',
    '{ "x":1}', '{"x":1e0}', '{"x":true,broken}',
])
def test_upstream_json_is_canonical_or_a_sanitized_failure(encoded):
    with pytest.raises(StoreError) as error:
        _decode(encoded)
    assert error.value.status == 502
    assert str(encoded) not in str(error.value)


def test_explicit_configuration_context_and_no_filesystem_fallback():
    with SupabaseReceiptJournal(ORIGIN, KEY, transport=Scripted([]), page_size=1) as journal:
        assert journal.page_size == 1
    for size in (True, 0, 501, 1.5):
        with pytest.raises(ValueError, match="page_size"):
            SupabaseReceiptJournal(ORIGIN, KEY, page_size=size)
    for url, key in (("http://bad.invalid", KEY), (ORIGIN, "")):
        with pytest.raises(ValueError):
            SupabaseReceiptJournal(url, key)
    with pytest.raises(ValueError, match="callable"):
        SupabaseReceiptJournal(ORIGIN, KEY, transport=object())


def test_objects_validate_content_id_before_transport():
    payload = {"source": "frozen", "x": True}
    assert _object_payload("source_snapshot", fingerprint(payload), payload) == _canonical(payload)
    doc = {"version": 1, "receipts": []}
    oid = fingerprint(doc)
    doc["envelope_id"] = oid
    assert _object_payload("generation", oid, doc) == _canonical(doc)
    for kind, oid, value in (("unknown", oid, doc), ("generation", "wrong", doc),
                              ("source_snapshot", fingerprint(payload), {"source": "frozen", "x": 1})):
        with pytest.raises(ValueError):
            _object_payload(kind, oid, value)


def test_partition_is_exhaustive_disjoint_and_never_bool_source_identity():
    a, b = receipt("a"), receipt("b")
    doc = {"receipts": [{"receipt_id": r["source_receipt_id"]} for r in (a, b)]}
    rejected = [{"source_receipt_id": b["source_receipt_id"], "reason": "unsupported_receipt"}]
    _partition(doc, [a], rejected)
    _partition(doc, [a, b], [])
    _partition({"receipts": []}, [], [])
    for values in (([a], []), ([a, a, b], []), ([a, b], rejected),
                   ([a], [{"source_receipt_id": b["source_receipt_id"], "reason": ""}]),
                   ([a], [{"source_receipt_id": True, "reason": "bad"}])):
        with pytest.raises(ValueError):
            _partition(doc, *values)


@pytest.mark.parametrize("kwargs", [
    {"revision_id": ""}, {"revision_id": 1}, {"revision_id": "x" * 201},
    {"revision_id": "v2", "supersedes": ""},
    {"revision_id": "v2", "supersedes": "v1"},
    {"revision_id": "v2", "supersedes": "v1", "reason": ""},
])
def test_revision_identity_and_reason_are_bounded(kwargs):
    defaults = {"supersedes": None, "reason": None, **kwargs}
    with pytest.raises(ValueError):
        _revision_envelope(outcome(receipt()), **defaults)


def page(rows=(), *, next_after=None, watermark="100"):
    return {"version": 1, "rows": list(rows), "next_after": next_after, "watermark": watermark}


def receipt_row(r, seq="1"):
    return {"receipt_id": r["receipt_id"], "payload_json": _canonical(r),
            "received_utc": NOW, "journal_seq": seq}


def generation(receipts):
    doc = {"version": 1, "receipts": [{"receipt_id": r["source_receipt_id"]} for r in receipts]}
    oid = fingerprint(doc)
    return {**doc, "envelope_id": oid}


def object_row(payload, kind="generation", seq="2"):
    oid = payload["envelope_id"] if kind == "generation" else fingerprint(payload)
    return {"object_id": oid, "kind": kind, "payload_json": _canonical(payload),
            "received_utc": NOW, "journal_seq": seq}


def ack(doc, receipts, rejections=(), status="recorded"):
    return {"version": 1, "object_id": doc["envelope_id"], "durable": True,
            "receipts": [{"source_receipt_id": r["source_receipt_id"],
                          "receipt_id": r["receipt_id"], "status": status} for r in receipts],
            "rejections": list(rejections)}


def generation_row(doc, accepted, *, seq="3", result=None):
    return {"object_id": doc["envelope_id"], "payload_json": _canonical(doc),
            "ack_json": _canonical(result if result is not None else ack(doc, accepted)),
            "recorded_utc": NOW, "journal_seq": seq}


def revision_row(r, *, revision_id="v1", supersedes=None, reason=None,
                 resolved=None, seq="4", clock=NOW):
    payload = {"version": 1, "revision_id": revision_id, "supersedes": supersedes,
               "reason": reason, "resolution": outcome(r) if resolved is None else resolved}
    return {"revision_id": revision_id, "payload_json": _canonical(payload),
            "recorded_utc": clock, "journal_seq": seq}


def journal(responses, **kwargs):
    wire = Scripted(responses)
    return SupabaseReceiptJournal(ORIGIN, KEY, transport=wire, **kwargs), wire


def test_receipt_writes_exact_canonical_payload_and_reports_database_replay():
    r = receipt()
    db, wire = journal([{"version": 1, "added": True}, {"version": 1, "added": False}])
    assert db.append_receipt(r) is True
    assert db.append_receipt(copy.deepcopy(r)) is False
    assert wire.calls == [("POST", "/rest/v1/rpc/selflearn_journal_append_receipt_v1",
                           {"data": {"p_receipt_json": _canonical(r)}})] * 2
    assert "received_utc" not in wire.calls[0][2]["data"]
    corrupted = {**r, "served_prob": .1}
    with pytest.raises(ValueError, match="hash_mismatch"):
        db.append_receipt(corrupted)
    assert len(wire.calls) == 2


def test_object_write_uses_database_derived_hash_input_without_duplicating_body():
    source = {"z": True, "n": 1.0}
    doc = generation([receipt()])
    db, wire = journal([{"version": 1, "added": True}, {"version": 1, "added": False}])
    assert db.append_object("source_snapshot", fingerprint(source), source) is True
    assert db.append_object("generation", doc["envelope_id"], doc) is False
    assert wire.calls[0][2]["data"] == {
        "p_kind": "source_snapshot", "p_object_id": fingerprint(source),
        "p_payload_json": _canonical(source), "p_hash_json": None}
    data = wire.calls[1][2]["data"]
    assert data["p_payload_json"] == _canonical(doc)
    assert data["p_hash_json"] is None
    assert data["p_object_id"] == doc["envelope_id"]


@pytest.mark.parametrize("response", [
    None, [], {}, {"version": True, "added": True}, {"version": 2, "added": True},
    {"version": 1, "added": 1}, {"version": 1}, {"version": 1, "added": True, "secret": "bad"},
])
def test_write_never_acknowledges_malformed_or_truthy_rpc_result(response):
    db, _ = journal([response])
    with pytest.raises(StoreError) as error:
        db.append_receipt(receipt())
    assert error.value.status == 502
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("error,expected", [
    (StoreConflict(), JournalConflict), (StoreError(429), StoreError),
    (RuntimeError("sensitive SQL secret"), StoreError), (OSError("sensitive socket"), StoreError),
])
def test_rpc_failures_are_sanitized_and_never_fallback(error, expected):
    db, wire = journal([error])
    with pytest.raises(expected) as failure:
        db.append_receipt(receipt())
    assert "sensitive" not in str(failure.value)
    assert len(wire.calls) == 1


def test_get_receipt_and_object_use_db_owned_clocks_and_exact_identity():
    r, doc = receipt(), generation([])
    source = {"source": "frozen"}
    db, wire = journal([page([receipt_row(r)]), page(), page([object_row(doc)]),
                        page([object_row(source, "source_snapshot")]), page()])
    assert db.get_receipt(r["receipt_id"]) == r
    assert db.get_receipt("absent") is None
    assert db.get_object(doc["envelope_id"]) == {"object_id": doc["envelope_id"],
        "kind": "generation", "payload": doc, "received_utc": NOW}
    assert db.get_object(fingerprint(source))["payload"] == source
    assert db.get_object("absent") is None
    assert [call[2]["data"]["p_limit"] for call in wire.calls] == [1] * 5
    assert wire.calls[0][2]["data"]["p_key"] == r["receipt_id"]


@pytest.mark.parametrize("mutator", [
    lambda row: {**row, "receipt_id": fingerprint("wrong")},
    lambda row: {**row, "received_utc": "not a DB clock"},
    lambda row: {**row, "payload_json": _canonical({"receipt_id": "wrong"})},
    lambda row: {**row, "received_utc": None},
    lambda row: {**row, "unexpected": "sensitive"},
])
def test_corrupt_receipt_row_fails_closed(mutator):
    r = receipt()
    db, _ = journal([page([mutator(receipt_row(r))])])
    with pytest.raises(StoreError) as error:
        db.get_receipt(r["receipt_id"])
    assert error.value.status == 502


def test_receipt_identity_must_match_lookup_not_only_the_row_payload():
    db, _ = journal([page([receipt_row(receipt("other"))])])
    with pytest.raises(StoreError):
        db.get_receipt(receipt()["receipt_id"])
    db, _ = journal([])
    with pytest.raises(ValueError, match="read_key"):
        db.get_receipt(True)


@pytest.mark.parametrize("mutator", [
    lambda row: {**row, "object_id": fingerprint("wrong")},
    lambda row: {**row, "kind": "unknown"},
    lambda row: {**row, "received_utc": None},
    lambda row: {**row, "unexpected": "sensitive"},
])
def test_corrupt_object_row_fails_closed(mutator):
    doc = generation([])
    db, _ = journal([page([mutator(object_row(doc))])])
    with pytest.raises(StoreError):
        db.get_object(doc["envelope_id"])


def test_get_object_rejects_another_valid_content_address():
    doc, other = generation([]), generation([receipt()])
    db, _ = journal([page([object_row(other)])])
    with pytest.raises(StoreError):
        db.get_object(doc["envelope_id"])


def test_reads_beyond_default_1000_without_truncation_and_pin_first_watermark():
    records = [receipt(str(n)) for n in range(1003)]
    rows = [receipt_row(r, str(i + 1)) for i, r in enumerate(records)]
    db, wire = journal([page(rows[:500], next_after="500", watermark="1003"),
                        page(rows[500:1000], next_after="1000", watermark="1003"),
                        page(rows[1000:], watermark="1003")])
    result = db.receipts()
    assert len(result) == 1003
    assert result == sorted(records, key=lambda r: r["receipt_id"])
    assert [(c[2]["data"]["p_after"], c[2]["data"]["p_watermark"]) for c in wire.calls] == [
        (None, None), ("500", "1003"), ("1000", "1003")]


def test_byte_bounded_short_page_with_next_cursor_is_not_truncated():
    a, b = receipt("short-page-a"), receipt("short-page-b")
    db, wire = journal([page([receipt_row(a, "1")], next_after="1", watermark="2"),
                        page([receipt_row(b, "2")], watermark="2")], page_size=500)
    assert db.receipts() == sorted([a, b], key=lambda row: row["receipt_id"])
    assert wire.calls[1][2]["data"]["p_after"] == "1"
    assert wire.calls[1][2]["data"]["p_watermark"] == "2"


@pytest.mark.parametrize("response", [
    {"version": 1, "rows": [], "next_after": "1", "watermark": "3"},
    page([{"journal_seq": "1"}], next_after="2"),
    page([{"journal_seq": "02"}]), page([{"journal_seq": "2"}], watermark="1"),
    page([{"journal_seq": "2"}, {"journal_seq": "1"}]),
    page([{"journal_seq": 1}]), page([{"journal_seq": "1"}, {"journal_seq": "1"}]),
    page([{"journal_seq": "1"}], watermark="-1"),
    page([{"journal_seq": "1"}], watermark="1" * 21),
    {**page(), "rows": {}}, {**page(), "leaked": "sensitive"},
    page([True]),
])
def test_nonprogressing_oversized_or_malformed_pages_are_not_partial_success(response):
    db, _ = journal([response])
    with pytest.raises(StoreError) as error:
        db._read("receipts")
    assert error.value.status == 502


def test_server_cannot_change_the_watermark_between_pages_or_repeat_cursor():
    for second in (page([{"journal_seq": "2"}], watermark="4"),
                   page([{"journal_seq": "1"}], watermark="3")):
        db, _ = journal([page([{"journal_seq": "1"}], next_after="1", watermark="3"), second])
        with pytest.raises(StoreError):
            db._read("receipts")
    db, _ = journal([page([{"journal_seq": "1"}, {"journal_seq": "2"}])], page_size=1)
    with pytest.raises(StoreError):
        db._read("receipts")


def test_duplicate_receipts_from_distinct_pages_are_not_independent_records():
    r = receipt()
    db, _ = journal([page([receipt_row(r, "1"), receipt_row(r, "2")])])
    with pytest.raises(StoreError):
        db.receipts()
    changed = reseal(r, first_seen_utc="2026-11-01T12:00:02Z")
    db, _ = journal([page([receipt_row(r, "1"), receipt_row(changed, "2")])])
    with pytest.raises(StoreError):
        db.receipts()


def test_generation_commit_sends_one_transaction_and_matches_exact_partition():
    a, b = receipt("a"), receipt("b")
    doc = generation([a, b])
    rejected = [{"source_receipt_id": b["source_receipt_id"], "reason": "unsupported_model"}]
    result = ack(doc, [a], rejected)
    db, wire = journal([page([object_row(doc)]), result])
    assert db.commit_generation(doc["envelope_id"], [a], rejected) == result
    assert wire.calls[-1] == ("POST", "/rest/v1/rpc/selflearn_journal_commit_generation_v1", {"data": {
        "p_object_id": doc["envelope_id"], "p_receipts_json": _canonical([a]),
        "p_rejections_json": _canonical(rejected)}})
    assert len(wire.calls) == 2
    duplicate = ack(doc, [a], rejected, "duplicate")
    db, _ = journal([page([object_row(doc)]), duplicate])
    assert db.commit_generation(doc["envelope_id"], [a], rejected)["receipts"][0]["status"] == "duplicate"


def test_invalid_or_missing_generation_never_calls_a_commit_rpc():
    a, b = receipt("a"), receipt("b")
    doc = generation([a, b])
    for result, expected in ((page(), "generation_object_missing"),
        (page([object_row({"source": "not generation"}, "source_snapshot")]), "generation_object_missing"),
        (page([object_row(doc)]), "partition_mismatch")):
        db, wire = journal([result])
        with pytest.raises((ValueError, StoreError)):
            db.commit_generation(doc["envelope_id"], [a], [])
        assert len(wire.calls) == 1


@pytest.mark.parametrize("mutator", [
    lambda result: {**result, "durable": 1},
    lambda result: {**result, "version": True},
    lambda result: {**result, "object_id": fingerprint("wrong")},
    lambda result: {**result, "receipts": []},
    lambda result: {**result, "receipts": result["receipts"] * 2},
    lambda result: {**result, "receipts": [{**result["receipts"][0], "receipt_id": fingerprint("wrong")}]},
    lambda result: {**result, "receipts": [{**result["receipts"][0], "status": "queued"}]},
    lambda result: {**result, "rejections": [{"source_receipt_id": fingerprint("unknown"), "reason": "bad"}]},
    lambda result: {**result, "rejections": [{"source_receipt_id": fingerprint("unknown"), "reason": ""}]},
    lambda result: {**result, "secret": "bad"},
])
def test_malformed_commit_ack_is_not_a_durable_success(mutator):
    r = receipt()
    doc = generation([r])
    db, _ = journal([page([object_row(doc)]), mutator(ack(doc, [r]))])
    with pytest.raises(StoreError) as error:
        db.commit_generation(doc["envelope_id"], [r], [])
    assert error.value.status == 502


def test_generation_rejections_require_canonical_not_python_equality():
    r = receipt()
    doc = generation([r])
    rejected = [{"source_receipt_id": r["source_receipt_id"], "reason": "unsupported", "detail": True}]
    changed = [{**rejected[0], "detail": 1}]
    db, _ = journal([page([object_row(doc)]), ack(doc, [], changed)])
    with pytest.raises(StoreError):
        db.commit_generation(doc["envelope_id"], [], rejected)


def test_generation_ack_read_and_absent_record():
    doc = generation([receipt()])
    result = ack(doc, [receipt()])
    row = {"object_id": doc["envelope_id"], "ack_json": json.dumps(result, indent=2), "journal_seq": "3"}
    db, _ = journal([page([row]), page()])
    assert db.generation_ack(doc["envelope_id"]) == result
    assert db.generation_ack("absent") is None
    for wrong in ({**row, "object_id": "wrong"}, {**row, "extra": "bad"}):
        db, _ = journal([page([wrong])])
        with pytest.raises(StoreError):
            db.generation_ack(doc["envelope_id"])


def test_generation_lists_only_committed_records_and_bounded_heavy_pages():
    a, b = receipt("a"), receipt("b")
    doc1, doc2 = generation([a]), generation([b])
    first = generation_row(doc1, [a], seq="3")
    first["ack_json"] = json.dumps(ack(doc1, [a]), indent=2)
    db, wire = journal([page([first], next_after="3"),
                        page([generation_row(doc2, [b], seq="4")])])
    result = db.generations()
    assert {row["object_id"] for row in result} == {doc1["envelope_id"], doc2["envelope_id"]}
    assert all(c[2]["data"]["p_view"] == "generations" and c[2]["data"]["p_limit"] == 1 for c in wire.calls)
    assert all(row["recorded_utc"] == NOW for row in result)


def test_resolution_append_keeps_exact_final_void_revisions_and_database_cas():
    r = receipt()
    value = outcome(r)
    db, wire = journal([page([receipt_row(r)]), {"version": 1, "added": True},
                        page([receipt_row(r)]), {"version": 1, "added": False}])
    assert db.append_resolution(value, revision_id="v1") is True
    assert db.append_resolution(value, revision_id="v1") is False
    expected = _revision_envelope(value, "v1", None, None)
    assert wire.calls[1] == ("POST", "/rest/v1/rpc/selflearn_journal_append_resolution_v1",
                             {"data": {"p_revision_json": _canonical(expected)}})
    void = {**outcome(r), "status": "VOID", "hit": None, "authoritative": True,
            "final_evidence_status": "FINAL", "reason": "official DNP"}
    db, wire = journal([page([receipt_row(r)]), {"version": 1, "added": True}])
    assert db.append_resolution(void, revision_id="v2", supersedes="v1", reason="official correction") is True
    sent = json.loads(wire.calls[1][2]["data"]["p_revision_json"])
    assert sent["supersedes"] == "v1" and sent["resolution"]["hit"] is None
    assert "recorded_utc" not in sent
    db, _ = journal([page([receipt_row(r)]), StoreConflict()])
    with pytest.raises(JournalConflict):
        db.append_resolution(outcome(r), revision_id="stale", supersedes="old", reason="fork")


def test_resolution_requires_existing_original_contract_and_final_evidence():
    r = receipt()
    db, wire = journal([page()])
    with pytest.raises(ValueError, match="unknown_receipt"):
        db.append_resolution(outcome(r), revision_id="v1")
    assert len(wire.calls) == 1
    for changes in ({"hit": 1}, {"status": "LIVE"}, {"contract_id": "wrong"}, {"status": "VOID"}):
        db, wire = journal([page([receipt_row(r)])])
        with pytest.raises(ValueError):
            db.append_resolution({**outcome(r), **changes}, revision_id="v1")
        assert len(wire.calls) == 1


def test_complete_revision_history_and_current_void_head_stay_distinct():
    r = receipt()
    v1 = revision_row(r)
    void = {**outcome(r), "status": "VOID", "hit": None, "authoritative": True,
            "final_evidence_status": "FINAL", "reason": "official DNP"}
    v2 = revision_row(r, revision_id="v2", supersedes="v1", reason="official correction", resolved=void, seq="5")
    db, wire = journal([page([v1, v2]), page([receipt_row(r)])])
    history = db.resolution_history(r["receipt_id"])
    assert [row["revision_id"] for row in history] == ["v1", "v2"]
    assert history[0]["resolution"]["hit"] is True and history[1]["resolution"]["hit"] is None
    assert wire.calls[1][2]["data"]["p_watermark"] == "100"
    db, _ = journal([page([v2]), page([receipt_row(r)])])
    assert db.latest_resolutions() == [void]
    db, wire = journal([page()])
    assert db.resolution_history("absent") == [] and len(wire.calls) == 1


def test_atomic_read_snapshot_pins_all_views_and_keeps_every_revision():
    r = receipt()
    doc = generation([r])
    v1 = revision_row(r)
    v2 = revision_row(r, revision_id="v2", supersedes="v1", reason="official correction",
                      resolved=outcome(r, False), seq="5")
    db, wire = journal([page([receipt_row(r)], watermark="5"),
                        page([generation_row(doc, [r])], watermark="5"),
                        page([v1, v2], watermark="5")])
    snap = db.read_snapshot()
    assert snap["version"] == 1 and snap["watermark"] == "5"
    assert snap["receipts"] == [r]
    assert [row["revision_id"] for row in snap["revisions"]] == ["v1", "v2"]
    assert snap["generation_records"][0]["ack"] == ack(doc, [r])
    assert [(c[2]["data"]["p_view"], c[2]["data"]["p_watermark"]) for c in wire.calls] == [
        ("receipts", None), ("generations", "5"), ("revisions", "5")]
    assert r["served_prob"] == .7


@pytest.mark.parametrize("revision", [
    lambda r: revision_row(r, supersedes="missing", reason="orphan"),
    lambda r: {**revision_row(r), "revision_id": "wrong"},
    lambda r: {**revision_row(r), "recorded_utc": "2026-11-03T12:00:00Z"},
    lambda r: {**revision_row(r), "extra": "bad"},
    lambda r: {**revision_row(r), "payload_json": _canonical({"version": True})},
])
def test_snapshot_refuses_broken_revision_history_or_db_clock(revision):
    r = receipt()
    db, _ = journal([page([receipt_row(r)]), page(), page([revision(r)])])
    with pytest.raises(StoreError):
        db.read_snapshot()


def test_snapshot_rejects_ack_receipt_absent_from_its_pinned_snapshot():
    r = receipt()
    doc = generation([r])
    db, _ = journal([page(), page([generation_row(doc, [r])])])
    with pytest.raises(StoreError):
        db.read_snapshot()


def test_status_requires_real_versioned_postgres_contract_and_cannot_claim_connected_from_env():
    good = {"schema_version": 1, "backend": "postgres", "journal_seq": "0"}
    db, _ = journal([page([good], watermark="0")])
    assert db.status() == {"schema_version": 1, "backend": "postgres", "watermark": "0"}
    for row in (None, {**good, "schema_version": True}, {**good, "backend": "sqlite"}, {**good, "extra": "secret"}):
        db, _ = journal([page([] if row is None else [row], watermark="0")])
        with pytest.raises(StoreError):
            db.status()


def test_injected_bound_request_and_real_transport_serialize_text_not_jsonb():
    wire = Scripted([{"version": 1, "added": True}])
    db = SupabaseReceiptJournal(ORIGIN, KEY, transport=wire.request)
    assert db.append_receipt(receipt()) is True
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, maximum):
            return b'{"version":1,"added":true}'
    class Opener:
        def open(self, request, timeout):
            assert request.full_url == ORIGIN + "/rest/v1/rpc/selflearn_journal_append_receipt_v1"
            body = json.loads(request.data)
            assert type(body["p_receipt_json"]) is str
            assert body["p_receipt_json"] == _canonical(receipt())
            assert "Authorization" not in request.headers
            return Response()
    transport = SupabaseStore(ORIGIN, KEY)
    transport._opener = Opener()
    assert SupabaseReceiptJournal(ORIGIN, KEY, transport=transport).append_receipt(receipt()) is True
