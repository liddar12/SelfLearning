"""Offline RPC-port tests over actual local SQLite; NOT hosted SQL proof."""
import copy

import pytest

from selflearn_core import owner_intents as dto
from selflearn_core.store.receipt_journal import JournalConflict
from selflearn_core.store.supabase_owner_intents import SupabaseOwnerIntentJournal
from selflearn_core.store.supabase_store import StoreConflict, StoreError
from test_owner_intents import ACTOR, AUTHORITY, NS, command, journal


class Transport:
    def __init__(self, journal): self.journal, self.calls, self.mutate = journal, [], None
    def request(self, method, path, *, data):
        self.calls.append((method, path, copy.deepcopy(data)))
        assert method == "POST" and data["p_authority_id"] == AUTHORITY
        if path.endswith("append_owner_intent_v1"):
            assert data["p_actor_ref"] == ACTOR
            result = self.journal.append_owner_intent(dto.decode(data["p_command_json"]))
        else:
            assert path.endswith("read_owner_intents_v1")
            result = self.journal.read_owner_intents_page(dto.decode(data["p_namespace_json"]),
                after=data["p_after"], watermark=data["p_watermark"], limit=data["p_limit"])
        return self.mutate(path, copy.deepcopy(result)) if self.mutate else result


def port(transport, **kwargs):
    return SupabaseOwnerIntentJournal("https://example.supabase.co", "sb_secret_fixture_only", authority_id=AUTHORITY,
        actor_ref=ACTOR, transport=transport, page_size=1, **kwargs)


def test_exact_original_ack_after_revoke_and_complete_pinned_read(journal):
    tr = Transport(journal)
    with port(tr) as hosted:
        first = hosted.append_owner_intent(command())
        hosted.append_owner_intent(command(head=first["event_id"], action="revoke"))
        assert hosted.append_owner_intent(command()) == first
        history = hosted.read_owner_intents(NS)
        assert history["head_sequence"] == 2 and history["activation"] is False
    assert any(c[2].get("p_watermark") == "2" for c in tr.calls)


@pytest.mark.parametrize("field,value", [("version", True), ("head_sequence", 0), ("head_event_id", None),
    ("watermark", "01"), ("namespace", {}), ("rows", []), ("next_after", "99"), ("observed_utc", "2001-01-01T00:00:00Z")])
def test_bad_or_incomplete_pages_fail_closed(journal, field, value):
    journal.append_owner_intent(command()); tr = Transport(journal)
    def mutate(path, row): row[field] = value; return row
    tr.mutate = mutate
    with pytest.raises(StoreError): port(tr).read_owner_intents(NS)


def test_changed_head_across_pages_and_duplicated_rows_refused(journal):
    first = journal.append_owner_intent(command()); journal.append_owner_intent(command(head=first["event_id"], action="revoke"))
    tr = Transport(journal)
    def mutate(path, row):
        if row["rows"][0]["event"]["sequence"] == 2: row["head_sequence"] = 1
        return row
    tr.mutate = mutate
    with pytest.raises(StoreError): port(tr).read_owner_intents(NS)
    tr.mutate = lambda path, row: {**row, "rows": [{**row["rows"][0], "ledger_seq": "0"}]}
    with pytest.raises(StoreError): port(tr).read_owner_intents(NS)


def test_false_ack_or_malformed_extra_metadata_cannot_be_success(journal):
    tr = Transport(journal)
    tr.mutate = lambda path, row: {**row, "durable": False} if path.endswith("append_owner_intent_v1") else row
    with pytest.raises(StoreError): port(tr).append_owner_intent(command())
    tr.mutate = None
    assert port(tr).append_owner_intent(command())["durable"] is True
    tr.mutate = lambda path, row: {**row, "secret_backend_metadata": "not-public"}
    with pytest.raises(StoreError): port(tr).read_owner_intents(NS)


@pytest.mark.parametrize("error,status", [(RuntimeError("SECRET"), 503), (StoreError(429), 429)])
def test_provider_errors_redacted_and_cancellation_not_swallowed(journal, error, status):
    def failing(*args, **kwargs): raise error
    with pytest.raises(StoreError) as caught: port(failing).read_owner_intents(NS)
    assert caught.value.status == status and "SECRET" not in str(caught.value)
    def cancel(*args, **kwargs): raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt): port(cancel).read_owner_intents(NS)


def test_conflict_and_invalid_options_do_not_become_success(journal):
    def conflict(*args, **kwargs): raise StoreConflict()
    with pytest.raises(JournalConflict): port(conflict).read_owner_intents(NS)
    for options in ({"after": "01"}, {"limit": 101}, {"watermark": "0", "after": "1"}):
        with pytest.raises(ValueError): port(Transport(journal)).read_owner_intents_page(NS, **options)
    with pytest.raises(ValueError): SupabaseOwnerIntentJournal("https://example.supabase.co", "key", authority_id=AUTHORITY, actor_ref=ACTOR, page_size=101)
