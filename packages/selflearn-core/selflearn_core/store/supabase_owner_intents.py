"""Server-only owner-intent RPC port. No local fallback or serving writes.

The service credential/application are trusted: this port cannot prove which
HTTP operator bearer was checked. It validates complete stored history before
returning an append acknowledgement, including recovery of original retries.
"""
from .. import owner_intents as dto
from ..probability_receipts import instant
from .receipt_journal import JournalConflict
from .supabase_journal import SupabaseReceiptJournal
from .supabase_store import StoreConflict, StoreError


class SupabaseOwnerIntentJournal(SupabaseReceiptJournal):
    def __init__(self, url, key, *, authority_id, actor_ref, transport=None, page_size=100):
        self.authority_id, self.actor_ref = dto.digest(authority_id), dto.actor(actor_ref)
        dto.need(type(page_size) is int and 1 <= page_size <= 100)
        super().__init__(url, key, transport=transport, page_size=page_size)

    def _owner_rpc(self, name, values):
        try:
            response = self._request("POST", "/rest/v1/rpc/selflearn_journal_" + name + "_v1", data=values)
            return dto.snapshot(response)
        except StoreConflict:
            raise JournalConflict("owner_head_conflict") from None
        except StoreError as exc:
            # Even a typed exception from an injected provider is not a trusted
            # public message/status. Do not expose its text or chained traceback.
            try:
                status = exc.status
            except Exception:
                status = 503
            raise StoreError(status if type(status) is int and 400 <= status <= 599 else 503) from None
        except JournalConflict:
            raise JournalConflict("owner_head_conflict") from None
        except Exception:
            raise StoreError(503) from None

    def read_owner_intents_page(self, namespace, *, after=None, watermark=None, limit=None):
        namespace = dto.namespace(namespace)
        size = self.page_size if limit is None else limit
        dto.need(type(size) is int and 1 <= size <= 100)
        cursor = dto.sequence(after) if after is not None else 0
        if watermark is not None:
            dto.need(cursor <= dto.sequence(watermark), "invalid_owner_watermark")
        page = self._owner_rpc("read_owner_intents", {"p_authority_id": self.authority_id,
            "p_namespace_json": dto.canonical(namespace), "p_after": after,
            "p_limit": size, "p_watermark": watermark})
        try:
            dto.need(type(page) is dict and set(page) == dto.PAGE_KEYS)
            dto.need(type(page["version"]) is int and page["version"] == 1
                and page["authority_id"] == self.authority_id and dto.canonical(page["namespace"]) == dto.canonical(namespace))
            fence = dto.sequence(page["watermark"])
            dto.need(cursor <= fence and (watermark is None or watermark == page["watermark"]))
            dto.need(type(page["head_sequence"]) is int and 0 <= page["head_sequence"] <= dto.MAX_EVENTS)
            if page["head_sequence"]:
                dto.digest(page["head_event_id"])
            else:
                dto.need(page["head_event_id"] is None)
            observed = dto._clock(page["observed_utc"])
            dto.need(type(page["rows"]) is list and len(page["rows"]) <= size)
            previous = cursor
            for row in page["rows"]:
                dto.need(type(row) is dict and set(row) == {"event", "ledger_seq"})
                seq = dto.sequence(row["ledger_seq"])
                dto.need(previous < seq <= fence)
                dto.validate_event(row["event"], authority_id=self.authority_id, expected_namespace=namespace)
                dto.need(instant(row["event"]["recorded_utc"]) <= observed)
                previous = seq
            if page["next_after"] is not None:
                dto.need(bool(page["rows"]) and page["next_after"] == page["rows"][-1]["ledger_seq"])
            return page
        except (ValueError, TypeError, KeyError, OverflowError):
            raise StoreError(502) from None

    def read_owner_intents(self, namespace):
        namespace = dto.namespace(namespace)
        first = self.read_owner_intents_page(namespace)
        rows = list(first["rows"]); page = first
        while page["next_after"] is not None:
            if len(rows) >= dto.MAX_EVENTS:
                raise StoreError(502)
            page = self.read_owner_intents_page(namespace, after=page["next_after"], watermark=first["watermark"])
            if page["head_event_id"] != first["head_event_id"] or page["head_sequence"] != first["head_sequence"]:
                raise StoreError(502)
            rows.extend(page["rows"])
        try:
            events = [r["event"] for r in rows]
            dto.need(len(events) == first["head_sequence"] and (events[-1]["event_id"] if events else None) == first["head_event_id"])
            dto.need(len({e["command"]["command_id"] for e in events}) == len(events))
            return dto.complete_snapshot(events, authority_id=self.authority_id, expected_namespace=namespace,
                watermark=first["watermark"], observed_utc=first["observed_utc"])
        except (ValueError, TypeError, KeyError, OverflowError):
            raise StoreError(502) from None

    def append_owner_intent(self, command):
        command = dto.validate_command(command, authority_id=self.authority_id)
        result = self._owner_rpc("append_owner_intent", {"p_command_json": dto.canonical(command),
            "p_authority_id": self.authority_id, "p_actor_ref": self.actor_ref})
        # A plausible durable:true is not an ACK: require exact complete stored
        # readback, including an original old result after the head advances.
        stored = self.read_owner_intents(command["namespace"])
        expected = [dto.acknowledgement(e) for e in stored["events"]
                    if dto.canonical(e["command"]) == dto.canonical(command)]
        if len(expected) != 1 or dto.canonical(result) != dto.canonical(expected[0]):
            raise StoreError(502)
        return result
