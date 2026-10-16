"""Explicit server-only job RPC port; no startup IO, env switch or fallback.

The configured service credential and transport are trusted. Content hashes do
not authenticate an operator, clock or namespace. Complete event histories are
independently reexecuted before any result is returned to the caller.
"""
from .. import learning_jobs as dto
from .supabase_journal import SupabaseReceiptJournal
from .supabase_store import StoreConflict, StoreError


class SupabaseLearningJobJournal(SupabaseReceiptJournal):
    def __init__(self, url, key, *, authority_id, transport=None):
        self.authority_id = dto.digest(authority_id)
        super().__init__(url,key,transport=transport)

    def _job_rpc(self, action, values):
        try:
            return dto.snapshot(self._request("POST", "/rest/v1/rpc/selflearn_jobs_" + action + "_v1", data=values), limit=dto.MAX_HISTORY_BYTES)
        except StoreConflict:
            raise dto.JobConflict("learning_job_conflict") from None
        except StoreError as error:
            try:status = error.status
            except Exception:status = 503
            raise StoreError(status if type(status) is int and 400 <= status <= 599 else 503) from None
        except dto.JobConflict:
            raise dto.JobConflict("learning_job_conflict") from None
        except Exception:
            raise StoreError(503) from None

    def _checked(self, value, job_id):
        try:
            expected = dto.complete_snapshot(value["definition"],value["events"], authority_id=self.authority_id,
                registered_utc=value["registered_utc"],observed_utc=value["observed_utc"])
            dto.need(expected["definition"]["job_id"] == job_id and dto.canonical(value) == dto.canonical(expected))
            return expected
        except Exception:
            raise StoreError(502) from None

    def register_job(self, definition):
        definition = dto.validate_spec(definition, authority_id=self.authority_id)
        result = self._checked(self._job_rpc("register", {"p_authority_id": self.authority_id,
            "p_definition_json": dto.canonical(definition)}), definition["job_id"])
        if dto.canonical(result["definition"]) != dto.canonical(definition):raise StoreError(502)
        return result

    def read_job(self, job_id):
        return self._checked(self._job_rpc("read", {"p_authority_id": self.authority_id,
            "p_job_id": dto.digest(job_id)}), job_id)

    def append_job_command(self, command):
        command = dto.validate_command(command, authority_id=self.authority_id)
        actual = self._job_rpc("append", {"p_authority_id": self.authority_id, "p_command_json": dto.canonical(command)})
        current = self.read_job(command["job_id"])
        matches = [dto.acknowledgement(e) for e in current["events"] if dto.canonical(e["command"]) == dto.canonical(command)]
        if len(matches) != 1 or dto.canonical(matches[0]) != dto.canonical(actual):raise StoreError(502)
        return actual
