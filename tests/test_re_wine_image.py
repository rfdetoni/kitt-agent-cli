"""WineHQ Staging image contracts and non-destructive Linux guest upgrades."""
from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from kitt import re_lab, vm_container
from kitt.cli.main import build_parser
from kitt.vm_supervisor import VMError


class WineImageContracts(unittest.TestCase):
    def test_distinct_debian_and_winehq_staging_packages_and_launchers(self):
        recipe = (Path(vm_container.__file__).with_name("vm_assets") /
                  "Dockerfile.re").read_text("utf-8")
        self.assertIn("FROM base AS wine", recipe)
        self.assertIn("wine wine64 mingw-w64", recipe)
        self.assertIn("wine-staging-amd64", recipe)
        self.assertNotIn("winehq-staging", recipe)
        self.assertIn("D43F640145369C51D786DDEA76F1A20FF987672F", recipe)
        self.assertIn("Signed-By: /etc/apt/keyrings/winehq-archive.asc", recipe)
        self.assertIn("wine --version", recipe)
        self.assertIn("wine64 --version", recipe)
        self.assertIn("wine-staging --version", recipe)
        self.assertIn("re-wine-packages.lock", recipe)
        self.assertIn("/opt/wine-staging/bin/", recipe)
        self.assertNotIn("trusted=yes", recipe)
        self.assertNotIn("apt-key", recipe)
        self.assertNotIn("curl |", recipe)

    def test_re_lab_upgrade_requires_explicit_approval(self):
        with patch.object(re_lab, "_root", side_effect=AssertionError("no host access")):
            with self.assertRaisesRegex(VMError, "explicit --approve"):
                re_lab.upgrade_linux()
        args = build_parser().parse_args(["re", "lab", "upgrade-linux", "--approve"])
        self.assertTrue(args.approve)
        self.assertEqual(args.lab_action, "upgrade-linux")

    @staticmethod
    def _fixtures(tmp):
        root = tmp / "re-lab"
        workspace = root / "workspace"
        workspace.mkdir(parents=True)
        (workspace / "important.txt").write_text("KEEP")
        old_image = "sha256:" + "a" * 64
        next_image = "sha256:" + "b" * 64
        managed = {
            "managed": True, "status": "running",
            "container_id": "c" * 64, "workspace": str(workspace),
        }
        return root, workspace, managed, old_image, next_image

    def test_upgrade_preflights_and_preserves_workspace_and_windows(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as temp:
            root, workspace, managed, old, nxt = self._fixtures(Path(temp))
            calls = []
            created = []
            def fake_call(argv, **_):
                calls.append(list(argv))
                if "inspect" in argv:
                    return old
                return ""
            with (patch.object(re_lab, "_root", return_value=root),
                  patch.object(re_lab.re_docker_lab, "inspect", return_value=managed),
                  patch.object(re_lab.re_docker_lab, "operate",
                               side_effect=lambda role, action: calls.append(["operate", role, action])),
                  patch.object(re_lab.shutil, "which", return_value="/usr/bin/docker"),
                  patch.object(vm_container, "_image_id", return_value=nxt),
                  patch.object(re_lab, "_call", side_effect=fake_call),
                  patch.object(re_lab, "_create_linux_container",
                               side_effect=lambda path, image: created.append((path, image)))):
                result = re_lab.upgrade_linux(approve=True)
            self.assertEqual(result["status"], "UPGRADED")
            self.assertTrue(result["workspace_preserved"])
            self.assertTrue(result["windows_preserved"])
            self.assertEqual(created, [(root, nxt)])
            self.assertEqual((workspace / "important.txt").read_text(), "KEEP")
            indices = [n for n, argv in enumerate(calls) if argv[0] == "run"]
            self.assertEqual(len(indices), 1)
            self.assertLess(indices[0], next(i for i, argv in enumerate(calls) if argv[0] == "rm"))
            self.assertTrue(all("windows" not in argv for argv in calls))

    def test_upgrade_rolls_back_if_new_container_creation_fails(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as temp:
            root, workspace, managed, old, nxt = self._fixtures(Path(temp))
            calls = []
            creates = []
            checks = [managed, None]
            def create(root_path, image):
                creates.append(image)
                if image == nxt:
                    raise VMError("candidate failed")
            def fake_call(argv, **_):
                calls.append(list(argv))
                return old if "inspect" in argv else ""
            with (patch.object(re_lab, "_root", return_value=root),
                  patch.object(re_lab.re_docker_lab, "inspect", side_effect=lambda role: checks.pop(0)),
                  patch.object(re_lab.re_docker_lab, "operate", return_value={}),
                  patch.object(re_lab.shutil, "which", return_value="/usr/bin/docker"),
                  patch.object(vm_container, "_image_id", return_value=nxt),
                  patch.object(re_lab, "_call", side_effect=fake_call),
                  patch.object(re_lab, "_create_linux_container", side_effect=create)):
                with self.assertRaisesRegex(VMError, "previous image restored"):
                    re_lab.upgrade_linux(approve=True)
            self.assertEqual(creates, [nxt, old])
            self.assertEqual((workspace / "important.txt").read_text(), "KEEP")

    def test_no_guest_mutation_if_candidate_preflight_fails(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as temp:
            root, workspace, managed, old, nxt = self._fixtures(Path(temp))
            calls = []
            def fake_call(argv, **_):
                calls.append(list(argv))
                if "inspect" in argv:
                    return old
                raise VMError("staging binary missing")
            with (patch.object(re_lab, "_root", return_value=root),
                  patch.object(re_lab.re_docker_lab, "inspect", return_value=managed),
                  patch.object(re_lab.shutil, "which", return_value="/usr/bin/docker"),
                  patch.object(vm_container, "_image_id", return_value=nxt),
                  patch.object(re_lab, "_call", side_effect=fake_call)):
                with self.assertRaisesRegex(VMError, "staging binary missing"):
                    re_lab.upgrade_linux(approve=True)
            self.assertFalse(any(argv[0] == "rm" for argv in calls))
            self.assertEqual((workspace / "important.txt").read_text(), "KEEP")


if __name__ == "__main__":
    unittest.main()
