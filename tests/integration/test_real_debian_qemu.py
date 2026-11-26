"""Real QEMU Debian Minimal lifecycle E2E test (P0-03).

Skips cleanly when qemu-system-x86_64, cloud-localds, or KITT_DEBIAN_IMAGE_SHA512
is not configured in the execution environment; never uses mocks as a substitute.
"""
from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path
import tempfile

from kitt.vm_debian import prepare_debian
from kitt.vm_supervisor import inspect_profile, run_vm_job


class RealDebianQemuTests(unittest.TestCase):
    def test_real_debian_provision_run_restart_persistence(self) -> None:
        required = ("qemu-system-x86_64", "qemu-img", "cloud-localds", "ssh", "ssh-keygen")
        missing = [tool for tool in required if shutil.which(tool) is None]
        if missing:
            self.skipTest(f"Missing real QEMU tools: {', '.join(missing)}")
        image_sha512 = os.getenv("KITT_DEBIAN_IMAGE_SHA512", "").strip()
        agent_sha = os.getenv("KITT_AGENT_COMMIT_SHA", "").strip()
        if not image_sha512 or not agent_sha:
            self.skipTest("Set KITT_DEBIAN_IMAGE_SHA512 and KITT_AGENT_COMMIT_SHA to run real Debian QEMU E2E")

        with tempfile.TemporaryDirectory(prefix="kitt-real-vm-") as tmp:
            dest = Path(tmp) / "debian-agent"
            profile = prepare_debian(dest, gateway_port=0, agent_sha=agent_sha, image_sha512=image_sha512)
            doctor = inspect_profile(profile)
            self.assertTrue(doctor["ready"])
            first = run_vm_job(profile, "echo real-qemu-pass")
            self.assertEqual(first["guest"]["status"], "PASS")
            second = run_vm_job(profile, "echo real-qemu-reboot-pass")
            self.assertEqual(second["guest"]["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
