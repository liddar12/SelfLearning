"""Real raw preparation; ACKs below are EXPLICIT in-memory contract fixtures.

No SQL, database-owned clock/sequence, durability, hosted service or complete
application execution is established by constructing and validating a fixture.
"""
import copy
import unittest

from selflearn_core.adapters import nfl_capture_application_v2 as V
from selflearn_core.adapters import nfl_capture_application_contracts as C
from selflearn_core.probability_receipts import fingerprint
from test_nfl_capture_application_v2 import context
from test_nfl_capture_delivery_job import PROCESSING
from test_nfl_capture_delivery_job import request as capture_request
import test_nfl_capture_evidence as E


def sealed(*, sizes=(4, 4), missing_first=False, **kwargs):
    ctx, _, _, _ = context(sizes=sizes, missing_first=missing_first)
    return ctx, V.prepare_seal(ctx, **kwargs)


def rehash(value, key):
    value[key] = fingerprint({k: v for k, v in value.items() if k != key})
    return value


def seal_hash(value):
    return rehash(value, "seal_id")


def registration(seal):
    value = {"version": 2, "kind": "nfl.capture_application_registration.v2",
        **{key: seal[key] for key in C.IDENTITIES}, "status": "registered",
        "group_count": len(seal["groups"]), "member_count": sum(len(g["receipt_ids"]) for g in seal["groups"]),
        "batch_count": len(seal["batches"]), "next_step_id": seal["batches"][0]["step_id"] if seal["batches"] else None,
        "source_watermark": seal["source_watermark"], "application_seq": "1", "recorded_utc": PROCESSING,
        "scope": "sealed_application_only", "durable": True, "learning_accepted": False,
        "source_authentication": "not_performed"}
    return rehash(value, "registration_id")


def withheld(group, reasons=None):
    return {**{key: copy.deepcopy(group[key]) for key in ("group_id", *V.MEMBERSHIP_KEYS, "membership_id")},
        "status": "withheld", "reason_codes": copy.deepcopy(group["reason_codes"] if reasons is None else reasons),
        "observation_ids": [], "revision_ids": []}


def batch_result(ctx, seal, ordinal=0, *, not_applied=False, replay=False, input_watermark=None):
    assignment = seal["batches"][ordinal]
    raw = {row["group_id"]: row for row in ctx["staged"]["groups"]}
    groups, observations, revisions = [], [], []
    for gid in assignment["group_ids"]:
        result, obs, rev = V.group_proofs(ctx, raw[gid])
        if not_applied:
            result = withheld(result, ["application_membership_changed"])
        else:
            observations.extend(obs); revisions.extend(rev)
        groups.append(result)
    added_obs = 0 if replay else len(observations)
    added_rev = 0 if replay else len(revisions)
    low = seal["source_watermark"] if input_watermark is None else input_watermark
    value = {"version": 2, "kind": "nfl.capture_application_batch_result.v2",
        "application_id": seal["application_id"], "step_id": assignment["step_id"], "ordinal": ordinal,
        "seal_id": seal["seal_id"], "membership_id": seal["membership_id"],
        "status": "not_applied" if not_applied else "committed", "groups": groups,
        "added_observations": added_obs, "added_revisions": added_rev,
        "input_watermark": low,
        "watermark": str(int(low) + added_obs + added_rev),
        "application_seq": str(ordinal+2), "recorded_utc": PROCESSING,
        "durable": True, "scope": "observations_and_batch_results", "source_authentication": "not_performed"}
    return rehash(value, "batch_result_id")


