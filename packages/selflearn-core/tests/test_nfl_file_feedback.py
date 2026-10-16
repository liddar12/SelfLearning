"""Complete repository feedback rehearsals; no database or hosted ACK claims."""
import copy
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from selflearn_core.adapters import nfl_file_feedback as F
from selflearn_core.probability_receipts import fingerprint

import test_nfl_capture_evidence as E
import test_nfl_capture_labels as L

PREGAME="2026-10-17T15:00:00Z"
POSTGAME="2026-10-20T15:00:00Z"
EVALUATED="2026-10-20T15:00:01Z"


def graph(journal=None):
    journal=journal or L.journal_fixture()
    docs=[r["payload"]for r in journal.snapshot["generation_records"]]
    refs={oid for doc in docs for oid in doc["generations"][0]["source_snapshots"].values()}
    return {"source_objects":[{"version":1,"kind":"source_snapshot","object_id":oid,
                "payload":journal.objects[oid]["payload"]}for oid in sorted(refs)],
            "generations":[{"request":{"version":1,"kind":"generation","object_id":doc["envelope_id"],"payload":doc},
                "expected_receipt_ids":sorted(r["receipt_id"]for r in doc["receipts"])}for doc in docs]}


def capture(value=80,*,day=19):
    envelope,objects=E.fixture(stats=E.STATS.replace(b",,,NA",(",,,"+str(value)).encode()))
    if day!=19:
        for row in envelope["observations"].values():
            for name in("fetch_started_utc","observed_utc"):row[name]=row[name].replace("10-19",f"10-{day:02d}")
        for name in("started_utc","completed_utc"):envelope["manifest"][name]=envelope["manifest"][name].replace("10-19",f"10-{day:02d}")
        E.reseal(envelope)
    return envelope,lambda key:E.stream(objects[key])


def reseal(state):
    state["state_id"]=fingerprint({k:v for k,v in state.items()if k!="state_id"})
    return state


