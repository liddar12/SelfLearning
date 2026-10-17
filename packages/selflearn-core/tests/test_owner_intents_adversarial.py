"""Independent bounded-history/CAS checks. SQLite threads are NOT native PG proof."""
import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from selflearn_core import owner_intents as dto
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.owner_intent_journal import OwnerIntentJournal
from selflearn_core.store.receipt_journal import JournalConflict
from selflearn_core.store.supabase_owner_intents import SupabaseOwnerIntentJournal
from selflearn_core.store.supabase_store import StoreError


AUTHORITY = fingerprint("independent-owner-history-boundary")
ACTOR = "independent-server-configured-owner"
NS = {"domain": "example", "task": "binary.test", "producer_id": "local-only"}
EVIDENCE = {"fixture": "not-domain-verified", "value": 1}
OID = fingerprint(EVIDENCE)


def command(head=None, *, namespace=None, reason="Local mechanics only", action="approve", **changes):
    value = {"version": 1, "kind": "selflearn.owner_intent_command.v1",
             "namespace": copy.deepcopy(NS if namespace is None else namespace),
             "action": action, "expected_head_event_id": head,
             "pins": {key: fingerprint(key) for key in dto.PIN_KEYS - {"evidence_object_ids"}},
             "effective_from_utc": None,
             "expires_utc": None if action == "revoke" else "2099-01-01T00:00:00Z", "reason": reason}
    value["pins"]["evidence_object_ids"] = [OID]
    value.update(changes)
    return dto.seal_command(value, authority_id=AUTHORITY)


def test_maximum_history_reads_all_pages_and_original_retry_survives_capacity(tmp_path):
    with OwnerIntentJournal(tmp_path / "capacity.sqlite", authority_id=AUTHORITY,
                            actor_ref=ACTOR, page_size=7) as journal:
        journal.append_object("source_snapshot", OID, EVIDENCE)
        original_command = command()
        first = journal.append_owner_intent(original_command)
        head = first["event_id"]
        for _ in range(dto.MAX_EVENTS - 1):
            head = journal.append_owner_intent(command(head))["event_id"]
        before = journal.read_owner_intents(NS)
        assert before["head_sequence"] == 256 and before["head_event_id"] == head
        assert len(before["events"]) == 256
        assert [event["sequence"] for event in before["events"]] == list(range(1, 257))
        with pytest.raises(ValueError, match="capacity"):
            journal.append_owner_intent(command(head))
        assert journal.append_owner_intent(original_command) == first
        assert journal.read_owner_intents(NS)["events"] == before["events"]
        other = {**NS, "producer_id": "separate-local-only"}
        assert journal.read_owner_intents(other)["head_sequence"] == 0
        assert journal.append_owner_intent(command(namespace=other))["sequence"] == 1
        assert journal.read_owner_intents(NS)["head_sequence"] == 256
        assert journal.receipts() == [] and journal.generations() == [] and journal.latest_resolutions() == []


def test_actual_separate_sqlite_connections_have_one_winning_head_compare_and_swap(tmp_path):
    path = tmp_path / "concurrent.sqlite"
    with OwnerIntentJournal(path, authority_id=AUTHORITY, actor_ref=ACTOR) as journal:
        journal.append_object("source_snapshot", OID, EVIDENCE)
    barrier = Barrier(2)

    def writer(label):
        with OwnerIntentJournal(path, authority_id=AUTHORITY, actor_ref=ACTOR) as journal:
            barrier.wait(timeout=10)
            try:
                return ("saved", journal.append_owner_intent(command(reason=label)))
            except JournalConflict:
                return ("conflict", None)

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(writer, label) for label in ("first-local-writer", "second-local-writer")]
        results = [future.result(timeout=20) for future in futures]
    assert sorted(result[0] for result in results) == ["conflict", "saved"]
    with OwnerIntentJournal(path, authority_id=AUTHORITY, actor_ref=ACTOR) as journal:
        history = journal.read_owner_intents(NS)
        assert history["head_sequence"] == 1
        assert history["head_event_id"] == next(value["event_id"] for status, value in results if status == "saved")
        assert history["activation"] is False


def test_revocation_is_recordable_after_expiry_and_never_restores_an_older_approval():
    first_command = command(expires_utc="2026-10-01T00:00:00Z")
    first = dto.make_event(first_command, [], authority_id=AUTHORITY, actor_ref=ACTOR,
                           recorded_utc="2026-09-30T00:00:00Z")
    revoke = dto.make_event(command(first["event_id"], action="revoke"), [first],
                            authority_id=AUTHORITY, actor_ref=ACTOR, recorded_utc="2026-10-02T00:00:00Z")
    assert revoke["command"]["expires_utc"] is None
    with pytest.raises(ValueError, match="rollback"):
        dto.make_event(command(revoke["event_id"], action="rollback"), [first, revoke],
                       authority_id=AUTHORITY, actor_ref=ACTOR, recorded_utc="2026-10-03T00:00:00Z")
    assert dto.complete_snapshot([first, revoke], authority_id=AUTHORITY, expected_namespace=NS,
        watermark="2", observed_utc="2026-10-03T00:00:00Z")["events"][-1]["command"]["action"] == "revoke"


def test_rehashed_history_cannot_hide_recorded_clock_regression():
    first = dto.make_event(command(), [], authority_id=AUTHORITY, actor_ref=ACTOR,
                           recorded_utc="2026-10-02T00:00:00Z")
    with pytest.raises(ValueError, match="clock"):
        dto.make_event(command(first["event_id"]), [first], authority_id=AUTHORITY, actor_ref=ACTOR,
                       recorded_utc="2026-10-01T00:00:00Z")


def test_unending_well_shaped_rpc_pages_are_bounded_not_a_complete_history():
    event = dto.make_event(command(), [], authority_id=AUTHORITY, actor_ref=ACTOR,
                           recorded_utc="2026-10-02T00:00:00Z")
    calls = []

    def transport(method, path, *, data):
        calls.append(data)
        cursor = int(data["p_after"] or "0") + 1
        return {"version": 1, "authority_id": AUTHORITY, "namespace": NS,
                "rows": [{"event": event, "ledger_seq": str(cursor)}], "next_after": str(cursor),
                "watermark": "10000", "head_event_id": event["event_id"],
                "head_sequence": dto.MAX_EVENTS, "observed_utc": "2026-10-03T00:00:00Z"}

    with SupabaseOwnerIntentJournal("https://example.supabase.co", "sb_secret_fixture_only",
        authority_id=AUTHORITY, actor_ref=ACTOR, transport=transport, page_size=1) as journal:
        with pytest.raises(StoreError):
            journal.read_owner_intents(NS)
    assert len(calls) == dto.MAX_EVENTS
