"""Read-only arithmetic/coverage audit of the declared local-build task ledger.

This checks IDs, denominators and evidence references, not whether referenced
tests prove a story, a deployment, an accuracy gain or an overnight deadline.
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path


class ProgressError(ValueError):
    pass


EXTRA_TASKS = {f"D{lane}-T{task}" for lane in (1, 2) for task in (1, 2, 3)}
R1_TASK_EPIC_SHA256 = "cff9dedf931826e9f1076029ccb44731aaf1830cc3737b6c4afe65c4c06b3676"


def _ids(value):
    if type(value) is not list or any(type(v) is not str or not v for v in value) or len(set(value)) != len(value):
        raise ProgressError("invalid_or_duplicate_task_ids")
    return set(value)


def validate(progress, backlog):
    if type(progress) is not dict or type(progress.get("version")) is not int or progress["version"] != 1:
        raise ProgressError("unsupported_progress_version")
    authority = {}
    for row in backlog:
        task, epic = row["Task"], row["Epic"]
        if task in authority:
            raise ProgressError("duplicate_backlog_task")
        authority[task] = epic
    # This validator is for the explicitly frozen R1 + D1/D2 scope. A future
    # approved scope revision must update its authority, not erase unfinished
    # tasks and quietly improve the reported percentage.
    if len(authority) != 93 or len(set(authority.values())) != 12:
        raise ProgressError("changed_r1_backlog_scope")
    mapping = json.dumps(authority, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    if hashlib.sha256(mapping).hexdigest() != R1_TASK_EPIC_SHA256:
        raise ProgressError("changed_r1_task_epic_authority")
    extras = progress["extra_tasks"]
    extra_ids, extra_done = _ids(extras["ids"]), _ids(extras["completed"])
    if extra_ids != EXTRA_TASKS or extra_ids.intersection(authority) or not extra_done <= extra_ids or extras["epic"] != "E02":
        raise ProgressError("invalid_extra_task_scope")
    rows, seen, seen_epics, counts, done = [], set(), set(), Counter(), set()
    evidence = progress["completed_task_evidence"]
    for epic in progress["epics"]:
        eid = epic["id"]
        if eid in seen_epics or eid not in set(authority.values()):
            raise ProgressError("invalid_or_duplicate_epic")
        seen_epics.add(eid)
        local = Counter()
        for state in ("completed", "partial", "open"):
            tasks = _ids(epic[state])
            if seen.intersection(tasks) or any(authority.get(task) != eid for task in tasks):
                raise ProgressError("duplicate_unknown_or_wrong_epic_task")
            seen.update(tasks)
            counts[state] += len(tasks)
            local[state] += len(tasks)
            if state == "completed":
                done.update(tasks)
        extra_n, extra_completed = (len(extra_ids), len(extra_done)) if eid == extras["epic"] else (0, 0)
        total, completed = sum(local.values()) + extra_n, local["completed"] + extra_completed
        rows.append({"id": eid, "completed": completed, "tasks": total,
                     "percent": round(100 * completed / total, 1),
                     "metric": "whole local-build tasks; not story or production acceptance"})
    if seen != set(authority) or seen_epics != set(authority.values()):
        raise ProgressError("unclassified_backlog_tasks_or_epics")
    done.update(extra_done)
    if any(type(evidence.get(task)) is not str or not evidence[task].strip() for task in done):
        raise ProgressError("completed_task_missing_evidence_reference")
    original = {"epics": len(seen_epics), "tasks": len(authority), "completed": counts["completed"],
                "partial": counts["partial"], "open": counts["open"]}
    expanded = {"tasks": len(authority) + len(extra_ids), "completed": len(done), "partial": counts["partial"],
                "open_or_in_progress": counts["open"] + len(extra_ids - extra_done)}
    for label, expected in (("original_scope", original), ("expanded_scope", expanded)):
        actual = progress[label]
        if (type(actual) is not dict or set(actual) != set(expected)
                or any(type(actual[k]) is not int or actual[k] != v for k, v in expected.items())):
            raise ProgressError("declared_task_counts_do_not_reconcile")
    return {"version": 1, "arithmetic_verified": True, "semantic_completion_verified": False,
            "original_scope": original, "expanded_scope": expanded,
            "percent": round(100 * expanded["completed"] / expanded["tasks"], 1), "epics": rows,
            "limitations": ["Evidence references are required but not executed or substantively certified by this tool.",
                            "No partial-task credit, story acceptance, release readiness, accuracy or deadline guarantee."]}


def main(argv=None):
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--progress", type=Path, default=root / "docs/architecture/2026-10-15/progress.json")
    parser.add_argument("--backlog", type=Path, default=root / "docs/architecture/2026-10-15/BACKLOG.csv")
    args = parser.parse_args(argv)
    try:
        progress = json.loads(args.progress.read_text(encoding="utf-8"))
        with args.backlog.open(encoding="utf-8", newline="") as handle:
            result = validate(progress, list(csv.DictReader(handle)))
    except (ValueError, KeyError, TypeError, OSError):
        print('{"version":1,"arithmetic_verified":false,"reason":"invalid_progress_ledger"}')
        return 1
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
