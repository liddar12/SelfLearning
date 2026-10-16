"""Default-off owner-intent routes, intended behind the existing OwnerBoundary.

The factory/authority/actor/namespace are trusted server configuration. This
module does not authenticate a bearer independently and MUST NOT be mounted
without the application boundary. No candidate activation or MCP mutation.
"""
from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from selflearn_core import owner_intents as dto
from selflearn_core.store.receipt_journal import JournalConflict
from selflearn_core.store.supabase_store import StoreError


PATH = "/api/v1/owner-intents"


class OwnerIntentReceiver:
    def __init__(self, journal, *, authority_id, actor_ref, namespace):
        self.authority_id, self.actor_ref = dto.digest(authority_id), dto.actor(actor_ref)
        self.namespace = dto.namespace(namespace)
        dto.need(journal.authority_id == self.authority_id and journal.actor_ref == self.actor_ref
                 and all(callable(getattr(journal, key, None)) for key in
                         ("append_owner_intent", "read_owner_intents")), "invalid_owner_intent_boundary")
        self.journal = journal

    def append(self, value):
        command = dto.validate_command(value, authority_id=self.authority_id)
        dto.need(dto.canonical(command["namespace"]) == dto.canonical(self.namespace), "owner_intent_scope_mismatch")
        ack = self.journal.append_owner_intent(command)
        # Both actual ports validate complete history; a receiver also rejects
        # a malformed injected port without exposing private provider metadata.
        try:
            dto.need(type(ack) is dict and set(ack) == dto.ACK_KEYS
                     and dto.canonical(ack["namespace"]) == dto.canonical(self.namespace)
                     and ack["authority_id"] == self.authority_id and ack["command_id"] == command["command_id"]
                     and ack["durable"] is True and all(ack[k] == v and type(ack[k]) is type(v) for k, v in dto.FLAGS.items()))
            dto.digest(ack["event_id"]); dto._clock(ack["recorded_utc"])
            dto.need(type(ack["version"]) is int and ack["version"] == 1
                     and type(ack["sequence"]) is int and 1 <= ack["sequence"] <= dto.MAX_EVENTS)
            return dto.snapshot(ack)
        except (ValueError, TypeError, KeyError, OverflowError):
            raise StoreError(502) from None

    def read(self):
        value = self.journal.read_owner_intents(self.namespace)
        try:
            expected = dto.complete_snapshot(value["events"], authority_id=self.authority_id,
                expected_namespace=self.namespace, watermark=value["watermark"], observed_utc=value["observed_utc"])
            dto.need(dto.canonical(value) == dto.canonical(expected), "invalid_owner_intent_snapshot")
            return expected
        except (ValueError, TypeError, KeyError, OverflowError):
            raise StoreError(502) from None


def register_owner_intent_routes(app, owner_intent_factory, body_reader):
    """Inject explicitly; no env lookup, storage fallback or automatic enable."""
    def run(operation):
        if owner_intent_factory is None:
            raise HTTPException(503, "Owner intents are disabled; serving is unchanged")
        try:
            with owner_intent_factory() as receiver:
                if not isinstance(receiver, OwnerIntentReceiver):
                    raise StoreError(503)
                return operation(receiver)
        except JournalConflict:
            raise HTTPException(409, "Owner head changed; review the complete current history") from None
        except StoreError as exc:
            raise HTTPException(429 if exc.status == 429 else 503, "Owner journal unavailable; no activation was acknowledged") from None
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            if str(exc) == "owner_intent_capacity_exceeded":
                raise HTTPException(429, "Owner journal capacity reached; no history was removed") from None
            raise HTTPException(422, "Invalid owner intent or configured scope") from None
        except Exception:
            raise HTTPException(503, "Owner journal unavailable; retry the exact command safely") from None

    @app.post(PATH)
    async def append(request: Request):
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise HTTPException(415, "Use application/json")
        value = await body_reader(request)
        return await run_in_threadpool(run, lambda receiver: receiver.append(value))

    @app.get(PATH)
    def read():
        return run(lambda receiver: receiver.read())
