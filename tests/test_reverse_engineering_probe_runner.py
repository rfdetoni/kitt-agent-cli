import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from kitt.extensions.builtin_plugins.reverse_engineering.probe_runner import run_probe, ProbeError


class ProbeRunnerTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX shell fixture")
    def test_manifest_pin_and_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            p = root / 'probe.sh'
            p.write_text('#!/bin/sh\nprintf "observed\\n"\n')
            p.chmod(0o700)
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'probes': {'observed': {'path': 'probe.sh', 'sha256': digest}}}))
            result = run_probe(root, manifest, 'observed')
            self.assertEqual(result['exit_code'], 0)
            self.assertEqual(result['stdout'], 'observed\n')
            p.write_text('#!/bin/sh\nprintf "tampered\\n"\n')
            with self.assertRaises(ProbeError):
                run_probe(root, manifest, 'observed')

    def test_unregistered_is_denied(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / 'manifest.json'
            manifest.write_text('{"probes":{}}')
            with self.assertRaises(ProbeError):
                run_probe(root, manifest, '../probe')
            with self.assertRaises(ProbeError):
                run_probe(root, manifest, 'other')


if __name__ == '__main__':
    unittest.main()