def report(ctx, seal, *, incomplete=None):
    """Fake complete paged report, populated with REAL current kernel proof IDs."""
    batches, low = [], seal["source_watermark"]
    for ordinal, assignment in enumerate(seal["batches"]):
        result = batch_result(ctx, seal, ordinal, not_applied=incomplete == "membership",input_watermark=low)
        batches.append({"assignment":copy.deepcopy(assignment),"result":result})
        low=result["watermark"]
    results = {group["group_id"]: group for batch in batches for group in batch["result"]["groups"]}
    groups = []
    for group in seal["groups"]:
        if group["status"] == "withheld":
            state, result = "initial_withheld", withheld(group)
        else:
            state = "not_applied" if incomplete else "committed"
            result = results[group["group_id"]]
        groups.append({"sealed_group": copy.deepcopy(group), "execution_state": state, "result": copy.deepcopy(result)})
    return groups, batches


def terminal(seal, groups, batches, *, status=None, reasons=None, current=None):
    counts = {key: 0 for key in C.COUNT_KEYS}
    counts.update(groups=len(groups), members=sum(len(g["sealed_group"]["receipt_ids"]) for g in groups),
        batches=len(batches), committed_batches=sum(b["result"] is not None and b["result"]["status"] == "committed" for b in batches))
    for row in groups:
        prefix = "accepted" if row["result"] and row["result"]["status"] == "accepted" else "withheld"
        counts[prefix+"_groups"] += 1; counts[prefix+"_members"] += len(row["sealed_group"]["receipt_ids"])
        if row["execution_state"] == "not_applied":
            counts["not_applied_groups"] += 1; counts["not_applied_members"] += len(row["sealed_group"]["receipt_ids"])
    if status is None:
        status = "no_supported_membership" if not groups else "accepted" if counts["accepted_groups"] == len(groups) else "partial" if counts["accepted_groups"] else "withheld"
    complete = status in {"accepted", "partial", "withheld", "no_supported_membership"}
    value = {"version": 2, "kind": "nfl.capture_application_result.v2", **{key: seal[key] for key in C.IDENTITIES},
        "status": status, "reason_codes": [] if reasons is None else reasons,
        "current_membership_id": seal["membership_id"] if current is None else current,
        "group_report_id": fingerprint(groups), "counts": counts,
        "registered_watermark": seal["source_watermark"],
        "watermark": str(max([int(seal["source_watermark"])] + [int(b["result"]["watermark"]) for b in batches if b["result"] is not None])),
        "application_seq": str(max([1]+[int(b["result"]["application_seq"]) for b in batches if b["result"] is not None])+1),
        "recorded_utc": PROCESSING,
        "processing_complete": complete, "complete_success": status == "accepted",
        "learning_accepted": bool(counts["accepted_groups"]), "durable": True,
        "scope": "application_group_results", "source_authentication": "not_performed"}
    return rehash(value, "terminal_result_id")


