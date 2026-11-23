"""Compare copy paths on a synthetic repeated graph, not valid journal authority.

Run each mode in a separate process to compare maximum RSS fairly:
PYTHONPATH=packages/selflearn-core:packages/selflearn-core/tests python tests/profile_scoped_snapshot_copies.py old
PYTHONPATH=packages/selflearn-core:packages/selflearn-core/tests python tests/profile_scoped_snapshot_copies.py owned
"""
import copy
import json
import resource
import sys
import time

from selflearn_core.adapters.nfl_capture_job import _PinnedJournal
from selflearn_core.adapters.nfl_capture_labels import CaptureGameScope, _OwnedGameSnapshot, _ScopedPinnedJournal
from test_nfl_capture_labels import journal_fixture

mode = sys.argv[1]
assert mode in {"old", "owned"}
fixture = journal_fixture(vintages=1, lines=tuple(i + 0.5 for i in range(668)))
scope = CaptureGameScope(2026, 6, "g6")
seed = fixture.snapshot["generation_records"][0]
snapshot = {**fixture.snapshot, "game_scope": scope.document(), "generation_records": [seed] * 38}
# Parse separately so the measured graph has independent records and strings.
serialized = json.dumps(snapshot, separators=(",", ":"))
wire_bytes = len(serialized.encode())
snapshot = json.loads(serialized)
del serialized, seed, fixture
started = time.perf_counter()
if mode == "old":
    cache = copy.deepcopy(snapshot)
    operation = copy.deepcopy(snapshot)
    del snapshot
    binding = copy.deepcopy(_PinnedJournal(None, operation).read_observed_snapshot())
else:
    cache = copy.deepcopy({key: value for key, value in snapshot.items() if key != "generation_records"})
    operation = snapshot
    binding = _ScopedPinnedJournal(None, _OwnedGameSnapshot(operation, scope))._borrow(scope)
print(json.dumps({"mode": mode, "scope": "synthetic_copy_mechanics_not_journal_or_hosted_evidence",
                  "generation_count": 38, "json_bytes": wire_bytes, "copy_path_seconds": time.perf_counter() - started,
                  "maximum_rss_platform_units": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  "binding_borrows_owned_graph": binding is operation}))
