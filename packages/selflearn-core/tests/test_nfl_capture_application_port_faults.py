"""Adversarial Python port contracts over FAKE pages/ACKs, not SQL proof."""
import copy
import unittest
from unittest.mock import patch

from selflearn_core.adapters import nfl_capture_application_contracts as C
from selflearn_core.adapters.nfl_capture_application_journal import NFLCaptureApplicationJournal, CaptureApplicationBudgetExceeded
from selflearn_core.adapters import nfl_capture_application_job as job
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.receipt_journal import JournalConflict
from selflearn_core.store.supabase_store import StoreConflict, StoreError, SupabaseStore
from test_nfl_capture_application_contracts import sealed, registration, report, terminal, rehash, batch_result, withheld


class Pages:
    """Self-consistent fake application ledger. Explicitly NOT a database."""
    def __init__(self, *, complete=True):
        self.ctx,self.seal=sealed(max_batch_members=4)
        self.ack=registration(self.seal)
        self.groups,self.batches=report(self.ctx,self.seal)
        self.terminal=terminal(self.seal,self.groups,self.batches) if complete else None
        if not complete:
            for row in self.groups:row.update(execution_state="pending",result=None)
            for row in self.batches:row["result"]=None
        self.fence=self.terminal["application_seq"] if complete else "1"
        self.calls=[];self.mutate=None;self.failure=None
        self.status={"version":2,"kind":"nfl.capture_application_status.v2","application_id":self.seal["application_id"],
            "registration_id":self.ack["registration_id"],"seal_id":self.seal["seal_id"],"membership_id":self.seal["membership_id"],
            "status":"terminal" if complete else "active","batch_count":len(self.batches),
            "committed_batches":len(self.batches) if complete else 0,
            "next_step_id":None if complete else self.batches[0]["assignment"]["step_id"],
            "terminal_result_id":None if not complete else self.terminal["terminal_result_id"],
            "ledger_watermark":self.fence,"scope":"pinned_application_progress","durable_ack":False,
            "execution_ready":not complete,"source_authentication":"not_performed"}

    def application(self):
        return {"version":2,"request":self.ctx["request"],"seal":self.seal,"registration":self.ack,
            "groups":self.groups,"batches":self.batches,"status":self.status,"terminal":self.terminal,"ledger_watermark":self.fence}

    def request(self,method,path,*,data):
        self.calls.append((method,path,copy.deepcopy(data)))
        if self.failure is not None:raise self.failure
        if not path.endswith("selflearn_journal_read_capture_application_v2"):
            raise AssertionError("Unexpected fake non-read RPC")
        view=data["p_view"]
        records={"registration":[{"request":self.ctx["request"],"seal_header":{k:v for k,v in self.seal.items() if k not in ("groups","batches")},"registration":self.ack}],
            "groups":self.groups,"batches":self.batches,"status":[self.status],"terminal":[] if self.terminal is None else [self.terminal]}
        if view=="batch":
            rows=[row["result"] for row in self.batches if row["assignment"]["step_id"]==data["p_key"] and row["result"] is not None]
        else:rows=records[view]
        start=0
        if data["p_after"] is not None:
            values=[row["sealed_group"]["group_id"] if view=="groups" else str(row["assignment"]["ordinal"]) for row in rows]
            start=values.index(data["p_after"])+1
        selected=rows[start:start+data["p_limit"]]
        after=None
        if start+len(selected)<len(rows):
            after=selected[-1]["sealed_group"]["group_id"] if view=="groups" else str(selected[-1]["assignment"]["ordinal"])
        page={"version":2,"kind":"nfl.capture_application_page.v2","found":True,"application_id":self.seal["application_id"],
            "view":view,"ledger_watermark":self.fence,"registration_id":self.ack["registration_id"],"seal_id":self.seal["seal_id"],
            "records":copy.deepcopy(selected),"total_records":len(rows),"next_after":after}
        if self.mutate is not None:self.mutate(page,data)
        return page


def port(fake,**kwargs):
    return NFLCaptureApplicationJournal("https://offline.supabase.invalid","sb_secret_fake_application_pages",transport=fake,page_size=1,**kwargs)


