"""Actual local NFL producer/grader vs engine, on synthetic pinned evidence.

No historical replay, fitting, production writes, cloud or database-fence claim.
Run with NFL2026_CHECKOUT for actual cross-repository acceptance.
"""
import base64
import copy
import datetime as dt
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from selflearn_core.adapters.nfl import adapt_prospective
from selflearn_core.adapters.nfl_observed_resolution import resolve_observed, observation_record
from selflearn_core.store.receipt_journal import ReceiptJournal
from selflearn_core.probability_receipts import fingerprint
from tools import compare_nfl_cohort as parity

CHECKOUT = os.environ.get("NFL2026_CHECKOUT")
AVAILABLE = bool(CHECKOUT and (Path(CHECKOUT) / "scripts/pinned_cohort_oracle.py").is_file())
SCRIPT = r'''
import base64,importlib.util,json,tempfile
from pathlib import Path
from scripts import build_learning_outcomes as p
spec=importlib.util.spec_from_file_location('f',Path('tests/fixtures/week6_outcomes.py'))
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
with tempfile.TemporaryDirectory() as directory:
 f.generation(Path(directory));f.generation(Path(directory),'2026-09-01T13:00:00Z')
 plan=f.plan(Path(directory))
 captures=[f.bundle(value='80'),f.bundle(value='30',observed='2026-09-04T12:00:00Z'),
  f.bundle(value='80',observed='2026-09-05T12:00:00Z'),
  f.bundle(stats_rows=[f.stats_row('5',player_display_name='Other Person')],
   snap_rows=[f.snap_row(player='Other Person')],observed='2026-09-06T12:00:00Z'),
  f.bundle(value='30',observed='2026-09-03T12:00:00Z')]
 wire=[{'manifest':c['manifest'],'observations':c['observations'],
  'raw':{k:base64.b64encode(v).decode() for k,v in c['raw_objects'].items()}} for c in captures]
 print(json.dumps({'plan':plan,'captures':wire,'batches':[p.build(plan,c) for c in captures]}))
'''


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


