"""Generic DTO/SQLite proof only; no owner HTTP auth or model adoption claim."""
import copy
import sqlite3

import pytest

from selflearn_core import owner_intents as dto
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.owner_intent_journal import OwnerIntentJournal
from selflearn_core.store.receipt_journal import JournalConflict


AUTHORITY = fingerprint("offline-owner-authority")
ACTOR = "configured-single-owner"
NS = {"domain": "example", "task": "binary.forecast", "producer_id": "local-producer"}
OBJECT = {"unverified_evidence": {"coefficients": {"a": 0.0, "b": 1.0}}}
OID = fingerprint(OBJECT)


def command(*, head=None, action="approve", artifact="artifact-a", refs=None, **changes):
    value = {"version": 1, "kind": "selflearn.owner_intent_command.v1", "namespace": NS.copy(),
        "action": action, "expected_head_event_id": head,
        "pins": {k: fingerprint(k) for k in dto.PIN_KEYS - {"evidence_object_ids"}},
        "effective_from_utc": None, "expires_utc": None if action == "revoke" else "2099-01-01T00:00:00Z",
        "reason": "Explicit owner intent; no serving activation"}
    value["pins"]["artifact_id"] = fingerprint(artifact)
    value["pins"]["evidence_object_ids"] = [OID] if refs is None else refs
    value.update(changes)
    return dto.seal_command(value, authority_id=AUTHORITY)


@pytest.fixture
def journal(tmp_path):
    with OwnerIntentJournal(tmp_path / "journal.sqlite", authority_id=AUTHORITY, actor_ref=ACTOR, page_size=1) as value:
        value.append_object("source_snapshot", OID, OBJECT)
        yield value


def test_strict_types_canonical_identity_and_detached_builders():
    value = command(); untouched = copy.deepcopy(value)
    assert dto.decode(dto.canonical(value)) == value
    assert dto.validate_command(value, authority_id=AUTHORITY) == value
    value["pins"]["artifact_id"] = fingerprint("changed")
    assert untouched["pins"]["artifact_id"] != value["pins"]["artifact_id"]
    with pytest.raises(ValueError, match="hash"):
        dto.validate_command(value, authority_id=AUTHORITY)
    with pytest.raises(ValueError, match="hash"):
        dto.validate_command(untouched, authority_id=fingerprint("other-authority"))


@pytest.mark.parametrize("patch", [
    {"version": True}, {"version": 1.0}, {"actor_ref": ACTOR}, {"recorded_utc": "2026-01-01T00:00:00Z"},
    {"expected_head_event_id": "bad"}, {"reason": " "}, {"reason": "x" * 1001},
    {"expires_utc": "2099-01-01"}, {"expires_utc": "2099-01-01T00:00:00"},
    {"expires_utc": "2200-01-01T00:00:00Z"}, {"action": "promote"}, {"action": []},
    {"namespace": {**NS, "producer_id": "../wrong"}}, {"namespace": {**NS, "owner": ACTOR}},
    {"pins": {}}, {"pins": {"artifact_id": True}}, {"command_id": fingerprint("wrong")},
])
def test_invalid_commands_never_normalize_into_authority(patch):
    value = command(); value.update(patch)
    with pytest.raises((ValueError, TypeError)):
        dto.validate_command(value, authority_id=AUTHORITY)


@pytest.mark.parametrize("text", ['{"x":1,"x":2}', '{ "x":1}', '{"x":NaN}', '{"x":1.0}', '{"x":Infinity}', '{broken'])
def test_raw_json_duplicate_float_nonfinite_and_noncanonical_rejected(text):
    with pytest.raises(ValueError): dto.decode(text)


def test_resource_and_plain_json_types_are_bounded_before_encoding():
    class Imposter(str): pass
    for value in ({"x": Imposter("x")}, {"x": float("nan")}, {"x": object()}, {True: 1}, {"x": 2 ** 80}):
        with pytest.raises(ValueError): dto.snapshot(value)
    deep = None
    for _ in range(26): deep = [deep]
    with pytest.raises(ValueError): dto.snapshot(deep)
    with pytest.raises(ValueError): dto.snapshot("x" * 20, limit=10)
    with pytest.raises(ValueError): dto.decode("x" * 20, limit=10)
    with pytest.raises(ValueError): dto.actor("owner\nsecret")
    with pytest.raises(ValueError): dto.sequence(True)
    with pytest.raises(ValueError): dto.sequence("01")


def test_reference_order_and_revocation_expiry_are_not_coerced():
    for refs in ([], [OID, OID], ["bad"], [fingerprint("z"), OID]):
        if refs == sorted(set(refs)) and refs and all(dto.HASH.fullmatch(r) for r in refs): continue
        with pytest.raises(ValueError): command(refs=refs)
    with pytest.raises(ValueError): command(action="revoke", expires_utc="2099-01-01T00:00:00Z")
    with pytest.raises(ValueError): command(effective_from_utc="2099-02-01T00:00:00Z")