class FileFeedbackTests(unittest.TestCase):
    def test_forecast_raw_grading_evaluation_and_repeat_preserve_original_clocks(self):
        source=graph();before=copy.deepcopy(source)
        first=F.prepare(source,observed_utc=PREGAME)
        self.assertTrue(first["changed"])
        state=first["state"]
        self.assertEqual((len(state["receipts"]),len(state["generation_records"])),(4,2))
        graded=F.prepare(source,previous=state,captures=[capture()],observed_utc=POSTGAME)
        self.assertEqual(len(graded["state"]["revisions"]),4)
        self.assertEqual([r["resolution"]["hit"]for r in graded["state"]["revisions"]],[True]*4)
        self.assertEqual(graded["state"]["source_imports"],state["source_imports"])
        self.assertEqual(graded["state"]["receipt_imports"],state["receipt_imports"])
        repeated=F.prepare(source,previous=graded["state"],captures=[capture()],observed_utc="2026-10-21T15:00:00Z")
        self.assertFalse(repeated["changed"])
        self.assertEqual(repeated["state"],graded["state"])
        self.assertEqual(repeated["capture_results"][0]["new_revisions"],0)
        result=F.evaluate(repeated["state"],source,as_of_utc=EVALUATED)
        self.assertEqual(result["report"]["cohort_n"],2)
        self.assertEqual((result["first_imported_before_kickoff"],result["late_imported_receipts"]),(4,0))
        self.assertEqual(F.plain(result),result)
        self.assertEqual(result,F.evaluate(repeated["state"],source,as_of_utc=EVALUATED))
        for key in F.FLAGS:self.assertIs(result[key],False)
        self.assertEqual(source,before)

    def test_newer_measurement_correction_old_retry_and_new_a_b_a_are_distinct(self):
        source=graph();a=F.prepare(source,captures=[capture()],observed_utc=POSTGAME)["state"]
        b=F.prepare(source,previous=a,captures=[capture(20,day=20)],observed_utc="2026-10-21T15:00:00Z")["state"]
        self.assertEqual(len(b["revisions"]),8)
        self.assertEqual(b["revisions"][:4],a["revisions"])
        self.assertEqual([r["resolution"]["hit"]for r in b["revisions"][4:]],[False]*4)
        retry=F.prepare(source,previous=b,captures=[capture()],observed_utc="2026-10-22T15:00:00Z")
        self.assertFalse(retry["changed"])
        self.assertEqual(retry["state"],b)
        again=F.prepare(source,previous=b,captures=[capture(80,day=21)],observed_utc="2026-10-22T15:00:00Z")["state"]
        self.assertEqual(len(again["revisions"]),12)
        self.assertEqual([r["resolution"]["hit"]for r in again["revisions"][8:]],[True]*4)
        self.assertTrue(all(r["supersedes"]is not None for r in again["revisions"][4:]))

    def test_same_capture_rebinds_expanded_membership_not_capture_only_cache(self):
        small=graph(L.journal_fixture(vintages=1));large=graph()
        first=F.prepare(small,captures=[capture()],observed_utc=POSTGAME)["state"]
        second=F.prepare(large,previous=first,captures=[capture()],observed_utc="2026-10-21T15:00:00Z")
        self.assertEqual((len(second["state"]["receipts"]),len(second["state"]["revisions"])),(4,4))
        self.assertEqual(second["capture_results"][0]["new_revisions"],2)
        self.assertEqual(len(second["state"]["capture_records"]),2)
        self.assertEqual(first["capture_records"][0]["capture_id"],second["state"]["capture_records"][1]["capture_id"])
        self.assertNotEqual(first["capture_records"][0]["membership_id"],second["state"]["capture_records"][1]["membership_id"])

    def test_missing_stat_is_withheld_not_zero_and_late_imports_not_prospective(self):
        source=graph();prepared=F.prepare(source,captures=[capture("NA")],observed_utc=POSTGAME)
        self.assertEqual(prepared["state"]["revisions"],[])
        self.assertEqual(prepared["capture_results"][0]["withheld"],{"missing_stat_value":4})
        result=F.evaluate(prepared["state"],source,as_of_utc=EVALUATED)
        self.assertEqual((result["first_imported_before_kickoff"],result["late_imported_receipts"]),(0,4))

    def test_evaluate_refuses_missing_extra_or_changed_graph_before_fitting(self):
        source=graph();state=F.prepare(source,observed_utc=PREGAME)["state"]
        for change in("missing_generation","missing_source","extra","tampered_source"):
            bad=copy.deepcopy(source)
            if change=="missing_generation":bad["generations"].pop()
            elif change=="missing_source":bad["source_objects"].pop()
            elif change=="extra":bad["source_objects"].append({"version":1,"kind":"source_snapshot","object_id":fingerprint({"extra":1}),"payload":{"extra":1}})
            else:bad["source_objects"][0]["payload"]={"tampered":1}
            with self.subTest(change=change),mock.patch.object(F.calibration,"walk_forward_report")as fitter:
                with self.assertRaises(ValueError):F.evaluate(state,bad,as_of_utc=EVALUATED)
                fitter.assert_not_called()

    def test_hashed_state_alone_cannot_hide_bad_import_clocks_or_missing_partition(self):
        source=graph();state=F.prepare(source,observed_utc=PREGAME)["state"]
        for change in("receipt_clock","source_clock","missing_source_import","missing_receipt_import","missing_partition","future_generation","hash"):
            bad=copy.deepcopy(state)
            if change=="receipt_clock":bad["receipt_imports"][0]["first_imported_utc"]="2026-10-16T00:00:00Z"
            elif change=="source_clock":bad["source_imports"][0]["first_imported_utc"]="2026-10-17T16:00:00Z"
            elif change=="missing_source_import":bad["source_imports"].pop()
            elif change=="missing_receipt_import":bad["receipt_imports"].pop()
            elif change=="missing_partition":bad["generation_records"].pop()
            elif change=="future_generation":bad["generation_records"][0]["recorded_utc"]="2026-10-16T00:00:00Z"
            else:bad["sequence"]+=1
            if change!="hash":reseal(bad)
            with self.subTest(change=change),self.assertRaises((ValueError,KeyError)):F.prepare(source,previous=bad,observed_utc=POSTGAME)

    def test_bad_raw_capture_is_atomic_and_future_capture_rejected(self):
        source=graph();state=F.prepare(source,observed_utc=PREGAME)["state"];before=copy.deepcopy(state)
        envelope,reader=capture()
        for captures,clock in(([(envelope,lambda key:iter([b"altered"]))],POSTGAME),([(envelope,reader)],PREGAME)):
            with self.assertRaises(ValueError):F.prepare(source,previous=state,captures=captures,observed_utc=clock)
            self.assertEqual(state,before)

    def test_strict_json_and_work_limits(self):
        for bad in(float("nan"),float("inf"),1<<4097,(1,),{1:1},object()):
            with self.subTest(kind=type(bad)),self.assertRaises(ValueError):F.plain(bad)
        cycle=[];cycle.append(cycle)
        with self.assertRaises(ValueError):F.plain(cycle)
        for captures in(iter([]),[(None,None)],[(None,lambda key:())]*33):
            with self.assertRaises(ValueError):F.prepare(graph(),captures=captures,observed_utc=PREGAME)
        with self.assertRaisesRegex(ValueError,"clock_regressed"):
            F.prepare(graph(),previous=F.prepare(graph(),observed_utc=PREGAME)["state"],observed_utc="2026-10-16T00:00:00Z")
        for replicates in(True,199,2001):
            with self.assertRaises(ValueError):F.evaluate(F.prepare(graph(),observed_utc=PREGAME)["state"],graph(),as_of_utc=EVALUATED,bootstrap_replicates=replicates)


