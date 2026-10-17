"""Real verified preparation, not SQL execution or durable ACK evidence."""
import copy
import json
import unittest
from unittest.mock import patch

from selflearn_core.adapters import nfl_capture_application_v2 as V
from selflearn_core.probability_receipts import fingerprint
from test_nfl_capture_application import population
from test_nfl_capture_delivery_job import PROCESSING, request as v1_request
import test_nfl_capture_evidence as E


def context(*, sizes=(4, 4), missing_first=False, **kwargs):
    journal, (envelope, objects) = population(sizes=sizes, missing_first=missing_first)
    request = V.make_request(v1_request(envelope, objects))
    bound = V.bind_context(journal, request, lambda oid: E.stream(objects[oid]), clock=lambda: PROCESSING, **kwargs)
    return bound, journal, envelope, objects


class SealedApplicationPreparationTests(unittest.TestCase):
    def test_exact_request_identity_scope_and_invalid_shapes(self):
        ctx, journal, envelope, objects = context()
        request = ctx["request"]
        identity = {"version": 2, "kind": "nfl.capture_application.v2",
                    "capture_intent_id": request["capture_request"]["delivery_id"], "execution_policy": V.EXECUTION_POLICY}
        self.assertEqual(request["application_id"], fingerprint(identity))
        self.assertEqual(V.validate_request(request), request)
        for key, value in (("version", True), ("kind", "other"), ("execution_policy", "reapply"),
                           ("application_id", fingerprint("forged")), ("extra", True), ("capture_request", [])):
            with self.subTest(key=key), self.assertRaises(ValueError):
                V.validate_request({**copy.deepcopy(request), key: value})
        for invalid in (None, [], {}):
            with self.assertRaises(ValueError): V.validate_request(invalid)
        self.assertEqual(journal.delivery_writes, [])

    def test_semantic_vector_rejects_metadata_restamp_and_equivalent_clock_spelling(self):
        ctx, _, envelope, objects = context()
        original = V.source_vector_id(envelope)
        metadata = copy.deepcopy(envelope)
        for source in metadata["observations"].values(): source["http"]["etag"] = "different transport decoration"
        E.reseal(metadata)
        self.assertNotEqual(V.make_request(v1_request(metadata, objects))["application_id"], ctx["request"]["application_id"])
        self.assertEqual(V.source_vector_id(metadata), original)
        clocks = copy.deepcopy(envelope)
        for source in clocks["observations"].values(): source["observed_utc"] = source["observed_utc"].replace("Z", ".000000+00:00")
        E.reseal(clocks)
        self.assertEqual(V.source_vector_id(clocks), original)
        advanced = copy.deepcopy(envelope)
        for source in advanced["observations"].values():
            source["observed_utc"] = source["observed_utc"].replace("Z", ".000001Z")
        E.reseal(advanced)
        self.assertNotEqual(V.source_vector_id(advanced), original)
        broken = copy.deepcopy(envelope)
        broken["manifest"]["sources"][0] = copy.deepcopy(broken["manifest"]["sources"][1])
        E.reseal(broken)
        with self.assertRaises(ValueError): V.source_vector_id(broken)

    def test_complete_group_id_order_seal_hash_and_real_rpc_wire(self):
        ctx, journal, _, _ = context(sizes=(4, 4, 2))
        frozen = copy.deepcopy(ctx)
        seal = V.prepare_seal(ctx, max_batch_members=6)
        self.assertEqual(ctx, frozen)
        self.assertEqual(seal["seal_id"], fingerprint({k: v for k, v in seal.items() if k != "seal_id"}))
        self.assertEqual(seal["membership_id"], V.membership_id(ctx["staged"]["groups"]))
        self.assertEqual([g["group_id"] for g in seal["groups"]], sorted(g["group_id"] for g in seal["groups"]))
        self.assertEqual(sum(b["member_count"] for b in seal["batches"]), 10)
        self.assertEqual(len(seal["batches"]), 2)
        original = {g["group_id"]: g for g in ctx["staged"]["groups"]}
        assigned = []
        for batch in seal["batches"]:
            self.assertEqual(batch["step_id"], V.step_id(seal["application_id"], batch["ordinal"]))
            results, obs, rev = [], [], []
            for gid in batch["group_ids"]:
                result, new_obs, new_rev = V.group_proofs(ctx, original[gid])
                results.append(result); obs.extend(new_obs); rev.extend(new_rev); assigned.append(gid)
            data, conservative = V.batch_wire(seal["application_id"], batch["ordinal"], results, obs, rev, "9223372036854775807")
            self.assertGreaterEqual(conservative, len(json.dumps(data, allow_nan=False).encode()))
            self.assertEqual(V.batch_wire(seal["application_id"], batch["ordinal"], results, obs, rev,
                                         "9223372036854775807", maximum=conservative)[1], conservative)
            with self.assertRaises(ValueError):
                V.batch_wire(seal["application_id"], batch["ordinal"], results, obs, rev,
                             "9223372036854775807", maximum=conservative - 1)
        self.assertEqual(sorted(assigned), [g["group_id"] for g in seal["groups"]])
        for group in seal["groups"]:
            self.assertEqual(group["status"], "planned")
            self.assertEqual(group["fact_id"], fingerprint(group["fact"]))
            self.assertEqual(group["fact"]["actual"], 80.0)
            self.assertIs(type(group["fact"]["actual"]), float)
            self.assertNotIn("hit", group["fact"])
            self.assertNotIn("canonical_contract", group["fact"])
        self.assertEqual(journal.delivery_writes, [])

    def test_oversized_whole_group_and_unknown_stat_hold_preserve_independent_groups(self):
        ctx, _, _, _ = context(sizes=(501, 2))
        seal = V.prepare_seal(ctx)
        held = [g for g in seal["groups"] if g["status"] == "withheld"]
        self.assertEqual(len(held), 1)
        self.assertEqual(len(held[0]["receipt_ids"]), 501)
        self.assertEqual(held[0]["reason_codes"], ["complete_group_exceeds_member_limit"])
        self.assertIsNone(held[0]["fact"])
        self.assertIsNone(held[0]["fact_id"])
        self.assertIsNone(held[0]["batch_ordinal"])
        self.assertEqual(sum(b["member_count"] for b in seal["batches"]), 2)
        ctx, _, _, _ = context(missing_first=True)
        seal = V.prepare_seal(ctx)
        self.assertEqual([len(g["receipt_ids"]) for g in seal["groups"] if g["status"] == "withheld"], [4])
        self.assertEqual(sum(b["member_count"] for b in seal["batches"]), 4)

    def test_all_proof_count_limits_and_actual_byte_capacity_are_enforced(self):
        ctx, _, _, _ = context(sizes=(4, 2))
        for key in ("max_batch_observations", "max_batch_revisions"):
            seal = V.prepare_seal(ctx, **{key: 2})
            self.assertEqual(sum(b["member_count"] for b in seal["batches"]), 2)
            self.assertIn("complete_group_exceeds_proof_limit", next(g for g in seal["groups"] if g["status"] == "withheld")["reason_codes"])
        seal = V.prepare_seal(ctx, max_batch_bytes=100)
        self.assertEqual(seal["batches"], [])
        self.assertTrue(all(g["reason_codes"] == ["complete_group_exceeds_byte_limit"] for g in seal["groups"]))
        for kwargs in ({"max_batch_members": 0}, {"max_batch_bytes": True}, {"max_batch_observations": 501}):
            with self.assertRaises(ValueError): V.prepare_seal(ctx, **kwargs)

    def test_kernel_gap_or_common_fact_conflict_withholds_whole_group(self):
        ctx, _, _, _ = context(sizes=(4, 2))
        gid = ctx["staged"]["groups"][0]["group_id"]
        missing = copy.deepcopy(ctx)
        missing["observations"][gid] = missing["observations"][gid][1:]
        seal = V.prepare_seal(missing)
        self.assertIn("kernel_group_incomplete", next(g for g in seal["groups"] if g["group_id"] == gid)["reason_codes"])
        conflict = copy.deepcopy(ctx)
        conflict["observations"][gid][0]["measurement"]["actual"] = 79.0
        seal = V.prepare_seal(conflict)
        self.assertIn("conflicting_underlying_stat_observations", next(g for g in seal["groups"] if g["group_id"] == gid)["reason_codes"])

    def test_raw_tamper_and_invalid_boundary_or_authority_fail_before_writes(self):
        _, journal, envelope, objects = context()
        request = V.make_request(v1_request(envelope, objects))
        bad = {key: raw + b"x" for key, raw in objects.items()}
        with self.assertRaises(ValueError):
            V.bind_context(journal, request, lambda oid: E.stream(bad[oid]), clock=lambda: PROCESSING)
        for kwargs in ({"clock": None}, {"raw_chunks": None}, {"max_total_bytes": True}, {"max_total_bytes": -1}):
            arguments = {"raw_chunks": lambda oid: E.stream(objects[oid]), "clock": lambda: PROCESSING, **kwargs}
            with self.assertRaises(ValueError): V.bind_context(journal, request, **arguments)
        for change in ({"version": True}, {"observations": None}, {"watermark": "9999999999999999999"}):
            with patch.object(journal, "read_observed_snapshot", return_value={**journal.snapshot, **change}):
                with self.assertRaises(ValueError):
                    V.bind_context(journal, request, lambda oid: E.stream(objects[oid]), clock=lambda: PROCESSING)
        self.assertEqual(journal.delivery_writes, [])

    def test_kernel_unlocatable_or_duplicate_proof_fails_globally(self):
        _, journal, envelope, objects = context()
        request = V.make_request(v1_request(envelope, objects))
        original = V.resolve_observed
        for key, wrong in (("observation_proposals", None), ("proposals", []), ("proposals", {"resolution": []}),
                           ("observation_proposals", {"receipt_id": fingerprint("foreign")})):
            def poisoned(*args, **kwargs):
                value = original(*args, **kwargs)
                value[key].append(wrong)
                return value
            with self.subTest(key=key, wrong=wrong), patch.object(V, "resolve_observed", side_effect=poisoned):
                with self.assertRaises(ValueError):
                    V.bind_context(journal, request, lambda oid: E.stream(objects[oid]), clock=lambda: PROCESSING)
        def duplicate(*args, **kwargs):
            value = original(*args, **kwargs)
            value["observation_proposals"].append(copy.deepcopy(value["observation_proposals"][0]))
            return value
        with patch.object(V, "resolve_observed", side_effect=duplicate), self.assertRaises(ValueError):
            V.bind_context(journal, request, lambda oid: E.stream(objects[oid]), clock=lambda: PROCESSING)

    def test_step_and_sequence_types_are_not_coerced(self):
        for ordinal in (True, -1, 2000, "0"):
            with self.assertRaises(ValueError): V.step_id(fingerprint("app"), ordinal)
        for value in (True, 1, "01", "-1", "9223372036854775808"):
            with self.assertRaises(ValueError): V.sequence(value)
        self.assertEqual(V.sequence("9223372036854775807"), 9223372036854775807)


if __name__ == "__main__": unittest.main()
