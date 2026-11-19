import json
from pathlib import Path
import subprocess
import sys

from publication_fit_fixture import seed_publication_journal
from selflearn_core.adapters.nfl_publication_journal import NFLPublicationJournal


CLI = Path(__file__).resolve().parents[3] / "tools" / "replay_nfl_corrections.py"


def run(journal, *args):
    return subprocess.run([sys.executable, str(CLI), "--journal", str(journal), *args],
                          capture_output=True, text=True)


def read(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_replay_uses_an_existing_journal_without_changing_it(tmp_path):
    database = tmp_path / "NFL journal #1.sqlite"
    with NFLPublicationJournal(database) as journal:
        seed_publication_journal(journal)
    original = database.read_bytes()
    fit_clock = "2090-01-01T00:00:00Z"
    start = "2090-01-02T00:00:00Z"
    end = "2090-01-03T00:00:00Z"
    fitted = read(run(database, "fit", "--trained-through", fit_clock, "--fitted-at", fit_clock,
                      "--expires-at", end))
    assert fitted["version"] == 2
    assert fitted["fit_audit"]["forecast_cohort_count"] == 3
    assert fitted["fit_audit"]["graded_count"] == 3
    artifact = tmp_path / "artifact.json"
    artifact.write_text(json.dumps(fitted))
    frozen = read(run(database, "freeze", "--artifact", str(artifact), "--start", start, "--end", end))
    assert frozen["artifact_id"] == fitted["artifact_id"]
    holdout = tmp_path / "holdout.json"
    holdout.write_text(json.dumps(frozen))
    score_args = ("score", "--artifact", str(artifact), "--holdout", str(holdout), "--as-of", end)
    report = read(run(database, *score_args))
    assert report["forecast_count"] == report["scored_count"] == 0
    assert report["evidence"]["status"] == "held"
    assert report["adoption_approved"] is False
    frozen["members"] = [{"receipt_id": "unexpected"}]
    holdout.write_text(json.dumps(frozen))
    refused = run(database, *score_args)
    assert refused.returncode == 1
    assert "frozen_holdout_membership_mismatch" in refused.stderr
    assert not refused.stdout
    assert database.read_bytes() == original


def test_missing_journal_is_refused_without_creating_a_file(tmp_path):
    database = tmp_path / "missing" / "journal.sqlite"
    refused = run(database, "fit", "--trained-through", "2090-01-01T00:00:00Z",
                  "--fitted-at", "2090-01-01T00:00:00Z", "--expires-at", "2090-01-03T00:00:00Z")
    assert refused.returncode == 1
    assert "replay refused:" in refused.stderr
    assert not database.parent.exists()
