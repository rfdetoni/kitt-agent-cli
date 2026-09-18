import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from kitt import __version__
from kitt.cli.main import build_parser
from kitt.ui.fallback import PlainLineUI


class _Runtime:
    def __init__(self, root: str):
        self.canonical_root = Path(root)


class CliBannerVersionTests(unittest.TestCase):
    def test_cli_version_flag_reports_installed_agent_version(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exit_context:
            build_parser().parse_args(["--version"])

        self.assertEqual(exit_context.exception.code, 0)
        self.assertIn(__version__, output.getvalue())

    def test_plain_banner_reports_installed_package_version(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
            output = io.StringIO()
            ui = PlainLineUI(_Runtime(temp), output_stream=output)
            ui.print_banner()

            rendered = output.getvalue()
            self.assertIn(f"K.I.T.T. Agent CLI v{__version__} — SYSTEM ONLINE", rendered)
            self.assertIn(f"Workspace: {Path(temp)}", rendered)


if __name__ == "__main__":
    unittest.main()