@unittest.skipUnless(AVAILABLE, "Explicit NFL2026_CHECKOUT required for actual producer/grader parity")
class NFLCohortParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run = subprocess.run([sys.executable, "-c", SCRIPT], cwd=CHECKOUT, capture_output=True, text=True, timeout=30)
        if run.returncode:
            raise AssertionError("Actual local NFL fixture failed")
        cls.producer = json.loads(run.stdout)
        cls.snapshots = []
        cls.base = {"version": 1, "export_plan": cls.producer["plan"], "captures": cls.producer["captures"], "population": "eligible_pool"}
        with ReceiptJournal(":memory:") as journal:
            docs = [i["request"]["payload"] for i in cls.producer["plan"]["generations"]]
            adapted = adapt_prospective(journal_docs=docs)
            if adapted["rejections"]:
                raise AssertionError("Original NFL fixture has rejected forecasts")
            for obj in cls.producer["plan"]["source_objects"]:
                journal.append_object(obj["kind"], obj["object_id"], obj["payload"])
            for doc in docs:
                journal.append_object("generation", doc["envelope_id"], doc)
                sids = {r["receipt_id"] for r in doc["receipts"]}
                journal.commit_generation(doc["envelope_id"], [r for r in adapted["receipts"] if r["source_receipt_id"] in sids], [])
            observed = {}
            for batch in cls.producer["batches"][:4]:
                history = [r for row in journal.receipts() for r in journal.resolution_history(row["receipt_id"])]
                result = resolve_observed(journal.receipts(), batch["source_receipts"], batch["observed_legs"], batch["game_observations"],
                    observed_utc=stamp(), existing_revisions=history, existing_observations=list(observed.values()))
                if result["rejections"]:
                    raise AssertionError("Actual producer evidence rejected by engine")
                for row in result["proposals"]:
                    journal.append_resolution(**row)
                observed.update({row["observation_id"]: row for row in result["observation_proposals"]})
                cls.snapshots.append({"version": 1, "watermark": None, "receipts": journal.receipts(),
                    "generation_records": journal.generations(), "revisions": [r for row in journal.receipts() for r in journal.resolution_history(row["receipt_id"])]})
        cls.cutoff = stamp()

    def bundle(self, index=0):
        return {**copy.deepcopy(self.base), "snapshot": copy.deepcopy(self.snapshots[index]), "as_of_utc": self.cutoff}

    def compare(self, value):
        return parity.compare(value, nfl_checkout=CHECKOUT)

    def test_exact_original_ids_contracts_revisions_and_independent_numeric_oracles(self):
        report = self.compare(self.bundle())
        self.assertTrue(report["parity"], report)
        self.assertEqual(report["n"], 2)
        self.assertEqual(report["first_eligible_forecasts"], 2)
        self.assertEqual(report["nfl_rows"], report["engine_rows"])
        self.assertEqual(report["pin_basis"], "local_frozen_content_copy_not_database_fence")
        ps = [row["served_prob"] for row in report["nfl_rows"]]
        self.assertAlmostEqual(report["nfl_scalars"]["brier"], sum((p-1)**2 for p in ps)/2)
        self.assertAlmostEqual(report["nfl_scalars"]["log_loss"], sum(-math.log(p) for p in ps)/2)
        self.assertTrue(all(row["revision_id"].startswith("nfl.observed_resolution_v2:") for row in report["nfl_rows"]))
        self.assertNotIn("candidate", report)

    def test_duplicates_permutations_and_later_vintages_do_not_multiply_population(self):
        value = self.bundle()
        value["snapshot"]["receipts"] = list(reversed(value["snapshot"]["receipts"])) * 2
        value["snapshot"]["revisions"].reverse()
        report = self.compare(value)
        self.assertTrue(report["parity"], report)
        self.assertEqual((report["n"], report["first_eligible_forecasts"]), (2, 2))
        self.assertEqual(report["nfl_excluded"]["duplicate_receipt_copy"], 4)

    def test_real_correction_changes_original_revision_set_but_not_probability_or_receipts(self):
        initial, corrected, restored = [self.compare(self.bundle(i)) for i in range(3)]
        self.assertTrue(all(r["parity"] for r in (initial, corrected, restored)))
        self.assertEqual([r["served_prob"] for r in initial["nfl_rows"]], [r["served_prob"] for r in corrected["nfl_rows"]])
        self.assertTrue(all(not r["hit"] for r in corrected["nfl_rows"]))
        self.assertTrue(all(r["hit"] for r in restored["nfl_rows"]))
        self.assertNotEqual(initial["nfl_rows"][0]["revision_hash"], restored["nfl_rows"][0]["revision_hash"])
        # A pinned earlier cutoff ignores genuine later database-recorded corrections.
        value = self.bundle(2)
        correction_times = [r["recorded_utc"] for r in value["snapshot"]["revisions"] if r["supersedes"] is not None]
        value["as_of_utc"] = min(correction_times)
        pinned = self.compare(value)
        self.assertTrue(pinned["parity"], pinned)
        self.assertEqual(pinned["nfl_rows"], initial["nfl_rows"])

    def test_unresolved_first_and_void_are_unavailable_not_zero_or_later_substitution(self):
        value = self.bundle()
        earliest = {r["receipt_id"] for r in value["snapshot"]["receipts"] if r["forecast_created_utc"] == "2026-09-01T12:00:00Z"}
        value["snapshot"]["revisions"] = [r for r in value["snapshot"]["revisions"] if r["resolution"]["receipt_id"] not in earliest]
        result = self.compare(value)
        self.assertFalse(result["parity"])
        self.assertEqual(result["n"], 0)
        self.assertEqual(result["nfl_excluded"]["unresolved_at_cutoff"], 2)
        self.assertIsNone(result["nfl_scalars"]["brier"])
        void = self.compare(self.bundle(3))
        self.assertFalse(void["parity"])
        self.assertEqual(void["nfl_excluded"]["non_binary_latest_revision"], 2)
        self.assertEqual(void["engine_excluded"]["non_binary_latest_revision"], 2)

    def test_missing_raw_cannot_be_replaced_by_client_normalized_label(self):
        value = self.bundle()
        value["captures"] = []
        report = self.compare(value)
        self.assertFalse(report["parity"])
        self.assertIn("missing_original_raw_evidence", report["nfl_excluded"])
        value = self.bundle()
        capture = value["captures"][0]
        oid = next(iter(capture["raw"]))
        capture["raw"][oid] = base64.b64encode(b"valid replacement bytes under an OLD content hash").decode()
        with self.assertRaisesRegex(ValueError, "bounded_nfl_oracle_failed"):
            self.compare(value)

    def test_wrong_contract_raw_actual_and_fork_never_report_parity(self):
        value = self.bundle()
        value["snapshot"]["revisions"][0]["resolution"]["actual"] = 999.
        report = self.compare(value)
        self.assertFalse(report["parity"])
        self.assertIn("raw_grade_revision_mismatch", report["nfl_excluded"])
        value = self.bundle()
        value["snapshot"]["revisions"][0]["resolution"]["contract_id"] = "sha256:" + "0"*64
        with self.assertRaises(ValueError):self.compare(value)
        value = self.bundle(1)
        value["snapshot"]["revisions"][1]["supersedes"] = None
        self.assertFalse(self.compare(value)["parity"])

    def test_unknown_malformed_or_global_conflicting_revision_is_not_silently_ignored(self):
        for change in ("foreign", "malformed", "cross_receipt_id", "conflicting_copy"):
            with self.subTest(change=change):
                value=self.bundle()
                extra=copy.deepcopy(value["snapshot"]["revisions"][0])
                if change=="foreign":extra["resolution"]["receipt_id"]="sha256:"+"f"*64
                if change=="malformed":extra={"revision_id":"malformed","resolution":[]}
                if change=="cross_receipt_id":
                    extra=copy.deepcopy(value["snapshot"]["revisions"][1]);extra["revision_id"]=value["snapshot"]["revisions"][0]["revision_id"]
                if change=="conflicting_copy":extra["reason"]="changed immutable copy"
                value["snapshot"]["revisions"].append(extra)
                with self.assertRaises(ValueError):self.compare(value)

    def test_actual_same_clock_changed_raw_measurement_is_not_legal_correction_parity(self):
        value=self.bundle()
        batch=self.producer["batches"][4]
        source=value["snapshot"]["receipts"]
        isolated=resolve_observed(source,batch["source_receipts"],batch["observed_legs"],batch["game_observations"],observed_utc=stamp())
        self.assertEqual(isolated["rejections"],[])
        rejected=resolve_observed(source,batch["source_receipts"],batch["observed_legs"],batch["game_observations"],
            observed_utc=stamp(),existing_revisions=value["snapshot"]["revisions"],
            existing_observations=[observation_record(r["resolution"]) for r in value["snapshot"]["revisions"]])
        self.assertEqual(rejected["proposals"],[],"The actual D1 kernel rejects this same-clock contradictory raw capture")
        proposals={p["resolution"]["receipt_id"]:p for p in isolated["proposals"]}
        forged=[]
        for parent in value["snapshot"]["revisions"]:
            rid=parent["resolution"]["receipt_id"]
            node={**copy.deepcopy(parent),**copy.deepcopy(proposals[rid]),"supersedes":parent["revision_id"],"reason":"deliberately illegal same-clock fixture"}
            node["revision_id"]="nfl.observed_resolution_v2:"+fingerprint({"receipt_id":rid,
                "observation_id":node["resolution"]["evidence_observation_id"],"supersedes":parent["revision_id"]})
            forged.append(node)
        value["snapshot"]["revisions"].extend(forged)
        self.assertFalse(self.compare(value)["parity"],"Two agreeing evaluators must not certify an illegal observed-v2 correction chain")

    def test_unsupported_population_empty_and_invalid_cli_inputs_are_explicit(self):
        value = self.bundle()
        value["population"] = "offered_my"
        result = self.compare(value)
        self.assertFalse(result["parity"])
        self.assertEqual(result["status"], "unsupported_population")
        value = self.bundle()
        value["snapshot"]["receipts"] = []
        value["snapshot"]["generation_records"] = []
        value["snapshot"]["revisions"] = []
        self.assertFalse(self.compare(value)["parity"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"bundle.json"
            path.write_text(json.dumps(self.bundle()))
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(parity.main(["--bundle", str(path), "--nfl-checkout", CHECKOUT]), 0)
            self.assertTrue(json.loads(output.getvalue())["parity"])
            path.write_text('{"SECRET":1,"SECRET":2}')
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(parity.main(["--bundle", str(path), "--nfl-checkout", CHECKOUT]), 2)
            self.assertNotIn("SECRET", output.getvalue())

    def test_nfl_oracle_never_imports_engine_cohort_selector(self):
        source = (Path(CHECKOUT)/"scripts/pinned_cohort_oracle.py").read_text()
        self.assertNotIn("selflearn_core", source)
        self.assertNotIn("build_cohort(", source)

    def test_original_graph_commit_mapping_and_first_capture_cannot_be_forged(self):
        mutations = [
            lambda v: v["snapshot"]["generation_records"].clear(),
            lambda v: v["snapshot"]["generation_records"][0]["ack"].update(durable=False),
            lambda v: v["snapshot"]["generation_records"][0]["ack"]["receipts"][0].update(receipt_id=fingerprint("wrong")),
            lambda v: v["snapshot"]["generation_records"][0]["ack"]["receipts"].pop(),
            lambda v: v["snapshot"]["generation_records"][0].update(recorded_utc="2026-01-01T00:00:00Z"),
            lambda v: v["export_plan"]["source_objects"][0].update(object_id=fingerprint("wrong")),
            lambda v: v["snapshot"]["receipts"][0].update(first_seen_utc="2026-09-01T23:00:00Z"),
        ]
        for change in mutations:
            with self.subTest(change=mutations.index(change)):
                value = self.bundle(); change(value)
                with self.assertRaises(ValueError):
                    self.compare(value)

    def test_raw_component_measurement_revision_and_time_tampering_are_not_parity(self):
        changes = [
            lambda r: r.update(revision_id="invented"),
            lambda r: r.update(version=True),
            lambda r: r["resolution"].update(evidence_basis="invented"),
            lambda r: r["resolution"].update(hit=1),
            lambda r: r["resolution"].update(authoritative=False),
            lambda r: r["resolution"].update(source_updated_utc="2026-09-03T12:00:00Z"),
            lambda r: r["resolution"]["measurement"].update(week=5),
            lambda r: r["resolution"]["source_observations"]["espn.scoreboard"].update(observation_id=fingerprint("wrong")),
            lambda r: r["resolution"].update(evidence_observation_id=fingerprint("wrong")),
            lambda r: r["resolution"].update(resolved_utc="2026-09-01T01:00:00Z"),
        ]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                value = self.bundle(); change(value["snapshot"]["revisions"][0])
                if changes.index(change)==1:  # Invalid wrapper is global, never a silent row exclusion.
                    with self.assertRaises(ValueError):self.compare(value)
                else:
                    self.assertFalse(self.compare(value)["parity"])

    def test_local_bounds_missing_checkout_and_cli_hold_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "missing_trusted_local_nfl_oracle"):
                parity.compare(self.bundle(), nfl_checkout=directory)
            with patch.object(parity, "MAX_INPUT", 1):
                with self.assertRaisesRegex(ValueError, "analysis_input_too_large"):
                    self.compare(self.bundle())
            with patch.object(parity, "MAX_OUTPUT", 1):
                with self.assertRaisesRegex(ValueError, "bounded_nfl_oracle_failed"):
                    self.compare(self.bundle())
            with patch.object(parity, "TIMEOUT", 0):
                with self.assertRaisesRegex(ValueError, "bounded_nfl_oracle_failed"):
                    self.compare(self.bundle())
            path = Path(directory)/"bundle.json"
            value = self.bundle();value["population"] = "offered_my"
            path.write_text(json.dumps(value))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(parity.main(["--bundle", str(path), "--nfl-checkout", CHECKOUT]), 3)
            for change in (
                lambda v: v.update(version=True),
                lambda v: v["snapshot"].update(version=True),
                lambda v: v["snapshot"].update(watermark="00"),
                lambda v: v["snapshot"].update(generation_records=[{}]*257),
                lambda v: v.update(captures=[{}]*21),
            ):
                value = self.bundle(); change(value)
                path.write_text(json.dumps(value))
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(parity.main(["--bundle", str(path), "--nfl-checkout", CHECKOUT]), 2)


    def test_independent_nfl_oracle_validation_matrix_and_actual_trace(self):
        # Isolate NFL imports; instrument the REAL oracle, not copied helpers.
        script = r'''
import copy,importlib.util,io,json,sys,trace
from pathlib import Path
path=str(Path('scripts/pinned_cohort_oracle.py').resolve())
class TargetTrace(trace.Trace):
 def globaltrace_lt(self,frame,why,arg):
  return self.localtrace if why=='call' and frame.f_code.co_filename==path else None
t=TargetTrace(count=True,trace=False)
spec=importlib.util.spec_from_file_location('real_oracle',path)
o=importlib.util.module_from_spec(spec)
t.runfunc(spec.loader.exec_module,o)
values=json.load(sys.stdin)
def call(v):
 try:return t.runfunc(o.oracle,v)
 except (ValueError,TypeError,KeyError,OverflowError,AttributeError):return None
for v in values: assert call(v)['supported'] is True
base=values[0]
v=copy.deepcopy(base);v['snapshot']['generation_records'].append(copy.deepcopy(v['snapshot']['generation_records'][0]))
assert call(v)['n']==2
v['snapshot']['generation_records'][-1]['recorded_utc']='2099-01-01T00:00:00Z'
assert call(v) is None
v=copy.deepcopy(base);v['as_of_utc']='2026-09-01T12:00:00Z'
assert call(v)['excluded']['future_capture']==4
v=copy.deepcopy(base);v['snapshot']['revisions']=[]
assert call(v)['excluded']['unresolved_at_cutoff']==2
v=copy.deepcopy(base);v['population']='offered_my'
assert call(v)['supported'] is False
v=copy.deepcopy(base);v['snapshot']['receipts']*=2
assert call(v)['excluded']['duplicate_receipt_copy']==4
changes=[
 lambda v:v.update(version=True),
 lambda v:v['snapshot'].update(version=True),
 lambda v:v['snapshot'].update(watermark='00'),
 lambda v:v['snapshot'].update(receipts=[{}]*5001),
 lambda v:v.update(captures=[{}]*21),
 lambda v:v['export_plan'].update(generations=[]),
 lambda v:v['captures'][0].update(raw={'bad':False}),
 lambda v:v['captures'][0].update(raw={'bad':'!!!!'}),
 lambda v:v['captures'][0].update(extra=True),
 lambda v:v['snapshot']['receipts'][0].update(version=True),
 lambda v:v['snapshot']['receipts'][0].update(source_receipt_id='bad'),
 lambda v:v['snapshot']['generation_records'][0]['ack'].update(durable=False),
 lambda v:v['snapshot']['generation_records'][0]['ack']['receipts'][0].update(status='bad'),
 lambda v:v['snapshot']['generation_records'][0]['ack']['receipts'].pop(),
 lambda v:v['snapshot']['generation_records'][0].update(recorded_utc='2026-01-01T00:00:00Z'),
 lambda v:v['snapshot']['generation_records'].clear(),
 lambda v:v['snapshot']['revisions'][0].update(version=True),
 lambda v:v['snapshot']['revisions'][0]['resolution'].update(actual=999),
 lambda v:v['snapshot']['revisions'][0]['resolution'].update(authoritative=False),
 lambda v:v['snapshot']['revisions'][0]['resolution'].update(source='bad'),
 lambda v:v['snapshot']['revisions'][0]['resolution'].update(source_observations={}),
 lambda v:v['snapshot']['revisions'][0]['resolution'].update(measurement_id='bad'),
 lambda v:v['snapshot']['revisions'][0]['resolution']['source_observations']['espn.scoreboard'].update(observed_utc='2099-01-01T00:00:00Z'),
]
for mutate in changes:
 v=copy.deepcopy(base);mutate(v);result=call(v)
 assert result is None or result.get('excluded'),mutate
entries=o.producer._entries(base['export_plan'],2026,{6})[0]
for field,value in [('base_prob',.123),('event_start_utc','2026-01-01T00:00:00Z'),('event_id','wrong'),('capture_provenance',{})]:
 r=copy.deepcopy(base['snapshot']['receipts'][0]);r[field]=value
 r['receipt_id']=o.lr.fingerprint({k:v for k,v in r.items() if k!='receipt_id'})
 try:t.runfunc(o._receipt,r,entries)
 except ValueError:pass
 else:raise AssertionError(field)
old=o.MAX_RAW;o.MAX_RAW=1
assert call(base) is None
o.MAX_RAW=old
old=o.MAX_INPUT;o.MAX_INPUT=1
assert call(base) is None
o.MAX_INPUT=old
oldstdin,oldstdout=sys.stdin,sys.stdout
for text,expected in [(json.dumps(base),0),('{"SECRET":1,"SECRET":2}',2)]:
 sys.stdin=io.TextIOWrapper(io.BytesIO(text.encode()));sys.stdout=io.StringIO()
 code=t.runfunc(o.main);assert code==expected
 sys.stdin,sys.stdout=oldstdin,oldstdout
executable={n for n in trace._find_executable_linenos(path) if n>0}
observed={line for (filename,line),count in t.results().counts.items() if filename==path and count>0 and line>0}
denominator=executable|observed
print(json.dumps({'covered':len(observed),'total':len(denominator),'percent':100*len(observed)/len(denominator),
 'missing':sorted(denominator-observed)}))
'''
        values = [self.bundle(i) for i in range(4)]
        run = subprocess.run([sys.executable, "-c", script], cwd=CHECKOUT,
            input=json.dumps(values), capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        coverage = json.loads(run.stdout)
        self.assertGreaterEqual(coverage["percent"], 90, coverage)


if __name__ == "__main__":
    unittest.main()
