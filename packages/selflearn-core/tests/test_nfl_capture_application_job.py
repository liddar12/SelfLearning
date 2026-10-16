"""Real verifier/kernel orchestration over an EXPLICIT FAKE application port.

The model below checks call ordering/IDs/retries, not SQL atomicity, durability,
database-owned timestamps, hosted Storage or a scalable production worker.
"""
import copy
import unittest
from unittest.mock import patch

from selflearn_core.adapters import nfl_capture_application_job as J
from selflearn_core.adapters import nfl_capture_application_v2 as V
from selflearn_core.adapters import nfl_capture_application_contracts as C
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.receipt_journal import JournalConflict
from selflearn_core.store.supabase_store import StoreError
from test_nfl_capture_application_v2 import context
from test_nfl_capture_application_contracts import registration,terminal,withheld,rehash
from test_nfl_capture_labels import journal_fixture
from test_nfl_capture_delivery_job import PROCESSING
import test_nfl_capture_evidence as E


class FakeApplicationPort:
    """Scripted in-memory test boundary; never masquerades as database proof."""
    def __init__(self):
        self.ctx,self.base,self.envelope,self.objects=context()
        self.request=self.ctx["request"]
        self.application=None
        self.registers,self.commits,self.stops,self.raw_reads,self.warms=[],[],[],[],[]
        self.before_register=self.before_commit=self.before_stop=None
        self.lose_register=self.lose_commit=None

    def raw(self,oid):
        self.raw_reads.append(oid)
        return E.stream(self.objects[oid])

    def read_observed_snapshot(self):return self.base.read_observed_snapshot()
    def get_object(self,oid):return self.base.get_object(oid)
    def get_capture_delivery(self,_oid):return None
    def get_capture_application(self,_aid):return copy.deepcopy(self.application)

    def prepare_capture_membership(self,season,weeks):
        # Explicit fake metadata only; real cache authority is SQL-owned.
        self.warms.append((season,copy.deepcopy(weeks)))
        return {"version":2,"kind":"nfl.capture_membership_cache.v2","scope":"derived_membership_cache_only",
            "durable_ack":False,"learning_accepted":False,"season":season,"weeks":copy.deepcopy(weeks),
            "authority_watermark":self.base.snapshot["watermark"],"policy_id":fingerprint("fake-policy"),
            "cache_id":fingerprint("fake-cache"),"membership_id":fingerprint("fake-membership"),"member_count":8,"group_count":2}

    def register_capture_application(self,request,seal,*,watermark):
        self.registers.append(watermark)
        if self.before_register:self.before_register(self)
        if watermark!=self.base.snapshot["watermark"]:raise JournalConflict("fake CAS")
        C.validate_seal(seal,request)
        ack=registration(seal)
        groups=[{"sealed_group":copy.deepcopy(g),"execution_state":"initial_withheld" if g["status"]=="withheld" else "pending",
                 "result":withheld(g) if g["status"]=="withheld" else None} for g in seal["groups"]]
        batches=[{"assignment":copy.deepcopy(b),"result":None} for b in seal["batches"]]
        self.application={"request":copy.deepcopy(request),"seal":copy.deepcopy(seal),"registration":ack,
            "groups":groups,"batches":batches,"status":{"next_step_id":ack["next_step_id"]},"terminal":None}
        if not batches:self.application["terminal"]=terminal(seal,groups,batches)
        if self.lose_register:raise self.lose_register
        return copy.deepcopy(ack)

    def commit_capture_application_batch(self,aid,step,groups,observations,proposals,*,watermark):
        self.commits.append(copy.deepcopy((groups,observations,proposals,watermark)))
        if self.before_commit:self.before_commit(self)
        if watermark!=self.base.snapshot["watermark"]:raise JournalConflict("fake CAS")
        app=self.application
        slot=next(row for row in app["batches"] if row["assignment"]["step_id"]==step)
        if slot["result"] is not None:return copy.deepcopy(slot["result"])
        no_apply=not groups
        if no_apply:
            groups=[withheld(g,["application_membership_changed"]) for g in app["seal"]["groups"] if g["group_id"] in slot["assignment"]["group_ids"]]
            added={"added_observations":0,"added_revisions":0,"watermark":watermark}
        else:
            # These fixture clocks/rows model a DB response; they are NOT DB
            # clocks or transactional execution. Actual SQL has a separate suite.
            wrapped=[{"version":1,**copy.deepcopy(row),"recorded_utc":PROCESSING} for row in proposals]
            added=self.base.commit_observed(observations,wrapped,watermark=watermark)
        ordinal=slot["assignment"]["ordinal"]
        result={"version":2,"kind":"nfl.capture_application_batch_result.v2","application_id":aid,"step_id":step,"ordinal":ordinal,
            "seal_id":app["seal"]["seal_id"],"membership_id":app["seal"]["membership_id"],"status":"not_applied" if no_apply else "committed",
            "groups":copy.deepcopy(groups),"added_observations":added["added_observations"],
            "added_revisions":added["added_revisions"],"watermark":added["watermark"],
            "input_watermark":watermark,"application_seq":str(ordinal+2),"recorded_utc":PROCESSING,
            "durable":True,"scope":"observations_and_batch_results","source_authentication":"not_performed"}
        rehash(result,"batch_result_id");C.validate_batch(result,app["seal"])
        slot["result"]=result
        by_gid={g["group_id"]:g for g in groups}
        for row in app["groups"]:
            if row["sealed_group"]["group_id"] in by_gid:
                row.update(execution_state="not_applied" if no_apply else "committed",result=copy.deepcopy(by_gid[row["sealed_group"]["group_id"]]))
        if no_apply:
            self._finish("incomplete_membership_changed",["application_membership_changed"],J._membership(self,self.base.snapshot,app["seal"]))
        elif all(b["result"] is not None for b in app["batches"]):
            app["terminal"]=terminal(app["seal"],app["groups"],app["batches"])
        else:
            app["status"]["next_step_id"]=next(b["assignment"]["step_id"] for b in app["batches"] if b["result"] is None)
        if self.lose_commit:raise self.lose_commit
        return copy.deepcopy(result)

    def _finish(self,status,reasons,current=None):
        app=self.application
        for row in app["groups"]:
            if row["execution_state"]=="pending":row.update(execution_state="not_applied",result=withheld(row["sealed_group"],reasons))
        app["terminal"]=terminal(app["seal"],app["groups"],app["batches"],status=status,reasons=reasons,current=current)
        C.validate_terminal(app["terminal"],app["seal"],app["groups"],app["batches"])
        return copy.deepcopy(app["terminal"])

    def stop_capture_application(self,aid,reason,*,watermark):
        self.stops.append((aid,reason,watermark))
        if self.before_stop:self.before_stop(self)
        return self._finish("incomplete_integrity_or_capacity",[reason])