def test_sqlite_approve_revoke_reapprove_and_compatible_rollback_are_intents_only(journal):
    first = journal.append_owner_intent(command())
    revoke = journal.append_owner_intent(command(head=first["event_id"], action="revoke"))
    with pytest.raises(ValueError, match="rollback"):
        journal.append_owner_intent(command(head=revoke["event_id"], action="rollback"))
    again = journal.append_owner_intent(command(head=revoke["event_id"]))
    second = journal.append_owner_intent(command(head=again["event_id"], artifact="artifact-b"))
    rolled = journal.append_owner_intent(command(head=second["event_id"], action="rollback"))
    result = journal.read_owner_intents(NS)
    assert result["head_sequence"] == 5 and result["head_event_id"] == rolled["event_id"]
    assert [e["command"]["action"] for e in result["events"]] == ["approve", "revoke", "approve", "approve", "rollback"]
    assert all(result[k] == v for k, v in dto.FLAGS.items())
    assert result["complete"] is True and "claim" not in result["kind"]
    assert journal.receipts() == [] and journal.generations() == [] and journal.latest_resolutions() == []
    assert journal.append_owner_intent(command()) == first  # Original, not current approval.
    assert journal.read_owner_intents(NS)["head_sequence"] == 5


def test_sqlite_stale_head_expiry_wrong_refs_and_atomic_rollback(journal):
    before = journal.read_owner_intents(NS)
    for cmd in (command(refs=[fingerprint("absent")]), command(expires_utc="2001-01-01T00:00:00Z"), command(action="rollback")):
        with pytest.raises(ValueError): journal.append_owner_intent(cmd)
        assert journal.read_owner_intents(NS)["events"] == before["events"]
    ack = journal.append_owner_intent(command())
    with pytest.raises(JournalConflict): journal.append_owner_intent(command(artifact="other"))
    assert journal.read_owner_intents(NS)["head_event_id"] == ack["event_id"]
    doc = {"receipts": []}; gid = fingerprint(doc); doc["envelope_id"] = gid
    journal.append_object("generation", gid, doc)
    with pytest.raises(ValueError, match="evidence"):
        journal.append_owner_intent(command(head=ack["event_id"], refs=[gid]))


def test_sqlite_pinned_pages_ignore_later_owner_or_other_namespace_events(journal):
    first = journal.append_owner_intent(command())
    second = journal.append_owner_intent(command(head=first["event_id"], action="revoke"))
    page = journal.read_owner_intents_page(NS, limit=1)
    journal.append_owner_intent(command(head=second["event_id"]))
    tail = journal.read_owner_intents_page(NS, after=page["next_after"], watermark=page["watermark"], limit=1)
    assert tail["head_sequence"] == 2 and tail["rows"][0]["event"]["event_id"] == second["event_id"]
    assert tail["next_after"] is None
    assert journal.read_owner_intents({**NS, "producer_id": "other"})["events"] == []
    for kwargs in ({"after": "9"}, {"watermark": "99"}, {"limit": True}, {"limit": 101}):
        with pytest.raises(ValueError): journal.read_owner_intents_page(NS, **kwargs)


def test_local_file_owner_tampering_is_detected_not_claimed_as_db_role_auth(journal):
    first = journal.append_owner_intent(command())
    journal.append_owner_intent(command(head=first["event_id"], action="revoke"))
    for sql in ("UPDATE owner_intent_events_v1 SET sequence=9", "DELETE FROM owner_intent_events_v1"):
        with pytest.raises(sqlite3.IntegrityError): journal._db.execute(sql)
    # Deliberately privileged local-file tampering: no false complete snapshot.
    journal._db.execute("DROP TRIGGER owner_intent_no_delete_v1")
    journal._db.execute("DELETE FROM owner_intent_events_v1 WHERE sequence=1")
    journal._db.commit()
    with pytest.raises(ValueError, match="incomplete"):
        journal.read_owner_intents(NS)


def test_core_clock_chain_hash_and_rollback_pin_guards():
    cmd = command()
    event = dto.make_event(cmd, [], authority_id=AUTHORITY, actor_ref=ACTOR, recorded_utc="2026-10-16T00:00:00Z")
    assert dto.acknowledgement(event)["activation"] is False
    for mutate in (lambda e: e.update(sequence=True), lambda e: e.update(actor_ref="bad\n"),
                   lambda e: e.update(recorded_utc="2099-02-01T00:00:00Z"), lambda e: e.update(event_id=OID)):
        bad = copy.deepcopy(event); mutate(bad)
        with pytest.raises(ValueError): dto.validate_event(bad, authority_id=AUTHORITY, expected_namespace=NS)
    bad = command(head=event["event_id"], action="rollback")
    bad["pins"]["policy_id"] = fingerprint("wrong-policy")
    bad = dto.seal_command({k: v for k, v in bad.items() if k != "command_id"}, authority_id=AUTHORITY)
    with pytest.raises(ValueError, match="rollback"):
        dto.make_event(bad, [event], authority_id=AUTHORITY, actor_ref=ACTOR, recorded_utc="2026-10-16T00:01:00Z")


def test_sqlite_constructor_and_empty_history(tmp_path):
    for size in (True, 0, 101):
        with pytest.raises(ValueError): OwnerIntentJournal(tmp_path / "x", authority_id=AUTHORITY, actor_ref=ACTOR, page_size=size)
    with OwnerIntentJournal(":memory:", authority_id=AUTHORITY, actor_ref=ACTOR) as j:
        page = j.read_owner_intents_page(NS)
        assert page["watermark"] == "0" and page["head_event_id"] is None and page["rows"] == []
        assert j.read_owner_intents(NS)["complete"] is True
