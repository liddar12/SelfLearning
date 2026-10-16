"""Independent owner-foundation checks; local integrity, not hosted authority.

These cases pin the two boundary failures found during independent review:
untrusted typed backend errors and cumulative JSON allocation before a cap.
They do not claim provider authenticity, semantic pin binding, or activation.
"""
import copy
import tracemalloc

import pytest

from selflearn_core import owner_intents as dto
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.owner_intent_journal import OwnerIntentJournal
from selflearn_core.store.receipt_journal import JournalConflict
from selflearn_core.store.supabase_owner_intents import SupabaseOwnerIntentJournal
from selflearn_core.store.supabase_store import StoreConflict, StoreError


PRIVATE = "PRIVATE_PROVIDER_CREDENTIAL"
AUTHORITY = fingerprint("independent-owner-review-authority")
ACTOR = "independent-review-configured-owner"
NS = {"domain": "example", "task": "binary.review", "producer_id": "offline-review"}
EVIDENCE = {"kind": "unverified-domain-evidence", "value": 1}
EVIDENCE_ID = fingerprint(EVIDENCE)


def make_command(*, namespace=None, authority=AUTHORITY, head=None, action="approve", artifact="a"):
    payload = {"version": 1, "kind": "selflearn.owner_intent_command.v1",
        "namespace": copy.deepcopy(NS if namespace is None else namespace),
        "action": action, "expected_head_event_id": head,
        "pins": {key: fingerprint(key) for key in dto.PIN_KEYS - {"evidence_object_ids"}},
        "effective_from_utc": None,
        "expires_utc": None if action == "revoke" else "2099-01-01T00:00:00Z",
        "reason": "Independent local review fixture; no activation authority"}
    payload["pins"]["artifact_id"] = fingerprint(artifact)
    payload["pins"]["evidence_object_ids"] = [EVIDENCE_ID]
    return dto.seal_command(payload, authority_id=authority)


def failing_port(error):
    def transport(*args, **kwargs):
        raise error
    return SupabaseOwnerIntentJournal("https://example.supabase.co", "sb_secret_fixture_only",
        authority_id=AUTHORITY, actor_ref=ACTOR, transport=transport)


@pytest.mark.parametrize("status,expected", [
    (PRIVATE, 503), (True, 503), (None, 503), (429.0, 503), (399, 503), (600, 503),
    (400, 400), (429, 429), (503, 503), (599, 599),
])
def test_typed_store_error_cannot_export_untrusted_status_or_message(status, expected):
    class UntrustedError(StoreError):
        def __str__(self):
            return PRIVATE
    with failing_port(UntrustedError(status)) as port:
        with pytest.raises(StoreError) as caught:
            port.read_owner_intents(NS)
    assert type(caught.value.status) is int and caught.value.status == expected
    assert PRIVATE not in str(caught.value)
    assert caught.value.__suppress_context__ is True


@pytest.mark.parametrize("kind", [JournalConflict, StoreConflict])
def test_typed_conflict_never_rethrows_private_provider_message(kind):
    class UntrustedConflict(kind):
        def __str__(self):
            return PRIVATE
    error = UntrustedConflict()
    with failing_port(error) as port:
        with pytest.raises(JournalConflict) as caught:
            port.read_owner_intents(NS)
    assert str(caught.value) == "owner_head_conflict"
    assert caught.value.__suppress_context__ is True


@pytest.mark.parametrize("cancel", [KeyboardInterrupt, SystemExit])
def test_cancellation_is_not_reclassified_as_an_owner_backend_failure(cancel):
    with failing_port(cancel()) as port:
        with pytest.raises(cancel):
            port.read_owner_intents(NS)


def test_cumulative_overbudget_aliases_rejected_before_whole_serialization(monkeypatch):
    # Only about 12 KiB of distinct caller allocations, but over 4 MiB if
    # expanded. The pre-fix snapshot allocated 8,198,416 traced bytes first.
    value = ["x" * 4096] * 1000
    original = dto.canonical
    encoded_containers = []

    def spy(item):
        if type(item) in (dict, list):
            encoded_containers.append(type(item).__name__)
        return original(item)

    monkeypatch.setattr(dto, "canonical", spy)
    tracemalloc.start()
    try:
        with pytest.raises(ValueError, match="owner_intent_byte_limit"):
            dto.snapshot(value, limit=128 * 1024)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert encoded_containers == [], "must not serialize an already overbudget container"
    assert peak < 2 * 1024 * 1024


@pytest.mark.parametrize("value", [
    "", "plain/ascii", '"\\\b\f\n\r\t', "\x01\x1f\x7f", "é", "😀",
    {"é😀\"\\\n": [None, True, False, -17, "a/😀\t"]},
    [[], {}, [1, 2], {"two": {"a": "😀"}}],
])
def test_exact_canonical_escaped_unicode_and_container_byte_boundaries(value):
    expected = copy.deepcopy(value)
    size = len(dto.canonical(value).encode("utf-8"))
    assert dto.snapshot(value, limit=size) == expected
    with pytest.raises(ValueError, match="owner_intent_byte_limit"):
        dto.snapshot(value, limit=size - 1)


