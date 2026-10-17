"""Arithmetic ledger checks are not substantive task acceptance."""
import copy
import csv
import json
from pathlib import Path

import pytest

from tools.validate_progress import ProgressError, validate

ROOT = Path(__file__).resolve().parents[1] / 'docs/architecture/2026-10-15'


@pytest.fixture
def ledger():
    progress = json.loads((ROOT / 'progress.json').read_text())
    with (ROOT / 'BACKLOG.csv').open(newline='') as handle:
        backlog = list(csv.DictReader(handle))
    return progress, backlog


def test_actual_ledger_reconciles_without_certifying_story_acceptance(ledger):
    progress, backlog = ledger
    before = copy.deepcopy(progress)
    result = validate(progress, backlog)
    assert result['arithmetic_verified'] is True and result['semantic_completion_verified'] is False
    assert sum(row['tasks'] for row in result['epics']) == progress['expanded_scope']['tasks']
    assert sum(row['completed'] for row in result['epics']) == progress['expanded_scope']['completed']
    assert result['expanded_scope']['tasks'] == 99 and result['original_scope']['tasks'] == 93
    assert progress == before


@pytest.mark.parametrize('fault', ['duplicate','drop','wrong_epic','unknown','no_evidence','inflated_count',
                                  'bool_count','hidden_extra','hidden_extra_recounted','shrunken_backlog',
                                  'duplicate_extra','wrong_extra_epic','renamed_authority','reassigned_authority','version'])
def test_false_numerator_denominator_and_membership_are_rejected(ledger, fault):
    progress, backlog = ledger
    first, second = progress['epics'][:2]
    tid = first['completed'][0]
    if fault == 'duplicate': first['partial'].append(tid)
    elif fault == 'drop': first['completed'].remove(tid)
    elif fault == 'wrong_epic': first['completed'].remove(tid); second['completed'].append(tid)
    elif fault == 'unknown': first['open'].append('S999-T1')
    elif fault == 'no_evidence': del progress['completed_task_evidence'][tid]
    elif fault == 'inflated_count': progress['expanded_scope']['completed'] += 1
    elif fault == 'bool_count': progress['original_scope']['completed'] = True
    elif fault == 'hidden_extra': progress['extra_tasks']['ids'].pop()
    elif fault == 'hidden_extra_recounted':
        progress['extra_tasks']['ids'].remove('D2-T3')
        progress['expanded_scope']['tasks'] -= 1
        progress['expanded_scope']['open_or_in_progress'] -= 1
    elif fault == 'shrunken_backlog': backlog.pop()
    elif fault == 'duplicate_extra': progress['extra_tasks']['ids'].append(progress['extra_tasks']['ids'][0])
    elif fault == 'wrong_extra_epic': progress['extra_tasks']['epic'] = 'E999'
    elif fault in ('renamed_authority', 'reassigned_authority'):
        partial = first['partial'][0]
        row = next(r for r in backlog if r['Task'] == partial)
        first['partial'].remove(partial)
        if fault == 'renamed_authority':
            row['Task'] = 'S999-T1'
            first['partial'].append('S999-T1')
        else:
            row['Epic'] = second['id']
            second['partial'].append(partial)
    elif fault == 'version': progress['version'] = True
    with pytest.raises(ProgressError): validate(progress, backlog)