class CaptureApplicationContractTests(unittest.TestCase):
    def test_positive_seal_registration_real_proofs_batch_and_terminal_contracts(self):
        ctx, seal = sealed(max_batch_members=4)
        self.assertIs(C.validate_seal(seal, ctx["request"]), seal)
        self.assertIs(C.validate_registration(registration(seal), seal)["learning_accepted"], False)
        groups, batches = report(ctx, seal)
        for row in batches:
            self.assertEqual(C.validate_batch(row["result"], seal), row["result"])
        for row in groups:
            self.assertEqual(C.validate_group_result(row["result"], row["sealed_group"]), row["result"])
        value = terminal(seal, groups, batches)
        self.assertEqual(C.validate_terminal(value, seal, groups, batches), value)
        self.assertTrue(value["complete_success"])
        # An unchanged replay can legitimately add no new IDs; the journal port
        # separately compares exact new-ID counts against its pinned snapshot.
        replay = batch_result(ctx, seal, replay=True)
        self.assertEqual(C.validate_batch(replay, seal), replay)

    def test_initial_withheld_oversized_group_preserves_independent_work(self):
        ctx, seal = sealed(sizes=(501, 2))
        C.validate_seal(seal, ctx["request"])
        held = next(g for g in seal["groups"] if g["status"] == "withheld")
        self.assertEqual(len(held["receipt_ids"]), 501)
        C.validate_group_result(withheld(held), held)
        groups, batches = report(ctx, seal)
        result = terminal(seal, groups, batches)
        C.validate_terminal(result, seal, groups, batches)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["counts"]["withheld_members"], 501)
        self.assertTrue(result["processing_complete"])
        self.assertFalse(result["complete_success"])

    def test_real_verified_zero_and_void_facts_do_not_collapse_numeric_identity(self):
        _, journal, _, _ = context(sizes=(4,))
        lines = E.STATS.splitlines(keepends=True)
        no_stat = lines[0] + b"".join(lines[2:])
        for absent in (False, True):
            envelope, objects = E.fixture(stats=no_stat, stats_audit=E.csv_audit(2),
                snaps=E.SNAPS.replace(b"Fixture Receiver", b"Different Receiver") if absent else E.SNAPS)
            request = V.make_request(capture_request(envelope, objects))
            ctx = V.bind_context(journal, request, lambda oid: E.stream(objects[oid]), clock=lambda: PROCESSING)
            seal = V.prepare_seal(ctx)
            C.validate_seal(seal, request)
            fact=seal["groups"][0]["fact"]
            self.assertEqual(fact["status"], "VOID" if absent else "FINAL")
            self.assertIs(fact["played"], not absent)
            if absent:
                self.assertIsNone(fact["actual"])
                wrong=copy.deepcopy(seal);wrong["groups"][0]["fact"]["actual"]=0.0
                wrong["groups"][0]["fact_id"]=fingerprint(wrong["groups"][0]["fact"]);seal_hash(wrong)
                with self.assertRaises(ValueError):C.validate_seal(wrong,request)
            else:
                self.assertEqual(fact["actual"],0.0)
                self.assertIs(type(fact["actual"]),float)
            groups,batches=report(ctx,seal)
            C.validate_terminal(terminal(seal,groups,batches),seal,groups,batches)
        ctx,seal=sealed()
        other=copy.deepcopy(seal);other["groups"][0]["fact"]["actual"]=80
        other["groups"][0]["fact_id"]=fingerprint(other["groups"][0]["fact"]);seal_hash(other)
        C.validate_seal(other,ctx["request"])
        self.assertNotEqual(other["seal_id"],seal["seal_id"])
        self.assertIs(type(other["groups"][0]["fact"]["actual"]),int)

    def test_empty_and_all_withheld_normal_terminal_are_honest_no_learning(self):
        for kwargs,status in (({"sizes":()},"no_supported_membership"),({"sizes":(4,),"missing_first":True},"withheld")):
            ctx,seal=sealed(**kwargs)
            C.validate_seal(seal,ctx["request"])
            self.assertIsNone(registration(seal)["next_step_id"])
            C.validate_registration(registration(seal),seal)
            groups,batches=report(ctx,seal)
            result=terminal(seal,groups,batches)
            C.validate_terminal(result,seal,groups,batches)
            self.assertEqual(result["status"],status)
            self.assertFalse(result["learning_accepted"])
            self.assertFalse(result["complete_success"])

    def test_missing_measurement_is_withheld_not_fabricated_zero_or_void(self):
        ctx, seal = sealed(missing_first=True)
        C.validate_seal(seal, ctx["request"])
        held = next(g for g in seal["groups"] if g["status"] == "withheld")
        self.assertIsNone(held["fact"])
        self.assertIsNone(held["fact_id"])
        self.assertIsNone(held["batch_ordinal"])
        groups, batches = report(ctx, seal)
        C.validate_terminal(terminal(seal, groups, batches), seal, groups, batches)

    def test_seal_exact_keys_types_hashes_membership_assignment_and_limits(self):
        ctx, seal = sealed(max_batch_members=4)
        changes = [
            lambda s: s.update(version=True), lambda s: s.update(extra=True),
            lambda s: s.update(application_id=fingerprint("wrong")),
            lambda s: s.update(source_vector_id=fingerprint("wrong")),
            lambda s: s.update(source_watermark="00"),
            lambda s: s.update(season=True), lambda s: s.update(weeks=[True]),
            lambda s: s["limits"].update(members=True),
            lambda s: s.update(membership_id=fingerprint("wrong")),
            lambda s: s["groups"][0].update(fact_id=fingerprint("wrong")),
            lambda s: s["groups"][0].update(status="accepted"),
            lambda s: s["groups"][0].update(batch_ordinal=True),
            lambda s: s["groups"][0]["receipt_ids"].pop(),
            lambda s: s["batches"][0].update(ordinal=True),
            lambda s: s["batches"][0].update(member_count=3),
            lambda s: s["batches"][0].update(step_id=fingerprint("wrong")),
            lambda s: s["batches"].pop(),
            lambda s: s["groups"].reverse(),
        ]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                value = copy.deepcopy(seal); change(value); seal_hash(value)
                with self.assertRaises(ValueError): C.validate_seal(value, ctx["request"])
        value = copy.deepcopy(seal);value["seal_id"] = fingerprint("wrong")
        with self.assertRaises(ValueError): C.validate_seal(value, ctx["request"])

    def test_resealed_fact_source_game_clock_and_numeric_semantics(self):
        ctx, seal = sealed()
        changes = [
            lambda f: f.update(actual=True), lambda f: f.update(actual=float("nan")),
            lambda f: f.update(status="PENDING"), lambda f: f.update(played=1),
            lambda f: f.update(reason="unknown"), lambda f: f.update(week=True),
            lambda f: f["game"].update(season=True), lambda f: f["game"].update(game_id="wrong"),
            lambda f: f["game"].update(state="LIVE"), lambda f: f["game"].update(home=f["game"]["away"]),
            lambda f: f["source_observations"].clear(),
            lambda f: f["source_observations"]["espn.scoreboard"].update(observation_id=fingerprint("wrong")),
            lambda f: f["source_observations"]["espn.scoreboard"].update(source="wrong"),
            lambda f: f["source_observations"]["espn.scoreboard"].update(observed_utc="2020-01-01T00:00:00Z"),
        ]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                value = copy.deepcopy(seal); change(value["groups"][0]["fact"])
                try:
                    value["groups"][0]["fact_id"] = fingerprint(value["groups"][0]["fact"]);seal_hash(value)
                except ValueError:
                    pass  # The validator must also reject an unhashable scalar.
                with self.assertRaises(ValueError): C.validate_seal(value, ctx["request"])

    def test_registration_resealed_wrong_identity_counts_flags_and_original_next_step(self):
        _, seal = sealed()
        for key, value in (("version", True), ("status", "accepted"), ("durable", 1), ("learning_accepted", True),
                           ("source_authentication", "verified"), ("group_count", True), ("member_count", 1),
                           ("batch_count", 0), ("next_step_id", None), ("application_seq", "0"),
                           ("recorded_utc", "not-time"), ("source_watermark", "00"), ("seal_id", fingerprint("wrong"))):
            with self.subTest(key=key):
                value = {**registration(seal), key: value};rehash(value, "registration_id")
                with self.assertRaises(ValueError): C.validate_registration(value, seal)

    def test_batch_resealed_unknown_partial_duplicate_counts_and_watermark_faults(self):
        ctx, seal = sealed()
        base = batch_result(ctx, seal)
        changes = [
            lambda b: b.update(ordinal=True), lambda b: b.update(status="pending"),
            lambda b: b.update(step_id=fingerprint("wrong")), lambda b: b["groups"].pop(),
            lambda b: b["groups"].reverse(), lambda b: b.update(added_observations=True),
            lambda b: b.update(added_revisions=999), lambda b: b.update(watermark=b["input_watermark"]),
            lambda b: b.update(added_observations=0, added_revisions=0),
            lambda b: b["groups"][0]["observation_ids"].pop(),
            lambda b: b["groups"][0].update(membership_id=fingerprint("wrong")),
        ]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                value = copy.deepcopy(base);change(value);rehash(value, "batch_result_id")
                with self.assertRaises(ValueError): C.validate_batch(value, seal)
        value = batch_result(ctx, seal, not_applied=True)
        C.validate_batch(value, seal)
        value["groups"][0]["reason_codes"] = ["source_integrity_failed"];rehash(value, "batch_result_id")
        with self.assertRaises(ValueError): C.validate_batch(value, seal)

    def test_terminal_complete_counts_hashes_flags_reasons_and_membership(self):
        ctx, seal = sealed()
        groups, batches = report(ctx, seal)
        base = terminal(seal, groups, batches)
        changes = [
            lambda t: t.update(processing_complete=False), lambda t: t.update(complete_success=False),
            lambda t: t.update(learning_accepted=1), lambda t: t.update(status="partial"),
            lambda t: t.update(reason_codes=["unknown"]), lambda t: t["counts"].update(groups=True),
            lambda t: t["counts"].update(members=1), lambda t: t.update(group_report_id=fingerprint("subset")),
            lambda t: t.update(current_membership_id=fingerprint("new")),
            lambda t: t.update(watermark="0"),
        ]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                value=copy.deepcopy(base);change(value);rehash(value,"terminal_result_id")
                with self.assertRaises(ValueError): C.validate_terminal(value, seal, groups, batches)

    def test_terminal_membership_stop_is_incomplete_not_full_success(self):
        ctx, seal = sealed()
        groups, batches = report(ctx, seal, incomplete="membership")
        value = terminal(seal, groups, batches, status="incomplete_membership_changed",
            reasons=["application_membership_changed"], current=fingerprint("new-complete-membership"))
        C.validate_terminal(value, seal, groups, batches)
        self.assertFalse(value["processing_complete"])
        self.assertFalse(value["complete_success"])
        self.assertFalse(value["learning_accepted"])

    def test_resealed_empty_or_subset_report_never_completes_nonempty_seal(self):
        ctx, seal = sealed(max_batch_members=4)
        groups, batches = report(ctx, seal)
        for chosen_groups, chosen_batches in (([], []), (groups[:1], batches[:1]), (list(reversed(groups)), batches)):
            with self.subTest(groups=len(chosen_groups)):
                value=terminal(seal, chosen_groups, chosen_batches)
                with self.assertRaises(ValueError): C.validate_terminal(value, seal, chosen_groups, chosen_batches)

    def test_resealed_assignment_cannot_swap_group_order_across_ordinals(self):
        ctx, seal = sealed(max_batch_members=4)
        value = copy.deepcopy(seal)
        value["batches"][0]["group_ids"],value["batches"][1]["group_ids"] = value["batches"][1]["group_ids"],value["batches"][0]["group_ids"]
        for group in value["groups"]:
            group["batch_ordinal"] = 1-group["batch_ordinal"]
        seal_hash(value)
        with self.assertRaises(ValueError): C.validate_seal(value, ctx["request"])

    def test_initial_withheld_group_cannot_acquire_learning_proofs(self):
        ctx, seal = sealed(missing_first=True)
        held=next(group for group in seal["groups"] if group["status"]=="withheld")
        value=withheld(held)
        value.update(status="accepted",reason_codes=[],observation_ids=sorted(fingerprint(["invented-observation",rid]) for rid in held["receipt_ids"]))
        with self.assertRaises(ValueError): C.validate_group_result(value, held)

    def test_observation_limit_is_required_for_each_planned_member(self):
        ctx, seal = sealed()
        value=copy.deepcopy(seal);value["limits"]["observations"]=1;seal_hash(value)
        with self.assertRaises(ValueError): C.validate_seal(value, ctx["request"])

    def test_integrity_terminal_cannot_relabel_membership_reason(self):
        ctx, seal = sealed()
        groups,batches=report(ctx,seal,incomplete="membership")
        value=terminal(seal,groups,batches,status="incomplete_integrity_or_capacity",reasons=["application_membership_changed"],current=fingerprint("changed"))
        with self.assertRaises(ValueError): C.validate_terminal(value,seal,groups,batches)

    def test_unassigned_unknown_proofs_and_revision_work_limit_are_rejected(self):
        ctx,seal=sealed(sizes=(2,2))
        base=batch_result(ctx,seal)
        bad=copy.deepcopy(base)
        for index,group in enumerate(bad["groups"]):
            group["revision_ids"]=["invented-revision-%03d-%03d"%(index,n) for n in range(300)]
        bad["added_revisions"]=600;rehash(bad,"batch_result_id")
        with self.assertRaises(ValueError):C.validate_batch(bad,seal)
        bad=copy.deepcopy(base);bad["groups"][1]["observation_ids"]=bad["groups"][0]["observation_ids"]
        rehash(bad,"batch_result_id")
        with self.assertRaises(ValueError):C.validate_batch(bad,seal)
        for cap in ("observations","revisions"):
            small=copy.deepcopy(seal);small["limits"][cap]=1
            # C.validate_batch must independently enforce its supplied seal,
            # even when the caller already validated its immutable assignment.
            with self.assertRaises(ValueError):C.validate_batch(base,small)

    def test_full_active_report_prefix_and_integrity_stop_preserve_all_assignments(self):
        ctx,seal=sealed(max_batch_members=4)
        groups,batches=report(ctx,seal)
        batches[1]["result"]=None
        for row in groups:
            if row["sealed_group"]["batch_ordinal"]==1:
                row.update(execution_state="pending",result=None)
        C.validate_report(seal,groups,batches)
        value=terminal(seal,groups,batches)
        with self.assertRaises(ValueError):C.validate_terminal(value,seal,groups,batches)
        reasons=["source_integrity_failed"]
        for row in groups:
            if row["execution_state"]=="pending":
                row.update(execution_state="not_applied",result=withheld(row["sealed_group"],reasons))
        value=terminal(seal,groups,batches,status="incomplete_integrity_or_capacity",reasons=reasons)
        C.validate_terminal(value,seal,groups,batches)
        self.assertTrue(value["learning_accepted"])
        self.assertFalse(value["processing_complete"])
        self.assertEqual(value["counts"]["not_applied_members"],value["counts"]["withheld_members"])
        for index,change in enumerate((
            lambda g,b:b[1].update(assignment=copy.deepcopy(b[0]["assignment"])),
            lambda g,b:g[0].update(execution_state="unknown"),
            lambda g,b:g[0]["result"].update(reason_codes=["unknown"]),
            lambda g,b:b[0].update(result=None),
        )):
            with self.subTest(index=index):
                gs,bs=copy.deepcopy(groups),copy.deepcopy(batches);change(gs,bs)
                forged=terminal(seal,gs,bs,status="incomplete_integrity_or_capacity",reasons=reasons)
                with self.assertRaises(ValueError):C.validate_terminal(forged,seal,gs,bs)

    def test_active_report_out_of_order_batch_and_wrong_group_crosslink_fail(self):
        ctx,seal=sealed(max_batch_members=4)
        groups,batches=report(ctx,seal)
        bad=copy.deepcopy(batches);bad[0]["result"]=None
        with self.assertRaises(ValueError):C.validate_report(seal,groups,bad)
        bad=copy.deepcopy(groups);bad[0]["result"]=copy.deepcopy(bad[1]["result"])
        with self.assertRaises(ValueError):C.validate_report(seal,bad,batches)
        bad=copy.deepcopy(groups);bad[0]["sealed_group"]["fact_id"]=fingerprint("unknown")
        with self.assertRaises(ValueError):C.validate_report(seal,bad,batches)
        groups,batches=report(ctx,seal,incomplete="membership")
        with self.assertRaises(ValueError):C.validate_report(seal,groups,batches)

    def test_evidence_and_ledger_chronology_and_terminal_cause_cannot_regress(self):
        ctx,seal=sealed(max_batch_members=4)
        groups,batches=report(ctx,seal)
        bad=batch_result(ctx,seal,input_watermark="1")
        with self.assertRaises(ValueError):C.validate_batch(bad,seal)
        for key,value in (("input_watermark",str(int(batches[0]["result"]["watermark"])-1)),
                          ("application_seq",batches[0]["result"]["application_seq"])):
            bad=copy.deepcopy(batches);bad[1]["result"][key]=value;rehash(bad[1]["result"],"batch_result_id")
            with self.assertRaises(ValueError):C.validate_report(seal,groups,bad)
        for key,value in (("watermark",str(int(batches[-1]["result"]["watermark"])-1)),
                          ("application_seq",batches[-1]["result"]["application_seq"])):
            bad=terminal(seal,groups,batches);bad[key]=value;rehash(bad,"terminal_result_id")
            with self.assertRaises(ValueError):C.validate_terminal(bad,seal,groups,batches)
        ctx,seal=sealed()
        groups,batches=report(ctx,seal,incomplete="membership")
        bad=terminal(seal,groups,batches,status="incomplete_integrity_or_capacity",
            reasons=["source_integrity_failed"],current=seal["membership_id"])
        with self.assertRaises(ValueError):C.validate_terminal(bad,seal,groups,batches)

    def test_committed_batch_may_explicitly_withhold_one_complete_group(self):
        ctx,seal=sealed()
        groups,batches=report(ctx,seal)
        result=batches[0]["result"]
        held=withheld(seal["groups"][1],["stale_conflicting_source_observation"])
        result["groups"][1]=held
        result["added_observations"]=len(result["groups"][0]["observation_ids"])
        result["added_revisions"]=len(result["groups"][0]["revision_ids"])
        result["watermark"]=str(int(result["input_watermark"])+result["added_observations"]+result["added_revisions"])
        rehash(result,"batch_result_id")
        groups[1]["result"]=copy.deepcopy(held)
        C.validate_batch(result,seal)
        value=terminal(seal,groups,batches)
        C.validate_terminal(value,seal,groups,batches)
        self.assertEqual(value["status"],"partial")
        self.assertEqual(value["counts"]["accepted_groups"],1)
        self.assertTrue(value["learning_accepted"])
        self.assertFalse(value["complete_success"])

    def test_cross_batch_proof_reuse_cannot_complete_unrelated_groups(self):
        ctx,seal=sealed(sizes=(1,1),max_batch_members=1)
        original_groups,original_batches=report(ctx,seal)
        for field in ("observation_ids","revision_ids"):
            with self.subTest(field=field):
                groups,batches=copy.deepcopy(original_groups),copy.deepcopy(original_batches)
                batches[1]["result"]["groups"][0][field]=copy.deepcopy(batches[0]["result"]["groups"][0][field])
                rehash(batches[1]["result"],"batch_result_id")
                groups[1]["result"]=copy.deepcopy(batches[1]["result"]["groups"][0])
                forged=terminal(seal,groups,batches)
                with self.assertRaises(ValueError):C.validate_report(seal,groups,batches)
                with self.assertRaises(ValueError):C.validate_terminal(forged,seal,groups,batches)

    def test_resealed_scope_cannot_exceed_existing_result_identity_boundary(self):
        ctx,seal=sealed()
        value=copy.deepcopy(seal)
        group=value["groups"][0]
        old=group["group_id"]
        group["scope"][2]="x"*300
        group["group_id"]=fingerprint(group["scope"])
        group["membership_id"]=fingerprint({"binding_policy":V.BINDING_POLICY,
            **{key:group[key] for key in V.MEMBERSHIP_KEYS}})
        for batch in value["batches"]:
            batch["group_ids"]=sorted(group["group_id"] if gid==old else gid for gid in batch["group_ids"])
        value["groups"].sort(key=lambda g:g["group_id"])
        value["membership_id"]=V.membership_id(value["groups"])
        seal_hash(value)
        with self.assertRaises(ValueError):C.validate_seal(value,ctx["request"])


if __name__ == "__main__":
    unittest.main()
