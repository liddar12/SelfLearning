"""Task-separated evaluation and proposal preparation, never promotion.

Descriptive scores may include imported observations. Learning proposals use
only evidence accepted by an explicit external verifier; metadata saying
"verified" is not sufficient. This module does not train foundation models,
change a registry, alter risk limits, or implement the later L2-L4 policies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from ..types import ResolvedPrediction, Score
from ..updater.policy import Change, Level0Monitor, UpdatePolicy
from .metrics import TaskSpec, score_task


@dataclass(frozen=True)
class TaskEvaluation:
    task: str
    observed_count: int
    eligible_count: int
    descriptive_scores: tuple[Score, ...]
    eligible_scores: tuple[Score, ...]
    proposals: tuple[Change, ...]


def eligible_learning_evidence(
    record: ResolvedPrediction,
    verifier: Callable[[ResolvedPrediction], bool] | None,
) -> bool:
    """Require independent evidence verification and reject all terminal imports.

    The verifier must establish forecast provenance, observation/receipt times
    and outcome validity from trusted receipts. A worker login, self-asserted
    metadata, or absence of an import tag is not such proof. With no verifier,
    all observations remain descriptive only.
    """
    meta = record.prediction.meta
    if (verifier is None or not isinstance(meta, dict) or "_terminal" in meta
            or record.outcome.prediction_id != record.prediction.id):
        return False
    try:
        return verifier(record) is True
    except Exception:
        raise ValueError("Independent learning-evidence verification failed.") from None


def evaluate_tasks(
    resolved: Iterable[ResolvedPrediction], specs: Iterable[TaskSpec], *,
    evidence_verifier: Callable[[ResolvedPrediction], bool] | None = None,
    policy: UpdatePolicy | None = None,
) -> tuple[TaskEvaluation, ...]:
    """Reuse one evaluator while keeping every task's evidence and changes apart.

    The default policy is monitoring only. An explicit L1 policy can produce
    pending proposals from verified evidence; applying them is outside this
    API. Callers must separately validate any proposed cross-domain transfer.
    """
    specs = tuple(specs)
    if (any(not isinstance(s.task, str) or not s.task.strip()
            or s.kind not in ("idea", "regression") for s in specs)
            or len({s.task for s in specs}) != len(specs)):
        raise ValueError("Evaluation requires unique, valid task specifications.")
    tasks = {s.task for s in specs}
    grouped: dict[str, list[ResolvedPrediction]] = {task: [] for task in tasks}
    seen: set[tuple[str, str]] = set()
    for record in resolved:
        task = record.prediction.task
        if task not in tasks:
            continue
        identity = (task, record.prediction.id)
        if identity in seen or record.outcome.prediction_id != record.prediction.id:
            raise ValueError("Evaluation received duplicate or mismatched evidence.")
        seen.add(identity)
        grouped[task].append(record)
    selected_policy = policy if policy is not None else Level0Monitor()
    result = []
    for spec in specs:
        records = grouped[spec.task]
        eligible = [r for r in records if eligible_learning_evidence(r, evidence_verifier)]
        scores = tuple(score_task(eligible, spec))
        proposals = tuple(selected_policy.propose(scores))
        if any(p.task != spec.task for p in proposals):
            raise ValueError("Learning policy returned a change for another task.")
        result.append(TaskEvaluation(
            task=spec.task, observed_count=len(records), eligible_count=len(eligible),
            descriptive_scores=tuple(score_task(records, spec)),
            eligible_scores=scores, proposals=proposals,
        ))
    return tuple(result)
