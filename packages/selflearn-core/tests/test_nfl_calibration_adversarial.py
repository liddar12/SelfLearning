"""Independent synthetic audits of committed offering and as-of boundaries.

No live sports data, fitting on real outcomes, source data writes or network.
"""
import copy
import datetime as dt
import unittest

from selflearn_core.adapters.nfl import adapt_prospective
from selflearn_core.adapters.nfl_calibration import build_cohort
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.receipt_journal import ReceiptJournal
from test_nfl_prospective import inputs, journal_document, rehash
from test_nfl_calibration import case, reseal


def _fixtures():
    """Use genuinely past synthetic dates relative to the local DB clock."""
    _, pool, games = inputs()
    day = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=30)
    date = day.isoformat()
    source = pool["players"][0]["rungs"][0]["learning_receipt"]
    source["forecast_created_utc"] = date + "T17:00:00Z"
    source["source_timestamps"] = {"fixture": date + "T16:00:00Z"}
    source["receipt_id"] = fingerprint({k: v for k, v in source.items() if k != "receipt_id"})
    pool["generated_utc"] = source["forecast_created_utc"]
    games["g1"]["kickoff_utc"] = date + "T18:00:00Z"
    first = journal_document(pool, games, date + "T17:01:00Z")
    receipt = adapt_prospective(journal_docs=[first])["receipts"][0]
    later = journal_document(pool, games, date + "T17:20:00Z")
    generation = later["generations"][0]
    generation["offered_cards"] = [{"card_id": "later-offered", "receipt_ids": [source["receipt_id"]]}]
    keys = ("version", "season", "week", "pool_generated_utc", "source_snapshots",
            "offered_cards", "selection_policy_version", "joint_model_version")
    generation["generation_id"] = fingerprint({k: generation[k] for k in keys})
    later["receipts"][0]["generation_id"] = generation["generation_id"]
    rehash(later)
    result = {"receipt_id": receipt["receipt_id"], "contract_id": receipt["contract_id"],
              "event_id": receipt["event_id"], "status": "FINAL", "hit": True,
              "source": "synthetic fixture only", "resolved_utc": date + "T22:00:00Z"}
    revision = {"version": 1, "revision_id": fingerprint(result), "supersedes": None,
                "reason": None, "resolution": result, "recorded_utc": date + "T22:01:00Z"}
    cutoff = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat()
    return first, later, receipt, source, revision, cutoff


def _cohort(receipt, source, revision, cutoff, records):
    return build_cohort([receipt], [source], [revision], as_of_utc=cutoff,
                        population="offered_my", generation_records=records)


def _commit(journal, document, receipts, rejections=()):
    journal.append_object("generation", document["envelope_id"], document)
    return journal.commit_generation(document["envelope_id"], receipts, list(rejections))


class NFLCommittedMembershipAdversarialTest(unittest.TestCase):
    def test_rejected_later_generation_cannot_resurrect_offered_membership(self):
        first, later, receipt, source, revision, cutoff = _fixtures()
        with ReceiptJournal(":memory:") as journal:
            _commit(journal, first, [receipt])
            rejection = {"source_receipt_id": source["receipt_id"],
                         "reason": "historical_adapter_policy_rejection"}
            ack = _commit(journal, later, [], [rejection])
            self.assertEqual(ack["receipts"], [])
            # Today's adapter accepts this row. A durable rejection from an
            # earlier boundary version must still control historical admission.
            self.assertEqual(len(adapt_prospective(journal_docs=[later],
                                                  existing_receipts=journal.receipts())["receipts"]), 1)
            cohort = _cohort(receipt, source, revision, cutoff, journal.generations())
            self.assertEqual(cohort["n"], 0)
            self.assertEqual(receipt["capture_provenance"]["offered_card_ids"], [])

    def test_accepted_later_membership_uses_exact_original_envelope(self):
        first, later, receipt, source, revision, cutoff = _fixtures()
        frozen = copy.deepcopy(receipt)
        with ReceiptJournal(":memory:") as journal:
            _commit(journal, first, [receipt])
            ack = _commit(journal, later, [receipt])
            self.assertEqual(ack["receipts"][0]["status"], "duplicate")
            cohort = _cohort(receipt, source, revision, cutoff, journal.generations())
            self.assertEqual(cohort["n"], 1)
            self.assertEqual(cohort["rows"][0]["receipt_id"], receipt["receipt_id"])
            self.assertEqual(cohort["rows"][0]["offering"]["card_ids"], ["later-offered"])
            self.assertEqual(journal.receipts(), [frozen])

    def test_generation_without_durable_result_is_not_offering_authority(self):
        _, later, receipt, source, revision, cutoff = _fixtures()
        purported_commit = {"object_id": later["envelope_id"], "payload": later,
                            "recorded_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
        cohort = _cohort(receipt, source, revision, cutoff, [purported_commit])
        self.assertEqual(cohort["n"], 0)

    def test_ack_must_cover_every_source_and_reference_the_exact_envelope(self):
        first, later, receipt, source, revision, cutoff = _fixtures()
        with ReceiptJournal(":memory:") as journal:
            _commit(journal, first, [receipt])
            _commit(journal, later, [receipt])
            stored = next(r for r in journal.generations() if r["object_id"] == later["envelope_id"])
            for kind in ("wrong_envelope", "missing_partition", "wrong_object", "not_durable"):
                record = copy.deepcopy(stored)
                ack = record["ack"]
                if kind == "wrong_envelope":
                    ack["receipts"][0]["receipt_id"] = fingerprint("not-the-original-envelope")
                elif kind == "missing_partition":
                    ack["receipts"] = []
                elif kind == "wrong_object":
                    ack["object_id"] = fingerprint("different-generation")
                else:
                    ack["durable"] = False
                with self.subTest(kind=kind):
                    self.assertEqual(_cohort(receipt, source, revision, cutoff, [record])["n"], 0)


class NFLForecastChronologyAdversarialTest(unittest.TestCase):
    def test_missing_earliest_source_does_not_select_a_later_resolved_vintage(self):
        first, source, old_revision = case()
        later_source = copy.deepcopy(source)
        later_source["forecast_created_utc"] = "2026-09-08T13:00:00Z"
        later_source["receipt_id"] = fingerprint({k: v for k, v in later_source.items() if k != "receipt_id"})
        later = reseal(first, source_receipt_id=later_source["receipt_id"],
                       forecast_created_utc=later_source["forecast_created_utc"], first_seen_utc="2026-09-08T13:01:00Z")
        revision = copy.deepcopy(old_revision)
        revision["resolution"]["receipt_id"] = later["receipt_id"]
        revision["revision_id"] = fingerprint(revision["resolution"])
        cohort = build_cohort([later, first], [later_source], [revision],
                              as_of_utc="2026-12-01T00:00:00Z", population="offered_my")
        self.assertEqual(cohort["n"], 0)
        self.assertIn("earliest_source_lineage_unverified", cohort["excluded"])

    def test_future_evidence_observation_cannot_be_backdated_by_resolution_clock(self):
        receipt, source, revision = case()
        revision["resolution"]["evidence_observed_utc"] = "2026-12-02T00:00:00Z"
        cohort = build_cohort([receipt], [source], [revision], as_of_utc="2026-12-01T00:00:00Z",
                              population="offered_my")
        self.assertEqual(cohort["n"], 0)
        self.assertIn("evidence_after_database_observation", cohort["excluded"])


if __name__ == "__main__":
    unittest.main()
