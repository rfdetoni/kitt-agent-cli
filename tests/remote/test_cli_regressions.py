import inspect
import unittest

from kitt.cli.main import build_parser
from kitt.daemon import process


class CliRegressionTests(unittest.TestCase):
    def test_daemon_run_is_parseable(self):
        args = build_parser().parse_args(["--root", ".", "daemon", "run"])
        self.assertEqual(args.subcommand, "daemon")
        self.assertEqual(args.daemon_action, "run")

    def test_remote_and_web_aliases_are_parseable(self):
        remote = build_parser().parse_args(["--root", ".", "remote", "--lan", "--port", "7337"])
        web = build_parser().parse_args(["--root", ".", "web", "--port", "0"])
        self.assertEqual(remote.subcommand, "remote")
        self.assertTrue(remote.lan)
        self.assertEqual(web.subcommand, "web")
        self.assertEqual(web.port, 0)

    def test_detached_launcher_no_longer_uses_unsupported_workspace_flag(self):
        source = inspect.getsource(process.start_daemon_detached)
        self.assertIn('"--root"', source)
        self.assertIn('"run"', source)
        self.assertNotIn('"--workspace"', source)


if __name__ == "__main__":
    unittest.main()