def register(port,**kwargs):
    return J.register_capture_application(port,port.request,port.raw,clock=lambda:PROCESSING,**kwargs)


def step(port,**kwargs):
    app=port.application
    return J.commit_capture_application_step(port,app["seal"]["application_id"],app["status"]["next_step_id"],port.raw,clock=lambda:PROCESSING,**kwargs)


class ApplicationJobOrchestrationTests(unittest.TestCase):
    def test_real_raw_bind_kernel_two_steps_and_original_recovery_over_fake_port(self):
        port=FakeApplicationPort();original=copy.deepcopy(port.base.snapshot["receipts"])
        ack=register(port,max_batch_members=4)
        first=step(port);second=step(port)
        self.assertEqual(len(port.commits),2)
        self.assertTrue(port.application["terminal"]["complete_success"])
        self.assertEqual(port.base.snapshot["receipts"],original)
        warm_count=len(port.warms)
        def forbidden(*args):raise AssertionError("Original before raw/clock")
        self.assertEqual(J.register_capture_application(port,port.request,forbidden,clock=forbidden),ack)
        self.assertEqual(J.commit_capture_application_step(port,port.application["seal"]["application_id"],first["step_id"],forbidden,clock=forbidden),first)
        self.assertEqual(len(port.warms),warm_count)
        self.assertNotEqual(first["step_id"],second["step_id"])

    def test_changed_fence_rebinds_every_raw_source_and_bounded_attempts_exhaust(self):
        def once(port):
            port.before_register=None;port.base.snapshot["watermark"]=str(int(port.base.snapshot["watermark"])+1)
        port=FakeApplicationPort();port.before_register=once
        register(port)
        self.assertEqual(len(port.registers),2);self.assertEqual(len(port.raw_reads),10)
        def always(port):port.base.snapshot["watermark"]=str(int(port.base.snapshot["watermark"])+1)
        port=FakeApplicationPort();port.before_register=always
        with self.assertRaisesRegex(JournalConflict,"registration_retry_exhausted"):register(port,max_attempts=2)
        self.assertEqual(len(port.registers),2)
        port=FakeApplicationPort();register(port);port.before_commit=always
        with self.assertRaisesRegex(JournalConflict,"step_retry_exhausted"):step(port,max_attempts=2)
        self.assertEqual(len(port.commits),2)

    def test_same_fence_conflict_never_blindly_rebinds(self):
        def conflict(_port):raise JournalConflict("synthetic semantic conflict")
        port=FakeApplicationPort();port.before_register=conflict
        with self.assertRaisesRegex(JournalConflict,"without_changed_fence"):register(port)
        self.assertEqual(len(port.raw_reads),5)
        port=FakeApplicationPort();register(port);port.before_commit=conflict
        before=len(port.raw_reads)
        with self.assertRaisesRegex(JournalConflict,"without_changed_fence"):step(port)
        self.assertEqual(len(port.raw_reads)-before,5)

    def test_now_known_original_after_conflict_recovers_without_another_bind(self):
        port=FakeApplicationPort();port.lose_register=JournalConflict("fake raced original")
        ack=register(port)
        self.assertEqual(ack,port.application["registration"])
        self.assertEqual(len(port.raw_reads),5)
        port.lose_commit=JournalConflict("fake raced original")
        result=step(port)
        self.assertEqual(result,port.application["batches"][0]["result"])
        self.assertEqual(len(port.commits),1)

    def test_membership_change_precedes_raw_and_records_no_evidence_in_fake_boundary(self):
        port=FakeApplicationPort();register(port)
        changed=journal_fixture(vintages=3,watermark="200")
        port.base.snapshot,port.base.objects=changed.snapshot,changed.objects
        port.raw=lambda oid:(_ for _ in ()).throw(AssertionError("No raw after membership change"))
        result=step(port)
        self.assertEqual(result["status"],"not_applied")
        self.assertEqual(port.commits[0][1:3],([],[]))
        self.assertEqual(port.application["terminal"]["status"],"incomplete_membership_changed")

    def test_integrity_capacity_and_existing_terminal_stop_not_transient_failure(self):
        for kind in ("integrity","capacity","proof_capacity","fact"):
            with self.subTest(kind=kind):
                port=FakeApplicationPort();register(port)
                original=V.group_proofs
                def changed(*args):
                    group,obs,rev=original(*args)
                    if kind=="fact":obs[0]["measurement"]["actual"]=79.0
                    else:obs*=200
                    return group,obs,rev
                if kind=="integrity":
                    port.raw=lambda oid:iter([b"corrupt"])
                    scope=patch.object(V,"group_proofs",side_effect=original)
                elif kind=="capacity":scope=patch.object(V,"batch_wire",side_effect=V.capacity._WireLimit("injected"))
                else:scope=patch.object(V,"group_proofs",side_effect=changed)
                with scope,self.assertRaises(J.CaptureApplicationStopped):step(port)
                self.assertFalse(port.application["terminal"]["complete_success"])
                self.assertEqual(port.commits,[])
                with self.assertRaises(J.CaptureApplicationStopped):step(port)
        for error in (TimeoutError("temporary"),StoreError(503),ValueError("not an integrity failure")):
            port=FakeApplicationPort();register(port)
            with patch.object(V,"bind_context",side_effect=error),self.assertRaises(type(error)):step(port)
            self.assertEqual(port.stops,[]);self.assertIsNone(port.application["terminal"])

    def test_step_cas_rebind_and_pre_bind_membership_or_terminal_inconsistency(self):
        port=FakeApplicationPort();register(port)
        def once(port):
            port.before_commit=None;port.base.snapshot["watermark"]=str(int(port.base.snapshot["watermark"])+1)
        port.before_commit=once
        before=len(port.raw_reads);step(port)
        self.assertEqual(len(port.raw_reads)-before,10)
        self.assertEqual(len(port.commits),2)
        port=FakeApplicationPort();register(port)
        sentinel=object()
        with patch.object(J,"_membership",return_value=port.application["seal"]["membership_id"]),patch.object(V,"membership_id",return_value=fingerprint("changed")),patch.object(port,"commit_capture_application_batch",return_value=sentinel) as commit:
            # Deliberately inconsistent metadata/bind is a fake fault: the job
            # must submit EMPTY arrays for SQL to independently prove or reject
            # current invalidation. This is NOT a fabricated successful ACK.
            result=step(port)
        self.assertIs(result,sentinel)
        self.assertEqual(commit.call_args.args[2:],([],[],[]))

    def test_invalid_or_replaced_request_and_expired_budget_do_not_start_work(self):
        port=FakeApplicationPort();register(port)
        port.application["request"]["capture_request"]["producer_id"]="different"
        with self.assertRaisesRegex(JournalConflict,"request_conflict"):register(port)
        ticks=iter([0,1])
        port=FakeApplicationPort()
        with self.assertRaises(J.CaptureApplicationBudgetExceeded):register(port,max_run_seconds=.5,monotonic=lambda:next(ticks))
        self.assertEqual(port.registers,[]);self.assertEqual(port.raw_reads,[])