@pytest.mark.parametrize("value", [{"\x00": 1}, {"\ud800": 1}, {"x": "\udfff"}, "\x00"])
def test_invalid_unicode_in_keys_and_values_is_never_serialized(value, monkeypatch):
    original = dto.canonical
    containers = []
    def spy(item):
        if type(item) in (dict, list):
            containers.append(item)
        return original(item)
    monkeypatch.setattr(dto, "canonical", spy)
    with pytest.raises(ValueError):
        dto.snapshot(value)
    assert containers == []


def test_content_references_are_integrity_only_namespace_and_training_stay_separate(tmp_path):
    # Actual content is checked; no semantic relationship between the supplied
    # candidate/policy pins and this flat evidence object is represented.
    path = tmp_path / "independent-owner.sqlite"
    with OwnerIntentJournal(path, authority_id=AUTHORITY, actor_ref=ACTOR, page_size=1) as journal:
        journal.append_object("source_snapshot", EVIDENCE_ID, EVIDENCE)
        first_command = make_command()
        first = journal.append_owner_intent(first_command)
        revoke = journal.append_owner_intent(make_command(head=first["event_id"], action="revoke"))
        with pytest.raises(ValueError, match="rollback"):
            journal.append_owner_intent(make_command(head=revoke["event_id"], action="rollback"))
        assert journal.append_owner_intent(first_command) == first
        stored = journal.read_owner_intents(NS)
        assert stored["head_event_id"] == revoke["event_id"] and stored["head_sequence"] == 2
        assert stored["domain_pin_binding_verified"] is False
        assert stored["current_serving_verified"] is False and stored["activation"] is False
        assert journal.read_owner_intents({**NS, "producer_id": "other"})["events"] == []
        assert journal.receipts() == [] and journal.generations() == [] and journal.latest_resolutions() == []
    with OwnerIntentJournal(path, authority_id=fingerprint("another-authority"), actor_ref=ACTOR) as other:
        assert other.read_owner_intents(NS)["events"] == []
        with pytest.raises(ValueError, match="hash"):
            other.append_owner_intent(first_command)


def test_valid_next_event_cannot_commit_an_unreadable_complete_history(tmp_path):
    """A count cap alone permits >3 MiB of individually valid Unicode events.

    Seed a completely validated prefix solely to avoid 230 quadratic test-only
    appends. The action under test is the real serialized journal append, with
    actual content references and a database-owned insertion clock.
    """
    with OwnerIntentJournal(tmp_path / "readable-capacity.sqlite", authority_id=AUTHORITY,
                            actor_ref=ACTOR) as journal:
        journal.append_object("source_snapshot", EVIDENCE_ID, EVIDENCE)
        recorded = journal._db.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0]
        history, rows, used = [], [], 2
        for sequence in range(1, dto.MAX_EVENTS + 1):
            head = history[-1]["event_id"] if history else None
            command = make_command(head=head)
            command["reason"] = "😀" * 1000
            command = dto.seal_command({k: v for k, v in command.items() if k != "command_id"},
                                       authority_id=AUTHORITY)
            event = {"version": 1, "kind": "selflearn.owner_intent_event.v1",
                "authority_id": AUTHORITY, "actor_ref": ACTOR, "namespace": NS.copy(),
                "sequence": sequence, "previous_event_id": head, "command": command,
                "recorded_utc": recorded, "effective_from_utc": recorded}
            event["event_id"] = fingerprint(event)
            encoded = dto.canonical(event)
            projected = used + len(encoded.encode("utf-8")) + bool(history)
            if projected > dto.MAX_PAGE_BYTES:
                break
            used = projected
            history.append(event)
            rows.append((AUTHORITY, dto.canonical(NS), sequence, command["command_id"],
                         event["event_id"], head, encoded))
        assert 1 < len(history) < dto.MAX_EVENTS
        dto.validate_history(history, authority_id=AUTHORITY, expected_namespace=NS)
        with journal._db:
            journal._db.executemany("INSERT INTO owner_intent_events_v1 "
                "(authority_id,namespace_json,sequence,command_id,event_id,previous_event_id,payload_json) "
                "VALUES(?,?,?,?,?,?,?)", rows)
        before = journal.read_owner_intents(NS)
        assert before["head_sequence"] == len(history)
        with pytest.raises(ValueError, match="capacity|byte_limit"):
            journal.append_owner_intent(command)
        assert journal.read_owner_intents(NS)["events"] == before["events"]
        assert journal.append_owner_intent(history[0]["command"]) == dto.acknowledgement(history[0])
