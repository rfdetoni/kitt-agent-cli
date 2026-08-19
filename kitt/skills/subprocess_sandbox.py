from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from kitt.skills.executable import SkillResult, validate_skill_ast

logger = logging.getLogger(__name__)


class SubprocessSkillSandbox:
    """Spawns an isolated worker subprocess to execute untrusted skills with RPC capability mediation."""

    def __init__(self, runtime: Any, timeout_seconds: float = 30.0):
        self.runtime = runtime
        self.timeout = timeout_seconds

    def execute(
        self,
        skill_name: str,
        source: str,
        arguments: Dict[str, Any],
        capabilities: List[str],
        call_stack: Optional[Set[str]] = None,
    ) -> SkillResult:
        start = time.perf_counter()

        # 1. Static AST Security Gate
        validate_skill_ast(source)

        # 2. Spawn sandboxed worker subprocess
        env = dict(os.environ)
        # Ensure project root is in PYTHONPATH
        repo_root = str(Path(__file__).resolve().parents[2])
        current_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{repo_root}:{current_pp}" if current_pp else repo_root

        cmd = [sys.executable, "-m", "kitt.skills.sandbox_worker"]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )

        import selectors
        sel = selectors.DefaultSelector()
        if proc.stdout:
            sel.register(proc.stdout, selectors.EVENT_READ)

        deadline = time.time() + self.timeout
        try:
            # Send initial payload
            init_payload = {
                "skill_name": skill_name,
                "source": source,
                "arguments": arguments,
                "capabilities": capabilities,
            }
            if proc.stdin:
                proc.stdin.write(json.dumps(init_payload, ensure_ascii=False) + "\n")
                proc.stdin.flush()

            # Non-blocking RPC loop with deadline
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(cmd, self.timeout)

                events = sel.select(timeout=min(0.2, remaining))
                if not events:
                    if proc.poll() is not None:
                        break
                    continue

                for key, mask in events:
                    line = proc.stdout.readline() if proc.stdout else ""
                    if not line:
                        break

                    try:
                        msg = json.loads(line.strip())
                    except Exception:
                        continue

                    msg_type = msg.get("type")
                    if msg_type == "RESULT":
                        dur = (time.perf_counter() - start) * 1000
                        return SkillResult(
                            success=msg.get("success", False),
                            data=msg.get("data"),
                            error=msg.get("error"),
                            duration_ms=dur,
                        )

                    elif msg_type == "RPC_CALL":
                        method = msg.get("method", "")
                        params = msg.get("params", {})
                        rpc_res = self._handle_rpc(method, params, capabilities)
                        if proc.stdin:
                            proc.stdin.write(json.dumps(rpc_res, ensure_ascii=False) + "\n")
                            proc.stdin.flush()

            # Process exited without sending RESULT
            dur = (time.perf_counter() - start) * 1000
            if proc.returncode != 0 and proc.returncode is not None:
                err = proc.stderr.read() if proc.stderr else "Process terminated abnormally"
                return SkillResult(success=False, error=f"Skill worker crashed: {err}", duration_ms=dur)
            return SkillResult(success=False, error="Skill completed without returning a result", duration_ms=dur)

        except subprocess.TimeoutExpired:
            dur = (time.perf_counter() - start) * 1000
            return SkillResult(success=False, error=f"Skill '{skill_name}' timed out after {self.timeout}s", duration_ms=dur)
        except Exception as exc:
            dur = (time.perf_counter() - start) * 1000
            return SkillResult(success=False, error=f"Skill execution failed: {exc}", duration_ms=dur)
        finally:
            try:
                sel.close()
            except Exception:
                pass
            try:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
            except Exception:
                pass
            try:
                if proc.stdout and not proc.stdout.closed:
                    proc.stdout.close()
            except Exception:
                pass
            try:
                if proc.stderr and not proc.stderr.closed:
                    proc.stderr.close()
            except Exception:
                pass
            if proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait(timeout=1.0)
                except Exception:
                    pass

    def _handle_rpc(self, method: str, params: Dict[str, Any], declared_caps: List[str]) -> Dict[str, Any]:
        """Dispatch RPC call from subprocess to host SafeRuntime."""
        safe_rt = self.runtime
        if not hasattr(safe_rt, "execute"):
            from kitt.runtime.safe_runtime import SafeRuntime
            root_p = getattr(
                self.runtime,
                "canonical_root",
                getattr(self.runtime, "root_path", getattr(self.runtime, "root", Path("."))),
            )
            safe_rt = SafeRuntime(
                workspace_root=root_p,
                workspace_id=getattr(self.runtime, "workspace_id", "default"),
                conversation_id="skill_exec",
                tool_registry=getattr(self.runtime, "registry", None),
                repository_index=getattr(self.runtime, "repository_index", None),
                artifact_store=getattr(self.runtime, "artifacts", None),
                child_manager=getattr(self.runtime, "children", None),
                goal_service=getattr(self.runtime, "goals", None),
                db=getattr(self.runtime, "database", None),
            )

        res = safe_rt.execute(
            method,
            params,
            effective_capabilities=set(declared_caps),
            origin="SKILL",
        )
        return {
            "success": res.success,
            "data": res.data,
            "error": res.error,
        }
