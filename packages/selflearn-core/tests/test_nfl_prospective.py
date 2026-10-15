"""Prospective capture, artifact replay, and opt-in actual NFL producer parity."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from selflearn_core.adapters.nfl import adapt_prospective
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.receipt_journal import ReceiptJournal
from test_nfl_adapter import fixture, refresh_receipt

OBSERVED = "2026-10-18T17:01:00Z"


def inputs(**kwargs):
    doc, scores, games = fixture(**kwargs)
    games["g1"].update(status="STATUS_SCHEDULED", season=2026, week=6)
    leg = doc["cards"][0]["legs"][0]
    pool = {"season": 2026, "week": 6, "generated_utc": leg["learning_receipt"]["forecast_created_utc"],
            "base_model_version": leg["learning_receipt"]["base_model_version"],
            "players": [{k: v for k, v in leg.items() if k in ("game_id", "gsis_id", "market", "side")} ]}
    pool["players"][0]["rungs"] = [{k: copy.deepcopy(leg[k]) for k in ("line", "model_prob", "learning_receipt", "selection")}]
    return doc, pool, games


def journal_document(pool, games, recorded=OBSERVED):
    player = pool["players"][0]
    rung = player["rungs"][0]
    source = rung["learning_receipt"]
    manifest = {"version": 1, "season": pool["season"], "week": pool["week"],
                "pool_generated_utc": pool["generated_utc"],
                "source_snapshots": {k: fingerprint(k) for k in ("leg_pool", "schedule", "parlay_calibration")},
                "offered_cards": [], "selection_policy_version": fingerprint("selection"),
                "joint_model_version": fingerprint("joint")}
    gid = fingerprint(manifest)
    row = {"receipt_id": source["receipt_id"], "learning_receipt": copy.deepcopy(source),
           "leg": {k: v for k, v in {**player, **rung}.items() if k not in ("rungs", "learning_receipt")},
           "kickoff_utc": games["g1"]["kickoff_utc"], "recorded_utc": recorded,
           "generation_id": gid, "publication_status": "generated_unverified"}
    generation = {**manifest, "generation_id": gid, "recorded_utc": recorded,
                  "receipt_ids": [source["receipt_id"]], "publication_status": "generated_unverified",
                  "population": "eligible_pool_prop_rungs_before_display_dedupe"}
    doc = {"version": 1, "season": 2026, "week": 6, "receipts": [row], "generations": [generation]}
    return rehash(doc)


def rehash(doc):
    doc["envelope_id"] = fingerprint({k: v for k, v in doc.items() if k != "envelope_id"})
    return doc


class NFLProspectiveTest(unittest.TestCase):
    def test_unresolved_pool_and_cards_are_captured_without_outcomes_or_public_claim(self):
        doc, pool, games = inputs(correction={"a": -.7, "b": .7})
        original = copy.deepcopy([doc, pool, games])
        doc["cards"][0]["locked"] = False
        batch = adapt_prospective([doc], [pool], games, observed_utc=OBSERVED)
        self.assertEqual(batch["rejections"], [])
        self.assertEqual(len(batch["receipts"]), 1)
        self.assertEqual(batch["summary"]["duplicate_copies"], 1)
        self.assertEqual(batch["outcomes"], [])
        self.assertEqual(batch["resolutions"], [])
        self.assertEqual(batch["receipts"][0]["served_prob"], pool["players"][0]["rungs"][0]["model_prob"])
        self.assertEqual(batch["receipts"][0]["publication_status"], "generated_unverified")
        doc["cards"][0]["locked"] = True
        self.assertEqual([doc, pool, games], original)

    def test_later_vintages_are_retained_not_collapsed_to_one_contract(self):
        doc, pool, games = inputs()
        later, _, _ = inputs(probability=.3)
        later["cards"][0]["legs"][0]["learning_receipt"]["forecast_created_utc"] = "2026-10-18T17:00:30Z"
        refresh_receipt(later, fixture()[1])
        batch = adapt_prospective([doc, later], game_evidence=games, observed_utc=OBSERVED)
        self.assertEqual(len(batch["receipts"]), 2)
        self.assertEqual(len({r["contract_id"] for r in batch["receipts"]}), 1)

    def test_replay_preserves_first_capture_and_journal_is_idempotent(self):
        _, pool, games = inputs()
        first = adapt_prospective(pool_docs=[pool], game_evidence=games, observed_utc=OBSERVED)
        with ReceiptJournal(":memory:") as store:
            store.append_receipt(first["receipts"][0])
            second = adapt_prospective(pool_docs=[pool], game_evidence=games,
                observed_utc="2026-10-18T17:30:00Z", existing_receipts=store.receipts())
            self.assertEqual(first["receipts"], second["receipts"])
            self.assertFalse(store.append_receipt(second["receipts"][0]))

    def test_unknown_late_live_and_wrong_period_evidence_is_quarantined(self):
        for kind in ("missing", "late", "live", "wrong_week", "unknown_source", "no_time"):
            _, pool, games = inputs()
            observed = OBSERVED
            if kind == "missing": games = {}
            elif kind == "late": observed = games["g1"]["kickoff_utc"]
            elif kind == "live": games["g1"]["status"] = "STATUS_IN_PROGRESS"
            elif kind == "wrong_week": games["g1"]["week"] = 5
            elif kind == "unknown_source": games["g1"]["source"] = ""
            elif kind == "no_time": observed = None
            with self.subTest(kind=kind):
                batch = adapt_prospective(pool_docs=[pool], game_evidence=games, observed_utc=observed)
                self.assertEqual(batch["receipts"], [])
                self.assertTrue(batch["rejections"])

    def test_null_source_and_explicit_ineligibility_are_not_training_candidates(self):
        for change in ("null_source", "unverified", "wrong_week"):
            doc, _, games = inputs()
            source = doc["cards"][0]["legs"][0]["learning_receipt"]
            if change == "null_source": source["source_timestamps"] = {"weekly": None}
            elif change == "unverified": source["input_context"]["eligibility"] = {"eligible": False}
            else: source["input_context"]["this_week"] = {"wk": 5}
            refresh_receipt(doc, fixture()[1])
            batch = adapt_prospective([doc], game_evidence=games, observed_utc=OBSERVED)
            self.assertEqual(batch["receipts"], [])

    def test_generation_snapshot_replay_and_preserved_population(self):
        _, pool, games = inputs()
        first = journal_document(pool, games)
        later = journal_document(pool, games, "2026-10-18T17:20:00Z")
        batch = adapt_prospective(journal_docs=[later, first])
        reverse = adapt_prospective(journal_docs=[first, later])
        self.assertEqual(batch, reverse)
        self.assertEqual(batch["rejections"], [])
        self.assertEqual(batch["receipts"][0]["first_seen_utc"], OBSERVED)
        provenance = batch["receipts"][0]["capture_provenance"]
        self.assertEqual(provenance["population"], "eligible_pool_prop_rungs_before_display_dedupe")
        self.assertEqual(provenance["offered_card_ids"], [])

    def test_existing_receipt_disagreement_fails_closed_without_overwrite(self):
        _, pool, games = inputs()
        first = adapt_prospective(pool_docs=[pool], game_evidence=games, observed_utc=OBSERVED)
        games["g1"]["kickoff_utc"] = "2026-10-18T19:00:00Z"
        retry = adapt_prospective(pool_docs=[pool], game_evidence=games, observed_utc=OBSERVED,
                                 existing_receipts=first["receipts"])
        self.assertEqual(retry["receipts"], [])
        self.assertIn("existing_receipt_conflict", retry["summary"]["quarantine_reasons"])

    def test_valid_unresolved_player_survives_an_invalid_sibling(self):
        doc, _, games = inputs()
        bad = copy.deepcopy(doc["cards"][0]["legs"][0])
        bad["model_prob"] = .999
        doc["cards"][0]["legs"].append(bad)
        batch = adapt_prospective([doc], game_evidence=games, observed_utc=OBSERVED)
        self.assertEqual(len(batch["receipts"]), 1)
        self.assertEqual(batch["summary"]["quarantined_records"], 1)

    def test_journal_hash_identity_generation_link_and_capture_time_checked(self):
        for kind in ("tamper", "identity", "generation", "capture", "late", "publication"):
            _, pool, games = inputs()
            doc = journal_document(pool, games)
            row = doc["receipts"][0]
            if kind == "tamper": row["leg"]["line"] = 1
            elif kind == "identity": row["receipt_id"] = fingerprint("other")
            elif kind == "generation": row["generation_id"] = fingerprint("other")
            elif kind == "capture": row["recorded_utc"] = "2026-10-18T17:02:00Z"
            elif kind == "late":
                row["recorded_utc"] = row["kickoff_utc"]
                doc["generations"][0]["recorded_utc"] = row["recorded_utc"]
            else: row["publication_status"] = "verified_publication"
            if kind != "tamper": rehash(doc)
            with self.subTest(kind=kind):
                result = adapt_prospective(journal_docs=[doc])
                self.assertEqual(result["receipts"], [])
                self.assertTrue(result["rejections"])

    def test_malformed_unhashable_values_and_conflicting_kickoff_do_not_crash(self):
        for field in ("market", "game_id", "line"):
            doc, _, games = inputs()
            doc["cards"][0]["legs"][0][field] = []
            result = adapt_prospective([doc], game_evidence=games, observed_utc=OBSERVED)
            self.assertEqual(result["receipts"], [])
            self.assertTrue(result["rejections"])
        _, pool, games = inputs()
        first = journal_document(pool, games)
        other = copy.deepcopy(first)
        other["receipts"][0]["kickoff_utc"] = "2026-10-18T19:00:00Z"
        rehash(other)
        result = adapt_prospective(journal_docs=[first, other])
        self.assertEqual(result["receipts"], [])
        self.assertIn("conflicting_capture_evidence", result["summary"]["quarantine_reasons"])

    @unittest.skipUnless(os.environ.get("NFL2026_CHECKOUT"), "set NFL2026_CHECKOUT for actual producer integration")
    def test_actual_nfl_receipt_and_generation_producers(self):
        checkout = Path(os.environ["NFL2026_CHECKOUT"]).resolve()
        self.assertTrue((checkout / "scripts/learning_receipts.py").is_file())
        self.assertTrue((checkout / "scripts/learning_journal.py").is_file())
        doc, pool, games = inputs(correction={"a": -.7, "b": .7})
        original = doc["cards"][0]["legs"][0]["learning_receipt"]
        script = '''
import json,sys
sys.path.insert(0, sys.argv[1])
from scripts import learning_receipts as lr, learning_journal as lj
data=json.load(sys.stdin)
s=data['source']; leg=data['pool']['players'][0]
r=lr.make_receipt({**leg,'line':s['line']},s['base_prob'],s['calibrator'],s,
                  features=s['features'],input_context=s['input_context'])
data['pool']['players'][0]['rungs'][0]['learning_receipt']=r
envelope,_=lj.capture(data['pool'],data['schedule'],{},[],data['observed'])
print(json.dumps({'receipt':r,'journal':envelope}))
'''
        result = subprocess.run([sys.executable, "-c", script, str(checkout)],
            input=json.dumps({"source": original, "pool": pool,
                "schedule": {"season": 2026, "games": [{**games["g1"], "game_id": "g1"}]}, "observed": OBSERVED}),
            text=True, capture_output=True, check=True, timeout=30)
        actual = json.loads(result.stdout)
        self.assertEqual(actual["receipt"], original)
        self.assertEqual(len(actual["journal"]["receipts"]), 1)
        adapted = adapt_prospective(journal_docs=[actual["journal"]])
        self.assertEqual(adapted["rejections"], [])
        self.assertEqual(len(adapted["receipts"]), 1)
        self.assertEqual(adapted["receipts"][0]["source_receipt_id"], original["receipt_id"])
        # Producer and consumer must agree on strict prospective eligibility;
        # the historical NFL verifier deliberately remains more permissive.
        for timestamps, reason in (({}, "missing_source_timestamps"),
                                   ({"weekly": None}, "invalid_timestamp")):
            with self.subTest(timestamps=timestamps):
                bad_source = copy.deepcopy(original)
                bad_source["source_timestamps"] = timestamps
                bad_source["receipt_id"] = fingerprint({k: v for k, v in bad_source.items() if k != "receipt_id"})
                bad_pool = copy.deepcopy(pool)
                bad_pool["players"][0]["rungs"][0]["learning_receipt"] = bad_source
                raw = adapt_prospective(pool_docs=[bad_pool], game_evidence=games, observed_utc=OBSERVED)
                self.assertEqual(raw["receipts"], [])
                self.assertEqual(raw["summary"]["quarantine_reasons"], {reason: 1})
                run = subprocess.run([sys.executable, "-c", script, str(checkout)],
                    input=json.dumps({"source": bad_source, "pool": bad_pool,
                        "schedule": {"season": 2026, "games": [{**games["g1"], "game_id": "g1"}]},
                        "observed": OBSERVED}), text=True, capture_output=True, check=True, timeout=30)
                captured = json.loads(run.stdout)["journal"]
                self.assertEqual(captured["receipts"], [])
                self.assertEqual(captured["generations"][0]["audit"]["quarantine_reasons"], {reason: 1})
                self.assertEqual(adapt_prospective(journal_docs=[captured])["receipts"], [])


if __name__ == "__main__":
    unittest.main()
