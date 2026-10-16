"""Opt-in v2 journal port: pinned complete reports and immutable original ACKs.

No migration, backend selection, retries, private upload, raw parsing, scheduler
or activation. Configuration supplies the transport; callers never supply a
membership subset. All v1 inherited functions retain their original contracts.
"""
from __future__ import annotations

import copy
import json
import math
import time

from . import nfl_capture_application_v2 as V
from . import nfl_capture_application_contracts as C
from . import nfl_capture_delivery_journal as D
from ..probability_receipts import fingerprint
from ..store.receipt_journal import JournalConflict
from ..store.supabase_store import StoreConflict, StoreError


class CaptureApplicationBudgetExceeded(TimeoutError):
    """Leave active work pending; never terminalize or fabricate a result."""


class NFLCaptureApplicationJournal(D.NFLCaptureDeliveryJournal):
    def __init__(self, *args, max_run_seconds=None, monotonic=time.monotonic, **kwargs):
        if (not callable(monotonic) or (max_run_seconds is not None
                and (type(max_run_seconds) not in (int, float) or not math.isfinite(max_run_seconds)
                     or not 0 < max_run_seconds <= 45))):
            raise ValueError("invalid_capture_application_operation_budget")
        super().__init__(*args, **kwargs)
        if max_run_seconds is not None:
            # Fixed per-instance/per-operation deadline. Never mutate another
            # worker's live port to install a temporary request wrapper.
            end = monotonic() + max_run_seconds
            request = self._request
            def bounded_request(*request_args, **request_kwargs):
                if monotonic() >= end:
                    raise CaptureApplicationBudgetExceeded("capture_application_pending_operation_budget")
                return request(*request_args, **request_kwargs)
            self._request = bounded_request

    def _application_rpc(self, name, data):
        # This is separate from _observed_rpc, whose strict version=1
        # response checks remain correct for every inherited v1 operation.
        if name not in {"selflearn_journal_register_capture_application_v2",
                        "selflearn_journal_commit_capture_application_batch_v2",
                        "selflearn_journal_stop_capture_application_v2",
                        "selflearn_journal_prepare_capture_membership_v2",
                        "selflearn_journal_read_capture_application_v2"}:
            raise ValueError("invalid_capture_application_rpc")
        try:
            response = self._request("POST", "/rest/v1/rpc/" + name, data=data)
        except CaptureApplicationBudgetExceeded:
            raise
        except StoreConflict:
            raise JournalConflict("capture_application_conflict_reread_required") from None
        except StoreError as exc:
            raise StoreError(exc.status if type(exc.status) is int and 400 <= exc.status <= 599 else 503) from None
        except Exception:
            raise StoreError(503) from None
        try:
            if type(response) is not dict or type(response.get("version")) is not int or response["version"] != 2:
                raise ValueError("invalid_capture_application_rpc_response")
            # Bound the actual assembled page/ACK even with a fake/custom
            # transport; no relaxed limits based on claimed record counts.
            return json.loads(V.bounded(response, V.MAX_REQUEST_BYTES))
        except (ValueError, TypeError, KeyError, OverflowError):
            raise StoreError(502) from None

    def _application_rows(self, application_id, view, *, key=None, ledger_watermark=None):
        D._hash(application_id)
        if type(view) is not str or view not in C.VIEWS or (view == "batch") != (key is not None):
            raise ValueError("invalid_capture_application_read_view")
        if key is not None:
            D._hash(key)
        if ledger_watermark is not None:
            V.sequence(ledger_watermark)
        many = view in {"groups", "batches"}
        limit = min(self.page_size, 100) if many else 1
        after, rows, metadata, fence = None, [], None, ledger_watermark
        for _ in range(V.MAX_GROUPS + 1):
            page = self._application_rpc("selflearn_journal_read_capture_application_v2", {
                "p_application_id": application_id, "p_view": view, "p_key": key,
                "p_after": after, "p_limit": limit, "p_ledger_watermark": fence})
            try:
                C.keys(page, C.PAGE_KEYS)
                if (page["kind"] != "nfl.capture_application_page.v2" or type(page["found"]) is not bool
                        or page["application_id"] != application_id or page["view"] != view
                        or type(page["records"]) is not list or len(page["records"]) > limit):
                    raise ValueError("invalid_capture_application_page")
                C.integer(page["total_records"], 0, V.MAX_GROUPS if many else 1)
                if not page["found"]:
                    if (after is not None or rows or page["records"] or page["total_records"]
                            or any(page[k] is not None for k in ("next_after", "registration_id", "seal_id", "ledger_watermark"))):
                        raise ValueError("invalid_missing_capture_application_page")
                    return None
                D._hash(page["registration_id"]); D._hash(page["seal_id"])
                V.sequence(page["ledger_watermark"])
                if fence is not None and fence != page["ledger_watermark"]:
                    raise ValueError("capture_application_page_fence_changed")
                fence = page["ledger_watermark"]
                current_metadata = (fence, page["registration_id"], page["seal_id"], page["total_records"])
                if metadata is not None and metadata != current_metadata:
                    raise ValueError("capture_application_page_metadata_changed")
                metadata = current_metadata
                if many:
                    previous = None if after is None else (after if view == "groups" else int(after))
                    last = None
                    for row in page["records"]:
                        if view == "groups":
                            C.keys(row, {"sealed_group", "execution_state", "result"})
                            last = D._hash(row["sealed_group"]["group_id"])
                        else:
                            C.keys(row, {"assignment", "result"})
                            last = C.integer(row["assignment"]["ordinal"], 0, V.MAX_GROUPS - 1)
                        if previous is not None and last <= previous:
                            raise ValueError("capture_application_page_order_changed")
                        previous = last
                    cursor = last if view == "groups" else None if last is None else str(last)
                else:
                    cursor = None
                    if after is not None or page["next_after"] is not None:
                        raise ValueError("invalid_single_capture_application_page")
                rows.extend(page["records"])
                if len(rows) > page["total_records"]:
                    raise ValueError("capture_application_page_count_changed")
                if page["next_after"] is None:
                    if len(rows) != page["total_records"]:
                        raise ValueError("incomplete_capture_application_page")
                    return {"records": rows, "ledger_watermark": fence,
                            "registration_id": page["registration_id"], "seal_id": page["seal_id"]}
                if (not many or not page["records"] or page["next_after"] != cursor
                        or page["next_after"] == after or len(rows) == page["total_records"]):
                    raise ValueError("capture_application_page_cursor_changed")
                after = page["next_after"]
            except (ValueError, TypeError, KeyError, OverflowError):
                raise StoreError(502) from None
        raise StoreError(502)

    def get_capture_application(self, application_id, *, ledger_watermark=None):
        """Read the COMPLETE sealed application at one immutable ledger fence."""
        header = self._application_rows(application_id, "registration", ledger_watermark=ledger_watermark)
        if header is None:
            return None
        try:
            if len(header["records"]) != 1:
                raise ValueError("missing_capture_application_registration")
            record = header["records"][0]
            C.keys(record, {"request", "seal_header", "registration"})
            C.keys(record["seal_header"], C.SEAL_KEYS - {"groups", "batches"})
            documents = {}
            for view in ("groups", "batches", "status", "terminal"):
                page = self._application_rows(application_id, view, ledger_watermark=header["ledger_watermark"])
                if page is None or any(page[k] != header[k] for k in ("ledger_watermark", "registration_id", "seal_id")):
                    raise ValueError("capture_application_cross_view_fence_changed")
                documents[view] = page["records"]
            groups, batches = documents["groups"], documents["batches"]
            seal = {**record["seal_header"], "groups": [row["sealed_group"] for row in groups],
                    "batches": [row["assignment"] for row in batches]}
            C.validate_seal(seal, record["request"])
            if seal["application_id"] != application_id or seal["seal_id"] != header["seal_id"]:
                raise ValueError("capture_application_seal_page_identity_mismatch")
            registration = C.validate_registration(record["registration"], seal)
            if registration["registration_id"] != header["registration_id"]:
                raise ValueError("capture_application_registration_page_identity_mismatch")
            if len(documents["status"]) != 1 or len(documents["terminal"]) > 1:
                raise ValueError("invalid_capture_application_status_partition")
            status = documents["status"][0]
            terminal = documents["terminal"][0] if documents["terminal"] else None
            C.validate_report(seal, groups, batches, terminal_reasons=None if terminal is None else terminal["reason_codes"])
            if terminal is not None:
                C.validate_terminal(terminal, seal, groups, batches)
            self._application_status(status, seal, registration, batches, terminal, header["ledger_watermark"])
            return {"version": 2, "request": record["request"], "seal": seal, "registration": registration,
                    "groups": groups, "batches": batches, "status": status, "terminal": terminal,
                    "ledger_watermark": header["ledger_watermark"]}
        except (ValueError, TypeError, KeyError, OverflowError):
            raise StoreError(502) from None

    @staticmethod
    def _application_status(status, seal, registration, batches, terminal, fence):
        C.keys(status, C.STATUS_KEYS)
        results = [row["result"] for row in batches if row["result"] is not None]
        committed = sum(row["status"] == "committed" for row in results)
        pending = next((row["assignment"]["step_id"] for row in batches if row["result"] is None), None)
        if (type(status["version"]) is not int or status["version"] != 2 or status["kind"] != "nfl.capture_application_status.v2"
                or status["application_id"] != seal["application_id"] or status["registration_id"] != registration["registration_id"]
                or status["seal_id"] != seal["seal_id"] or status["membership_id"] != seal["membership_id"]
                or status["ledger_watermark"] != fence or status["scope"] != "pinned_application_progress"
                or status["durable_ack"] is not False or status["source_authentication"] != "not_performed"
                or status["status"] != ("active" if terminal is None else "terminal")
                or status["execution_ready"] is not (terminal is None)
                or status["next_step_id"] != (pending if terminal is None else None)
                or status["terminal_result_id"] != (None if terminal is None else terminal["terminal_result_id"])):
            raise ValueError("capture_application_status_mismatch")
        C.integer(status["batch_count"], 0, V.MAX_GROUPS); C.integer(status["committed_batches"], 0, V.MAX_GROUPS)
        if (status["batch_count"] != len(batches) or status["committed_batches"] != committed
                or (terminal is None and (pending is None or committed == len(batches)))):
            raise ValueError("capture_application_status_count_mismatch")
        registered_seq = V.sequence(registration["application_seq"])
        if (registered_seq > V.sequence(fence)
                or any(not registered_seq < V.sequence(row["application_seq"]) <= V.sequence(fence) for row in results)
                or (terminal is not None and not registered_seq < V.sequence(terminal["application_seq"]) <= V.sequence(fence))):
            raise ValueError("capture_application_status_sequence_mismatch")

    def get_capture_application_batch(self, application_id, step_id):
        D._hash(step_id)
        application = self.get_capture_application(application_id)
        if application is None:
            return None
        for row in application["batches"]:
            if row["assignment"]["step_id"] == step_id:
                return row["result"]
        raise ValueError("capture_application_step_not_in_seal")

    def prepare_capture_membership(self, season, weeks):
        """Warm a DB-derived authority cache; never a forecast/outcome ACK.

        Only SQL can construct its complete membership and source index. The
        returned metadata is a performance hint, not a replacement for the
        subsequent complete snapshot, raw bind, membership check or CAS.
        """
        C.integer(season, 2000, 2100)
        if (type(weeks) is not list or not 1 <= len(weeks) <= 18
                or any(type(week) is not int or not 1 <= week <= 18 for week in weeks)
                or weeks != sorted(set(weeks))):
            raise ValueError("invalid_capture_application_cache_period")
        result = self._application_rpc("selflearn_journal_prepare_capture_membership_v2",
                                      {"p_season": season, "p_weeks_json": V.bounded(weeks, 1024)})
        try:
            C.keys(result, {"version", "kind", "scope", "durable_ack", "learning_accepted", "season", "weeks",
                            "authority_watermark", "policy_id", "cache_id", "membership_id", "member_count", "group_count"})
            if (result["kind"] != "nfl.capture_membership_cache.v2" or result["scope"] != "derived_membership_cache_only"
                    or result["durable_ack"] is not False or result["learning_accepted"] is not False
                    or type(result["season"]) is not int or result["season"] != season
                    or V.bounded(result["weeks"], 1024) != V.bounded(weeks, 1024)):
                raise ValueError("invalid_capture_application_cache_ack")
            V.sequence(result["authority_watermark"])
            for key in ("policy_id", "cache_id", "membership_id"):
                D._hash(result[key])
            C.integer(result["member_count"], 0, V.MAX_MEMBERS)
            C.integer(result["group_count"], 0, min(V.MAX_GROUPS, result["member_count"]))
            if bool(result["member_count"]) != bool(result["group_count"]):
                raise ValueError("capture_application_cache_count_mismatch")
            return result
        except (ValueError, TypeError, KeyError, OverflowError):
            raise StoreError(502) from None

    def register_capture_application(self, request, seal, *, watermark):
        request = V.validate_request(request)
        C.validate_seal(seal, request); V.sequence(watermark)
        if watermark != seal["source_watermark"]:
            raise ValueError("capture_application_registration_fence_mismatch")
        data = {"p_request_json": V.bounded(request, V.MAX_REQUEST_BYTES), "p_seal_json": V.bounded(seal, V.MAX_SEAL_BYTES),
                "p_watermark": watermark}
        V.capacity._encode(data, V.MAX_RPC_BYTES, outer=True)
        result = self._application_rpc("selflearn_journal_register_capture_application_v2", data)
        try:
            return C.validate_registration(result, seal)
        except (ValueError, TypeError, KeyError, OverflowError):
            # A concurrent exact registration can legitimately own an older
            # seal. Recover independently, never relax a new ACK's structure.
            old = self.get_capture_application(request["application_id"])
            if (old is not None and fingerprint(old["request"]) == fingerprint(request)
                    and fingerprint(old["registration"]) == fingerprint(result)):
                return old["registration"]
            raise StoreError(502) from None

    def commit_capture_application_batch(self, application_id, step_id, group_results, observations, proposals, *, watermark):
        D._hash(application_id); D._hash(step_id); V.sequence(watermark)
        original = self.get_capture_application(application_id)
        if original is None:
            raise ValueError("capture_application_not_registered")
        assignment = next((row for row in original["batches"] if row["assignment"]["step_id"] == step_id), None)
        if assignment is None:
            raise ValueError("capture_application_step_not_in_seal")
        if assignment["result"] is not None:
            return assignment["result"]
        if (type(group_results) is not list or type(observations) is not list or type(proposals) is not list
                or len(observations) > V.MAX_BATCH_ITEMS or len(proposals) > V.MAX_BATCH_ITEMS):
            raise ValueError("capture_application_batch_work_limit")
        seal = original["seal"]
        data, _size = V.batch_wire(application_id, assignment["assignment"]["ordinal"], group_results, observations, proposals,
                                    watermark, maximum=seal["limits"]["rpc_bytes"])
        snapshot = self._observed_snapshot
        if snapshot is None or snapshot.get("watermark") != watermark:
            snapshot = self._load_observed_snapshot(watermark)
        try:
            result = self._application_rpc("selflearn_journal_commit_capture_application_batch_v2", data)
        finally:
            self._observed_snapshot = None
        try:
            C.validate_batch(result, seal)
            # Validate the returned ORIGINAL proof IDs at its own pinned input
            # fence, even when it belongs to a concurrent earlier attempt.
            # A stored matching blob cannot excuse false new-ID counts.
            basis = snapshot if snapshot["watermark"] == result["input_watermark"] else self._load_observed_snapshot(result["input_watermark"])
            expected_obs = len({oid for group in result["groups"] for oid in group["observation_ids"]}
                               - {row["observation_id"] for row in basis["observations"]})
            expected_rev = len({rid for group in result["groups"] for rid in group["revision_ids"]}
                               - {row["revision_id"] for row in basis["revisions"]})
            if result["added_observations"] != expected_obs or result["added_revisions"] != expected_rev:
                raise ValueError("capture_application_original_new_id_count_mismatch")
            if (result["status"] != "committed" or result["input_watermark"] != watermark
                    or fingerprint(result["groups"]) != fingerprint(group_results)):
                stored = self.get_capture_application_batch(application_id, step_id)
                if stored is None or fingerprint(stored) != fingerprint(result):
                    raise ValueError("unverified_original_capture_application_batch")
            return result
        except (ValueError, TypeError, KeyError, OverflowError):
            raise StoreError(502) from None

    def stop_capture_application(self, application_id, reason, *, watermark):
        D._hash(application_id); V.sequence(watermark)
        if type(reason) is not str or reason not in {"membership_changed", "source_integrity_failed", "runtime_capacity_exceeded", "complete"}:
            raise ValueError("invalid_capture_application_stop_reason")
        result = self._application_rpc("selflearn_journal_stop_capture_application_v2", {
            "p_application_id": application_id, "p_reason": reason, "p_watermark": watermark})
        # Whole-report validation is mandatory; terminal bytes are not trusted
        # merely because the returned digest is self-consistent.
        stored = self.get_capture_application(application_id)
        if stored is None or stored["terminal"] is None or fingerprint(stored["terminal"]) != fingerprint(result):
            raise StoreError(502)
        return stored["terminal"]