@unittest.skipUnless(os.environ.get("NFL2026_CHECKOUT"),"Set NFL2026_CHECKOUT for actual producer feedback rehearsal")
class ActualProducerFileFeedbackTests(unittest.TestCase):
    def test_actual_nfl_receipts_raw_outcomes_and_shared_cohort(self):
        checkout=Path(os.environ["NFL2026_CHECKOUT"])
        self.assertTrue((checkout/"tests/fixtures/week6_outcomes.py").is_file())
        script=r'''
import base64,importlib.util,json,tempfile
from pathlib import Path
spec=importlib.util.spec_from_file_location('fixture',Path('tests/fixtures/week6_outcomes.py'))
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
with tempfile.TemporaryDirectory() as directory:
 root=Path(directory);f.generation(root);f.generation(root,'2026-09-01T13:00:00Z');plan=f.plan(root);bundle=f.bundle()
 print(json.dumps({'graph':{k:plan[k]for k in('source_objects','generations')},'manifest':bundle['manifest'],
  'observations':bundle['observations'],'raw':{key:base64.b64encode(raw).decode()for key,raw in bundle['raw_objects'].items()}}))
'''
        run=subprocess.run([sys.executable,"-c",script],cwd=checkout,text=True,capture_output=True,timeout=30)
        self.assertEqual(run.returncode,0,"Actual local producer fixture failed")
        payload=json.loads(run.stdout);raw={key:base64.b64decode(value)for key,value in payload["raw"].items()}
        envelope={"version":1,"kind":"nfl.capture_envelope.v1","scope":"uploaded_only","manifest":payload["manifest"],
            "observations":payload["observations"],"raw_objects":[{"raw_sha256":key,"raw_bytes":len(value)}for key,value in sorted(raw.items())]}
        first=F.prepare(payload["graph"],observed_utc="2026-09-01T15:00:00Z")["state"]
        second=F.prepare(payload["graph"],previous=first,captures=[(envelope,lambda key:E.stream(raw[key]))],observed_utc="2026-09-04T15:00:00Z")["state"]
        report=F.evaluate(second,payload["graph"],as_of_utc="2026-09-04T15:00:01Z")
        self.assertEqual((len(second["receipts"]),len(second["revisions"])),(4,4))
        self.assertEqual(report["report"]["cohort_n"],2)  # canonical contracts, not four duplicated vintages
        self.assertEqual(report["first_imported_before_kickoff"],4)
        self.assertFalse(report["report"]["proposed_shared_return_gate"]["passed"])
        self.assertFalse(report["model_adoption"])


if __name__=="__main__":unittest.main()
