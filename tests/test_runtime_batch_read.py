"""Batch never widens tool authority; responses stay correlated by identifier."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kitt.runtime.safe_runtime import SafeRuntime, SafeRuntimeResult
from kitt.security.capabilities import CAP_REPO_READ


class ReadBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime = SafeRuntime(Path(self.tmp.name), 'ws', 'conv')

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, calls, caps=None):
        return self.runtime.execute('repo.batch_read', {'calls': calls},
                                    effective_capabilities=caps if caps is not None else {CAP_REPO_READ})

    @staticmethod
    def _read(identifier, path):
        return {'id': identifier, 'operation': 'repo.read', 'arguments': {'path': path}}

    def test_kap_capabilities_list_only_implemented_host_features(self):
        from kitt.runtime.read_batch import POSITIONAL_DIGEST
        status = self.runtime.execute('kap.capabilities', {})
        self.assertTrue(status.success)
        self.assertEqual(status.data['grammar'], 'KAP/2')
        self.assertEqual(status.data['positional_schema_digest'], POSITIONAL_DIGEST)
        self.assertFalse(status.data['frame_batch'])
        self.assertFalse(status.data['early_stream_termination'])
        self.assertFalse(status.data['metadata_version_negotiation'])
        self.assertFalse(self.runtime.execute('kap.capabilities', {'enable_unsafe': True}).success)

    def test_read_only_parallel_batch_maintains_order_and_receipts(self):
        def fake_read(args, *_):
            return SafeRuntimeResult(True, 'repo.read', data={'path': args['path']})
        with patch.object(self.runtime, '_op_repo_read', side_effect=fake_read):
            result = self._run([self._read('a', 'a.py'), self._read('b', 'b.py')])
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.metadata['parallel'], 2)
        self.assertEqual([(x['id'], x['data']['path']) for x in result.data['calls']],
                         [('a', 'a.py'), ('b', 'b.py')])

    def test_invalid_batch_fails_before_first_execution(self):
        bad = [
            [self._read('a', 'a'), self._read('a', 'b')],
            [self._read('a', 'a'), {'id': 'b', 'operation': 'repo.write_file',
                                     'arguments': {'path': 'x', 'content': 'evil'}}],
            [self._read('a', 'a'), {'id': 'b', 'operation': 'process.run',
                                     'arguments': {'argv': ['sh']}}],
            [self._read('x', 'a')] * 9,
            [self._read('a', 'a'), self._read('b', '')],
        ]
        with patch.object(self.runtime, '_op_repo_read') as read:
            for calls in bad:
                with self.subTest(calls=calls):
                    result = self._run(calls)
                    self.assertFalse(result.success)
            read.assert_not_called()

    def test_positional_table_requires_exact_host_schema_digest(self):
        from kitt.runtime.read_batch import POSITIONAL_DIGEST
        rows = [{'id': 'a', 'operation': 'repo.read', 'path': 'README.md'},
                {'id': 'b', 'operation': 'repo.read', 'path': 'pom.xml'}]
        with patch.object(self.runtime, '_op_repo_read',
                          side_effect=lambda args, *_: SafeRuntimeResult(
                              True, 'repo.read', data={'path': args['path']})) as read:
            good = self.runtime.execute('repo.batch_read',
                {'calls': rows, 'schema_digest': POSITIONAL_DIGEST},
                effective_capabilities={CAP_REPO_READ})
            self.assertTrue(good.success, good.error)
            self.assertEqual([r['data']['path'] for r in good.data['calls']],
                             ['README.md', 'pom.xml'])
            read.reset_mock()
            denied = self.runtime.execute('repo.batch_read',
                {'calls': rows, 'schema_digest': 'invented-by-model'},
                effective_capabilities={CAP_REPO_READ})
            self.assertFalse(denied.success)
            read.assert_not_called()

    def test_missing_capability_and_per_call_error_fail_closed(self):
        denied = self._run([self._read('a', 'a')], caps=set())
        self.assertFalse(denied.success)
        with patch.object(self.runtime, '_op_repo_read',
                          return_value=SafeRuntimeResult(False, 'repo.read', error='denied')):
            result = self._run([self._read('a', 'a')])
        self.assertFalse(result.success)
        self.assertFalse(result.data['calls'][0]['success'])
        self.assertEqual(result.data['calls'][0]['error'], 'denied')


if __name__ == '__main__':
    unittest.main()
