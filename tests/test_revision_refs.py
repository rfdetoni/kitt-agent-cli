import tempfile
import unittest
from pathlib import Path

from kitt.runtime.safe_runtime import SafeRuntime
from kitt.security.capabilities import CAP_REPO_READ, CAP_REPO_WRITE


class FakeStore:
    def __init__(self):
        self.values = {}
    def set(self, key, value, ttl_seconds=None):
        self.values[key] = value
        return True
    def get(self, key):
        return self.values.get(key)
    def delete(self, key):
        return self.values.pop(key, None) is not None


class Context:
    capabilities = frozenset({CAP_REPO_READ, CAP_REPO_WRITE})
    principal_type = 'USER'
    principal_id = 'tester'
    def assert_scope(self, workspace_id, conversation_id):
        assert (workspace_id, conversation_id) == ('workspace', 'conversation')
    def assert_path_allowed(self, path):
        if path.startswith('blocked/'):
            raise PermissionError('blocked path')


class RevisionReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'file.txt'
        self.path.write_text('before', encoding='utf-8')
        self.runtime = SafeRuntime(self.tmp.name, 'workspace', 'conversation',
                                   state_store=FakeStore())
        self.ctx = Context()

    def tearDown(self):
        self.tmp.cleanup()

    def action(self, operation, args):
        return self.runtime.execute(operation, args, security_context=self.ctx)

    def test_atomic_revision_receipt_and_replay_rejection(self):
        created = self.action('repo.ref.create', {'path': 'file.txt'})
        self.assertTrue(created.success, created.error)
        ref = created.data['ref']
        self.assertEqual(self.action('repo.ref.read', {'ref': ref}).data['content'], 'before')
        updated = self.action('repo.ref.write', {'ref': ref, 'path': 'file.txt',
                          'expected_revision': created.data['revision'], 'content': 'after'})
        self.assertTrue(updated.success, updated.error)
        self.assertEqual(self.path.read_text(), 'after')
        self.assertNotEqual(updated.data['previous_revision'], updated.data['revision'])
        self.assertFalse(self.action('repo.ref.write', {'ref': ref, 'path': 'file.txt',
                         'expected_revision': created.data['revision'], 'content': 'replay'}).success)

    def test_external_change_rejects_stale_write(self):
        ref = self.action('repo.ref.create', {'path': 'file.txt'}).data
        self.path.write_text('changed out of band', encoding='utf-8')
        result = self.action('repo.ref.write', {'ref': ref['ref'], 'path': 'file.txt',
                           'expected_revision': ref['revision'], 'content': 'overwrite'})
        self.assertFalse(result.success)
        self.assertEqual(self.path.read_text(), 'changed out of band')

    def test_utf8_byte_limit_never_emits_spurious_replacement_character(self):
        self.path.write_text('A€B', encoding='utf-8')
        created = self.action('repo.ref.create', {'path': 'file.txt'})
        self.assertTrue(created.success, created.error)
        ref = created.data['ref']
        for limit, expected in ((1, 'A'), (2, 'A'), (3, 'A'), (4, 'A€'), (5, 'A€B')):
            with self.subTest(limit=limit):
                result = self.action('repo.ref.read', {'ref': ref, 'max_bytes': limit})
                self.assertTrue(result.success, result.error)
                self.assertEqual(result.data['content'], expected)
                self.assertEqual(result.data['truncated'], limit < 5)

    def test_path_scope_and_reference_scoping(self):
        ref = self.action('repo.ref.create', {'path': 'file.txt'}).data
        self.assertFalse(self.action('repo.ref.write', {'ref': ref['ref'], 'path': 'blocked/other',
                             'expected_revision': ref['revision'], 'content': 'bad'}).success)
        self.runtime.state = FakeStore()
        self.assertFalse(self.action('repo.ref.read', {'ref': ref['ref']}).success)


if __name__ == '__main__':
    unittest.main()