class ApplicationPortFaultTests(unittest.TestCase):
    def test_membership_prewarm_is_only_a_bounded_derived_cache_hint(self):
        class CacheRPC:
            def __init__(self):
                self.calls=[]
                self.reply={"version":2,"kind":"nfl.capture_membership_cache.v2","scope":"derived_membership_cache_only",
                    "durable_ack":False,"learning_accepted":False,"season":2026,"weeks":[6],
                    "authority_watermark":"100","policy_id":fingerprint("fake-policy"),"cache_id":fingerprint("fake-cache"),
                    "membership_id":fingerprint("fake-membership"),"member_count":8,"group_count":2}
            def request(self,method,path,*,data):
                self.calls.append((method,path,copy.deepcopy(data)))
                return copy.deepcopy(self.reply)
        fake=CacheRPC();journal=port(fake)
        self.assertEqual(journal.prepare_capture_membership(2026,[6]),fake.reply)
        self.assertEqual(fake.calls,[("POST","/rest/v1/rpc/selflearn_journal_prepare_capture_membership_v2",
            {"p_season":2026,"p_weeks_json":"[6]"})])
        fake.reply.update(member_count=0,group_count=0)
        self.assertEqual(journal.prepare_capture_membership(2026,[6]),fake.reply)
        changes=[{"version":True},{"version":1},{"extra":"SECRET"},{"kind":"SECRET"},{"scope":"observations_and_batch_results"},
            {"durable_ack":True},{"durable_ack":0},{"learning_accepted":True},{"learning_accepted":0},
            {"season":True},{"season":2025},{"season":2026.0},{"weeks":[6.0]},{"weeks":[True]},
            {"weeks":[]},{"weeks":[6,6]},{"weeks":"SECRET"},{"authority_watermark":"00"},
            {"authority_watermark":100},{"policy_id":"SECRET"},{"cache_id":None},{"membership_id":True},
            {"member_count":True},{"member_count":-1},{"member_count":10001},{"group_count":True},
            {"group_count":-1},{"member_count":1,"group_count":2},{"member_count":1,"group_count":0},
            {"member_count":0,"group_count":1}]
        baseline=copy.deepcopy(fake.reply)
        for change in changes:
            with self.subTest(change=change):
                fake.reply={**baseline,**change}
                with self.assertRaises(StoreError) as caught:journal.prepare_capture_membership(2026,[6])
                self.assertEqual(caught.exception.status,502)
                self.assertNotIn("SECRET",str(caught.exception))
        fake.reply=baseline;fake.reply.pop("policy_id")
        with self.assertRaises(StoreError):journal.prepare_capture_membership(2026,[6])

    def test_invalid_membership_prewarm_period_performs_no_io(self):
        fake=Pages();journal=port(fake)
        for season,weeks in ((True,[6]),(2026.0,[6]),(1999,[6]),(2101,[6]),(2026,None),(2026,()),
                             (2026,[]),(2026,[True]),(2026,[6.0]),(2026,[0]),(2026,[19]),
                             (2026,[6,6]),(2026,[7,6]),(2026,list(range(1,20)))):
            with self.subTest(season=season,weeks=weeks),self.assertRaises(ValueError):
                journal.prepare_capture_membership(season,weeks)
        self.assertEqual(fake.calls,[])

    def test_complete_fake_pages_and_active_prefix_are_reconstructed_without_sql_claim(self):
        for complete in (False,True):
            fake=Pages(complete=complete);journal=port(fake)
            value=journal.get_capture_application(fake.seal["application_id"])
            self.assertEqual(value,fake.application())
            self.assertTrue(all(data["p_ledger_watermark"]==fake.fence for _,_,data in fake.calls[1:]))

    def test_invalid_page_keys_versions_metadata_fences_counts_and_cursors_fail_redacted(self):
        changes=[
            lambda p,d:p.update(version=True),lambda p,d:p.update(version=1),lambda p,d:p.update(extra="SECRET"),
            lambda p,d:p.update(found=1),lambda p,d:p.update(application_id=fingerprint("wrong")),
            lambda p,d:p.update(total_records=True),lambda p,d:p.update(ledger_watermark="00"),
            lambda p,d:p.update(records=[]),lambda p,d:p.update(next_after="SECRET"),
            lambda p,d:p.update(registration_id="SECRET"),
        ]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                fake=Pages();fake.mutate=change
                with self.assertRaises(StoreError) as caught:port(fake).get_capture_application(fake.seal["application_id"])
                self.assertEqual(caught.exception.status,502)
                self.assertNotIn("SECRET",str(caught.exception))

    def test_partial_pages_cannot_change_fence_total_order_or_terminate_early(self):
        changes=[
            lambda p:p.update(ledger_watermark="99"),lambda p:p.update(total_records=1),
            lambda p:p.update(next_after=fingerprint("wrong")),lambda p:p.update(records=[]),
            lambda p:p.update(registration_id=fingerprint("wrong")),lambda p:p.update(next_after=None),
        ]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                fake=Pages()
                def mutate(p,d):
                    if d["p_view"]=="groups" and d["p_after"] is None:change(p)
                fake.mutate=mutate
                with self.assertRaises(StoreError):port(fake).get_capture_application(fake.seal["application_id"])

    def test_status_and_terminal_flags_sequences_and_hashes_never_replace_full_report(self):
        for key,value in (("execution_ready",True),("durable_ack",True),("batch_count",True),
                          ("committed_batches",0),("source_authentication","verified"),("status","active")):
            fake=Pages();fake.status[key]=value
            with self.subTest(key=key),self.assertRaises(StoreError):port(fake).get_capture_application(fake.seal["application_id"])
        for key,value in (("application_seq","1"),("watermark","0"),("complete_success",False),("group_report_id",fingerprint("subset"))):
            fake=Pages();fake.terminal[key]=value;rehash(fake.terminal,"terminal_result_id")
            fake.status["terminal_result_id"]=fake.terminal["terminal_result_id"]
            with self.subTest(key=key),self.assertRaises(StoreError):port(fake).get_capture_application(fake.seal["application_id"])

    def test_unknown_application_is_not_a_successful_empty_known_application(self):
        fake=Pages()
        def missing(p,d):
            p.update(found=False,records=[],total_records=0,next_after=None,registration_id=None,seal_id=None,ledger_watermark=None)
        fake.mutate=missing
        self.assertIsNone(port(fake).get_capture_application(fake.seal["application_id"]))

    def test_transport_failure_redaction_conflict_and_cancellation(self):
        class Cancelled(BaseException):pass
        for error,expected in ((RuntimeError("SECRET"),StoreError),(TimeoutError("SECRET"),StoreError),
                               (StoreError(503),StoreError),(StoreError("SECRET"),StoreError),
                               (StoreError(True),StoreError),(StoreError(999),StoreError),
                               (StoreConflict(),JournalConflict),(Cancelled(),Cancelled)):
            fake=Pages();fake.failure=error
            with self.subTest(error=type(error).__name__),self.assertRaises(expected) as caught:
                port(fake).get_capture_application(fake.seal["application_id"])
            self.assertNotIn("SECRET",str(caught.exception))
            if isinstance(error,StoreError) and (type(error.status) is not int or not 400<=error.status<=599):
                self.assertEqual(caught.exception.status,503)

    def test_invalid_constructor_budget_opens_no_resource_and_deadline_does_not_mutate_other_port(self):
        for value in (True,False,0,-1,46,float("nan"),float("inf"),"45"):
            with self.subTest(value=value),patch.object(SupabaseStore,"__init__",side_effect=AssertionError("constructor opened resource")):
                with self.assertRaises(ValueError):port(Pages(),max_run_seconds=value)
        fake=Pages();ticks=iter([0,46])
        expired=port(fake,max_run_seconds=45,monotonic=lambda:next(ticks))
        with self.assertRaises(CaptureApplicationBudgetExceeded):expired.get_capture_application(fake.seal["application_id"])
        self.assertEqual(fake.calls,[])
        self.assertEqual(port(fake).get_capture_application(fake.seal["application_id"]),fake.application())

    def test_fresh_bad_added_counts_cannot_be_blessed_by_matching_stored_fake_response(self):
        fake=Pages(complete=False);journal=port(fake)
        seal,ctx=fake.seal,fake.ctx
        original=fake.application()
        command=ctx["staged"]["groups"]
        from selflearn_core.adapters import nfl_capture_application_v2 as V
        gid=seal["batches"][0]["group_ids"][0]
        group,obs,rev=V.group_proofs(ctx,next(g for g in command if g["group_id"]==gid))
        for observed,revisions in ((0,0),(0,len(rev)),(len(obs),0)):
            with self.subTest(observed=observed,revisions=revisions):
                bad=batch_result(ctx,seal)
                bad.update(added_observations=observed,added_revisions=revisions,
                    watermark=str(int(seal["source_watermark"])+observed+revisions))
                rehash(bad,"batch_result_id")
                journal._observed_snapshot=copy.deepcopy(ctx["snapshot"])
                with patch.object(journal,"get_capture_application",return_value=original),patch.object(journal,"_application_rpc",return_value=bad),patch.object(journal,"get_capture_application_batch",return_value=bad):
                    with self.assertRaises(StoreError):
                        journal.commit_capture_application_batch(seal["application_id"],bad["step_id"],[group],obs,rev,watermark=seal["source_watermark"])

    def test_registration_original_recovery_never_relaxes_a_malformed_new_ack(self):
        fake=Pages();journal=port(fake)
        seal=copy.deepcopy(fake.seal)
        seal["source_watermark"]=str(int(seal["source_watermark"])+1)
        rehash(seal,"seal_id")
        # A concurrent exact request can legitimately have registered an older
        # seal. Only a completely validated matching original is recoverable.
        with patch.object(journal,"_application_rpc",return_value=fake.ack),patch.object(journal,"get_capture_application",return_value=fake.application()):
            self.assertEqual(journal.register_capture_application(fake.ctx["request"],seal,watermark=seal["source_watermark"]),fake.ack)
        with patch.object(journal,"_application_rpc",return_value={"version":2}),patch.object(journal,"get_capture_application",return_value=fake.application()):
            with self.assertRaises(StoreError):journal.register_capture_application(fake.ctx["request"],seal,watermark=seal["source_watermark"])
        with self.assertRaises(ValueError):journal.register_capture_application(fake.ctx["request"],seal,watermark="0")

    def test_genuine_same_fence_withheld_original_uses_returned_ids_not_new_attempt_ids(self):
        fake=Pages(complete=False);journal=port(fake);seal,ctx=fake.seal,fake.ctx
        from selflearn_core.adapters import nfl_capture_application_v2 as V
        gid=seal["batches"][0]["group_ids"][0]
        group,obs,rev=V.group_proofs(ctx,next(g for g in ctx["staged"]["groups"] if g["group_id"]==gid))
        original=batch_result(ctx,seal)
        original.update(groups=[withheld(seal["groups"][0],["stale_conflicting_source_observation"])],
            added_observations=0,added_revisions=0,watermark=seal["source_watermark"])
        rehash(original,"batch_result_id")
        journal._observed_snapshot=copy.deepcopy(ctx["snapshot"])
        with patch.object(journal,"get_capture_application",return_value=fake.application()),patch.object(journal,"_application_rpc",return_value=original),patch.object(journal,"get_capture_application_batch",return_value=original):
            recovered=journal.commit_capture_application_batch(seal["application_id"],original["step_id"],[group],obs,rev,watermark=seal["source_watermark"])
        self.assertEqual(recovered,original)
        self.assertEqual(recovered["groups"][0]["status"],"withheld")

    def test_stop_requires_allowlisted_reason_and_exact_fully_validated_original_terminal(self):
        fake=Pages();journal=port(fake)
        with patch.object(journal,"_application_rpc",return_value=fake.terminal),patch.object(journal,"get_capture_application",return_value=fake.application()):
            self.assertEqual(journal.stop_capture_application(fake.seal["application_id"],"complete",watermark="100"),fake.terminal)
        with self.assertRaises(ValueError):journal.stop_capture_application(fake.seal["application_id"],"SECRET",watermark="100")
        with patch.object(journal,"_application_rpc",return_value={"version":2}),patch.object(journal,"get_capture_application",return_value=fake.application()):
            with self.assertRaises(StoreError):journal.stop_capture_application(fake.seal["application_id"],"complete",watermark="100")

    def test_unknown_step_unregistered_application_and_invalid_read_arguments_are_not_success(self):
        fake=Pages();journal=port(fake)
        with self.assertRaises(ValueError):journal.get_capture_application_batch(fake.seal["application_id"],fingerprint("unknownstep"))
        for view,key in (("unknown",None),("batch",None),("groups",fingerprint("wrongkey"))):
            with self.assertRaises(ValueError):journal._application_rows(fake.seal["application_id"],view,key=key)
        with self.assertRaises(ValueError):journal._application_rows(fake.seal["application_id"],"groups",ledger_watermark="00")
        with self.assertRaises(ValueError):journal._application_rpc("unknown",{})
        with patch.object(journal,"get_capture_application",return_value=None):
            with self.assertRaises(ValueError):journal.commit_capture_application_batch(fake.seal["application_id"],fingerprint("step"),[],[],[],watermark="100")

    def test_job_invalid_attempts_budgets_and_cancellation_do_not_create_terminal_results(self):
        fake=Pages();journal=port(fake)
        for key,values in (("max_attempts",(True,False,0,6,2.0,"2")),
                           ("max_run_seconds",(True,False,0,-1,46,float("nan"),float("inf"),"45"))):
            for value in values:
                with self.subTest(key=key,value=value):
                    count=len(fake.calls)
                    with self.assertRaises(ValueError):job.register_capture_application(journal,fake.ctx["request"],None,**{key:value})
                    with self.assertRaises(ValueError):job.commit_capture_application_step(journal,fake.seal["application_id"],fake.seal["batches"][0]["step_id"],None,**{key:value})
                    self.assertEqual(len(fake.calls),count)
        class Cancelled(BaseException):pass
        fake.failure=Cancelled()
        with self.assertRaises(Cancelled):job.register_capture_application(journal,fake.ctx["request"],None)
        fake.failure=None
        def forbidden(*args):raise AssertionError("Original result recovery precedes source clock/raw")
        self.assertEqual(job.register_capture_application(journal,fake.ctx["request"],forbidden,clock=forbidden,max_attempts=5),fake.ack)
        self.assertEqual(job.commit_capture_application_step(journal,fake.seal["application_id"],fake.seal["batches"][0]["step_id"],forbidden,clock=forbidden,max_attempts=5),fake.batches[0]["result"])

    def test_scope_checks_precede_original_recovery_and_malformed_scope_precedes_backend(self):
        fake=Pages();journal=port(fake)
        aid,step=fake.seal["application_id"],fake.seal["batches"][0]["step_id"]
        for scope in ([fingerprint("destination"),"producer"],(),("bad","producer"),(fingerprint("destination"),"bad producer")):
            before=len(fake.calls)
            with self.subTest(scope=scope),self.assertRaises(ValueError):
                job.commit_capture_application_step(journal,aid,step,None,expected_scope=scope)
            self.assertEqual(len(fake.calls),before)
        with self.assertRaises(job.CaptureApplicationScopeMismatch):
            job.commit_capture_application_step(journal,aid,step,None,expected_scope=(fingerprint("other-destination"),"other-producer"))
        original=fake.ctx["request"]["capture_request"]
        self.assertEqual(job.commit_capture_application_step(journal,aid,step,None,
            expected_scope=(original["destination_id"],original["producer_id"])),fake.batches[0]["result"])

    def test_job_unknown_out_of_order_and_existing_legacy_owner_are_not_retried(self):
        fake=Pages(complete=False);journal=port(fake);aid=fake.seal["application_id"]
        with patch.object(journal,"get_capture_application",return_value=None):
            with self.assertRaisesRegex(ValueError,"not_registered"):
                job.commit_capture_application_step(journal,aid,fake.seal["batches"][0]["step_id"],None)
        with self.assertRaisesRegex(ValueError,"not_in_seal"):
            job.commit_capture_application_step(journal,aid,fingerprint("unknown"),None)
        with self.assertRaisesRegex(JournalConflict,"out_of_order"):
            job.commit_capture_application_step(journal,aid,fake.seal["batches"][1]["step_id"],None)
        with patch.object(journal,"get_capture_application",return_value=None),patch.object(journal,"get_capture_delivery",return_value={"fixture":"trusted-port-known-legacy-original"}):
            with self.assertRaisesRegex(JournalConflict,"legacy_original_exists"):
                job.register_capture_application(journal,fake.ctx["request"],None)
