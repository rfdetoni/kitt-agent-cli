import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InstallerServiceShutdownTests(unittest.TestCase):
    def test_posix_installer_stops_daemon_before_mutating_install_tree(self):
        text = (ROOT / "install.sh").read_text(encoding="utf-8")

        stop_call = text.index("stop_kitt_services\n")
        sync_call = text.index('sync_repo "$REPO_URL"')
        self.assertLess(stop_call, sync_call)
        self.assertIn("kitt-daemon.service", text)
        self.assertIn("com.kitt.daemon", text)
        self.assertIn('"$existing_kitt" daemon stop', text)
        self.assertIn('[[ "$pid" != "$$" && "$pid" != "$PPID" ]]', text)
        self.assertIn("Python 3.14+ is required", text)
        self.assertIn("HistoryDatabase(\":memory:\", in_memory=True)", text)

    def test_installer_repairs_existing_re_lab_and_supports_explicit_opt_in(self):
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('--with-re-tools) WITH_RE_TOOLS=1', text)
        self.assertIn('--no-re-tools) SKIP_RE_TOOLS=1', text)
        self.assertIn('re lab images prepare --runtime docker', text)
        self.assertIn('-d "$HOME/.local/share/kitt/re-lab"', text)
        self.assertIn('SKIP_RE_TOOLS -eq 0', text)
        self.assertNotIn('--accept-license --yes', text)

    def test_windows_installer_stops_daemon_before_mutating_install_tree(self):
        text = (ROOT / "install.ps1").read_text(encoding="utf-8")

        stop_call = text.index("Stop-KittServices\n")
        sync_call = text.index("Sync-Repo $Repo $Ref $Src")
        self.assertLess(stop_call, sync_call)
        self.assertIn("'KITT Daemon'", text)
        self.assertIn("& $ExistingKitt daemon stop", text)
        self.assertIn("Get-Service", text)
        self.assertIn("Stop-Process", text)
        self.assertIn("Python 3.14+ is required", text)
        self.assertIn("HistoryDatabase(':memory:', in_memory=True)", text)


if __name__ == "__main__":
    unittest.main()
