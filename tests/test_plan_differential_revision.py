"""Differential plans preserve host evidence and enforce immutable revisions."""
import copy
from types import SimpleNamespace

import pytest

from kitt.core.task_plan import TaskPlanCoordinator


class Ledger:
    def __init__(self):
        self.rows = []
        self.ids = {}
    def events(self, conversation, *, turn_id, after_sequence, limit):
        return [row for row in self.rows if row.sequence > after_sequence
                and row.turn_id == turn_id][:limit]
    def append_event(self, conversation, event_type, payload, *, turn_id, event_id, **kwargs):
        old = self.ids.get(event_id)
        if old is not None and old != payload:
            raise ValueError('revision commit collision')
        self.ids[event_id] = copy.deepcopy(payload)
        record = SimpleNamespace(sequence=len(self.rows) + 1, event_type=event_type,
                                 payload=copy.deepcopy(payload), turn_id=turn_id)
        self.rows.append(record)
        return record


class Context:
    def assert_path_allowed(self, path):
        if path.startswith('secret'):
            raise PermissionError(path)


def test_patch_requires_revision_and_pending_state(tmp_path):
    ledger = Ledger()
    plans = TaskPlanCoordinator(ledger, tmp_path)
    baseline = plans.submit('conv', 'turn', {'schema_version': 1, 'objective': 'test',
        'tasks': [{'local_id': 'T01', 'title': 'initial', 'role': 'IMPLEMENT',
                   'paths': ['a.py'], 'check_ids': [], 'depends_on': []}]}, Context())
    assert baseline['revision'] == 1
    changed = plans.patch('conv', 'turn', 1, [
        {'local_id': 'T01', 'title': 'updated', 'paths': ['b.py']}], Context())
    assert changed['revision'] == 2
    assert changed['tasks'][0]['title'] == 'updated'
    assert changed['tasks'][0]['paths'] == ['b.py']
    assert changed['tasks'][0]['task_id'] == baseline['tasks'][0]['task_id']
    with pytest.raises(ValueError, match='Stale'):
        plans.patch('conv', 'turn', 1, [{'local_id': 'T01', 'title': 'stale'}], Context())
    assert plans.inspect('conv', 'turn')['revision'] == 2


def test_patch_rejects_cycles_unauthorized_paths_and_nonpending(tmp_path):
    ledger = Ledger()
    plans = TaskPlanCoordinator(ledger, tmp_path)
    plans.submit('conv', 'turn', {'schema_version': 1, 'objective': 'test',
        'tasks': [
            {'local_id': 'A', 'title': 'a', 'paths': ['a.py'], 'depends_on': []},
            {'local_id': 'B', 'title': 'b', 'paths': ['b.py'], 'depends_on': ['A']}
        ]}, Context())
    with pytest.raises(ValueError, match='Cyclic'):
        plans.patch('conv', 'turn', 1, [{'local_id': 'A', 'depends_on': ['B']}], Context())
    with pytest.raises(PermissionError):
        plans.patch('conv', 'turn', 1, [{'local_id': 'A', 'paths': ['secret.txt']}], Context())
    assert plans.inspect('conv', 'turn')['revision'] == 1
    # Existing task evidence is immutable to model-originated field patches.
    current = plans.inspect('conv', 'turn')
    current['tasks'][0]['status'] = 'VERIFIED'
    plans._save('conv', 'turn', current)
    with pytest.raises(ValueError, match='pending'):
        plans.patch('conv', 'turn', 2, [{'local_id': 'A', 'title': 'rewrite'}], Context())
