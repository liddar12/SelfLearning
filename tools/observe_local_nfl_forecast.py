#!/usr/bin/env python3
"""Retain a publicly retrievable NFL forecast in a local journal."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "packages" / "selflearn-core")]

from selflearn_core.adapters.nfl_publication_journal import NFLPublicationJournal
from services.web.nfl_ingestion import finalize_generation
from services.web.nfl_publication_verifier import PublicPoolVerifier, fetch_public_pool

FILES = ("leg_pool.json", "schedule_full.json", "parlay_backtest.json")


def observe(*, origin, workspace, nfl_checkout, nfl_commit, published_commit):
    git = ["git", "-C", str(nfl_checkout)]
    if not re.fullmatch(r"[0-9a-f]{40}", published_commit):
        raise ValueError("invalid_published_commit")
    head = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output([*git, "status", "--porcelain", "--", "scripts", "config"], text=True)
    if head != nfl_commit or dirty:
        raise ValueError("producer_checkout_not_at_clean_expected_commit")
    sys.path.insert(0, str(nfl_checkout.resolve()))
    from scripts import export_learning as export, run_forecast_delivery as delivery

    verifier = PublicPoolVerifier(origin)
    first = verifier.observe()
    inputs = {"leg_pool.json": first["body_utf8"].encode("utf-8")}
    for name in FILES[1:]:
        url = origin + "/data/" + name
        code, headers, raw, final_url = fetch_public_pool(url)
        media_type = next((value for key, value in headers.items() if key.lower() == "content-type"), "")
        if code != 200 or final_url != url or media_type.lower().split(";", 1)[0].strip() != "application/json":
            raise ValueError("public_source_response_refused_" + name)
        inputs[name] = raw
    for name, public_bytes in inputs.items():
        expected = subprocess.check_output([*git, "show", f"{published_commit}:data/{name}"])
        if public_bytes != expected:
            raise ValueError("published_source_does_not_match_commit_" + name)
    checks = {name: sha256(raw).hexdigest() for name, raw in inputs.items()}
    local_generation = workspace / "forecasts" / export.fingerprint(checks).split(":")[1]
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="read-public-source-", dir=workspace) as directory:
        source = Path(directory)
        for name, raw in inputs.items():
            (source / name).write_bytes(raw)
        prepared = delivery.prepare(source, local_generation, execute=True)
    if not prepared["ok"]:
        raise ValueError("no_eligible_public_forecasts")

    plan = export.plan(local_generation / "snapshots")
    generation, = plan["generations"]
    request = generation["request"]
    object_id = request["object_id"]
    journal_path = workspace / "journal.sqlite"
    with NFLPublicationJournal(journal_path) as journal:
        original = journal.get_publication_attestation(object_id)
        for obj in plan["source_objects"] + [request]:
            journal.append_object(obj["kind"], obj["object_id"], obj["payload"])
        ack = finalize_generation(journal, object_id)
        if original is None:
            observed = verifier.observe()
            if observed["body_utf8"] != first["body_utf8"]:
                raise ValueError("public_pool_changed_during_observation")
            proof = journal.commit_publication_attestation(object_id, observed)
        else:
            proof = original
        if proof["policy"]["origin"] != origin or proof["observation"]["body_sha256"] != "sha256:" + checks["leg_pool.json"]:
            raise ValueError("prior_publication_observation_does_not_match_source")
    return {
        "state": "replayed" if original else "recorded",
        "journal": str(journal_path), "generation_workspace": str(local_generation),
        "origin": origin, "published_commit": published_commit, "producer_commit": nfl_commit,
        "file_sha256": checks, "generation_id": prepared["generation_id"], "envelope_id": object_id,
        "attestation_id": proof["attestation_id"], "observed_completed_utc": proof["observation"]["observed_completed_utc"],
        "accepted": len(ack["receipts"]), "attested_forecasts": len(proof["receipts"]),
        "intake_rejections": dict(Counter(row["reason"] for row in ack["rejections"])),
        "publication_rejections": dict(Counter(row["reason"] for row in proof["rejections"])),
        "hosted_writes": False, "adoption_approved": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True, help="approved HTTPS NFL site origin")
    parser.add_argument("--workspace", type=Path, required=True, help="private local capture directory")
    parser.add_argument("--nfl-checkout", type=Path, required=True, help="trusted local NFL2026 checkout")
    parser.add_argument("--nfl-commit", required=True, help="expected full producer source SHA")
    parser.add_argument("--published-commit", required=True, help="full SHA whose generated data is served")
    args = parser.parse_args(argv)
    try:
        result = observe(**vars(args))
    except (OSError, ValueError, KeyError, sqlite3.Error, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"local forecast observation refused: {exc}\n")
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
