from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "posix", reason="POSIX reparenting contract")
def test_abrupt_parent_loss_cancels_real_tool_process_before_mutation(tmp_path):
    worker = tmp_path / "guarded_worker.py"
    marker = tmp_path / "side_effect.txt"
    ready = tmp_path / "ready"
    worker.write_text("""import os, sys
from pathlib import Path
from kitt.children.supervision import ParentProcessGuard
from kitt.core.cancellation import CancellationRegistry
from kitt.tools.process_runner import ProcessRunner
from kitt.security.execution_sandbox import ExecutionSandbox
root = Path(sys.argv[1])
registry = CancellationRegistry()
guard = ParentProcessGuard(int(sys.argv[2]), registry.cancel_all)
runner = ProcessRunner(root, sandbox=ExecutionSandbox(root, auto_detect=False))
runner.run([sys.executable, '-c', "import time; from pathlib import Path; Path('ready').write_text('ready'); time.sleep(3); Path('side_effect.txt').write_text('unsafe')"], timeout_seconds=10, cancellation=registry.token('turn'), sandbox_profile='full-access')
guard.close()
""")
    parent_code = "import subprocess,sys,os,time; p=subprocess.Popen([sys.executable,sys.argv[1],sys.argv[2],str(os.getpid())]); print(p.pid,flush=True); time.sleep(20)"
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_code, str(worker), str(tmp_path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert int(parent.stdout.readline().strip()) > 1
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "worker must start the real execution path"
        parent.kill()
        parent.wait(timeout=2)
        time.sleep(3.2)
        assert not marker.exists(), "orphaned tool must not finish its delayed mutation"
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=2)
        parent.stdout.close()
