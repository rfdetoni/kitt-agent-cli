import contextlib
import io
import re
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
    def test_source_tree_fallback_matches_package_metadata(self):
        root = Path(__file__).resolve().parents[1]
        import tomllib
        with (root / "pyproject.toml").open("rb") as fh:
            current = tomllib.load(fh)["project"]["version"]
        source = (root / "kitt" / "__init__.py").read_text(encoding="utf-8")
        fallback = re.search(r'(?m)^\\s*__version__ = "([0-9]+\\.[0-9]+\\.[0-9]+)"
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
, source)
        self.assertIsNotNone(fallback)
        self.assertEqual(fallback.group(1), current)


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
