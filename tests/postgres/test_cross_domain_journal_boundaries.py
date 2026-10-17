"""The IDENTICAL two-domain journal contract on actual PostgreSQL-WASM.

No SQL/source changes, cloud, hosted PostgREST or native concurrency claim.
An absent reviewed PGlite module fails explicitly through PostgresBridge.
"""
from contextlib import contextmanager
import json
import importlib.util
from pathlib import Path
import unittest

from selflearn_core.store.supabase_journal import SupabaseReceiptJournal
from test_adapter_integration import PostgresBridge

CORE_TESTS = Path(__file__).resolve().parents[2] / "packages/selflearn-core/tests"
SPEC = importlib.util.spec_from_file_location("cross_domain_contract_fixture", CORE_TESTS / "test_cross_domain_journal_boundaries.py")
CONTRACT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTRACT)
JournalBoundaryContract, DOMAINS, receipt, resolution = (
    CONTRACT.JournalBoundaryContract, CONTRACT.DOMAINS, CONTRACT.receipt, CONTRACT.resolution)


class PostgresCrossDomainBoundaryTests(JournalBoundaryContract, unittest.TestCase):
    @contextmanager
    def journal(self):
        bridge = PostgresBridge()
        try:
            with SupabaseReceiptJournal("https://offline.supabase.invalid", "sb_secret_synthetic_cross_domain",
                    transport=bridge, page_size=1) as journal:
                yield journal
        finally:
            bridge.close()

    def test_actual_pinned_watermark_preserves_both_domains_before_corrections(self):
        with self.journal() as journal:
            rows = [receipt(domain, task) for domain, task in DOMAINS]
            for index, row in enumerate(rows):
                journal.append_receipt(row)
                journal.append_resolution(resolution(row), revision_id="initial-" + str(index))
            before = journal.read_snapshot()
            for index, row in enumerate(rows):
                journal.append_resolution(resolution(row, False, corrected=True), revision_id="corrected-" + str(index),
                                          supersedes="initial-" + str(index), reason="synthetic correction")
            after = journal.read_snapshot()
            self.assertGreater(int(after["watermark"]), int(before["watermark"]))
            raw, watermark = journal._read("revisions", watermark=before["watermark"])
            self.assertEqual(watermark, before["watermark"])
            payloads = [json.loads(row["payload_json"]) for row in raw]
            self.assertEqual({value["revision_id"] for value in payloads}, {"initial-0", "initial-1"})
            self.assertTrue(all(value["resolution"]["hit"] for value in payloads))
            self.assertEqual(before["receipts"], after["receipts"])
            self.assertEqual(len(before["revisions"]), 2)
            self.assertEqual(len(after["revisions"]), 4)
