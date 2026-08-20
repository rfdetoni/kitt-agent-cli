from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional, Set

from kitt.security.context import ExecutionSecurityContext
from kitt.skills.executable import SkillResult, validate_skill_ast


class SubprocessSkillSandbox:
    """Run executable skills out-of-process and broker host calls explicitly."""

    def __init__(self, runtime: Any, timeout_seconds: float = 30.0):
        self.runtime = runtime
        self.timeout = timeout_seconds

    @staticmethod
    def _minimal_env():
        # Never forward provider secrets/tokens into untrusted skill workers.
        keep = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}
        return {key: value for key, value in os.environ.items() if key in keep}

    @staticmethod
    def _unix_resource_limits():
        if sys.platform == "win32":
            return None

        def apply_limits():
            import resource

            limits = (
                (resource.RLIMIT_CPU, 10, 12),
                (resource.RLIMIT_AS, 256 * 1024 * 1024, 256 * 1024 * 1024),
                (resource.RLIMIT_NOFILE, 32, 32),
                (resource.RLIMIT_FSIZE, 2 * 1024 * 1024, 2 * 1024 * 1024),
            )
            for kind, soft, hard in limits:
                try:
                    resource.setrlimit(kind, (soft, hard))
                except (ValueError, OSError):
                    pass
            if hasattr(resource, "RLIMIT_NPROC"):
                try:
                    resource.setrlimit(resource.RLIMIT_NPROC, (8, 8))
                except (ValueError, OSError):
                    pass

        return apply_limits

    @staticmethod
    def _kill_tree(proc):
        if proc.poll() is not None:
            return
        if sys.platform != "win32":
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=1)
                return
            except Exception:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    pass
        else:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        try:
            proc.kill()
        except Exception:
            pass

    def execute(
        self,
        skill_name,
        source,
        arguments,
        capabilities,
        call_stack: Optional[Set[str]] = None,
        security_context: Optional[ExecutionSecurityContext] = None,
    ):
        start = time.perf_counter()
        validate_skill_ast(source)

        worker_path = Path(__file__).with_name("sandbox_worker.py").resolve()
        command = [sys.executable, "-I", str(worker_path)]
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=self._minimal_env(),
            start_new_session=(sys.platform != "win32"),
            preexec_fn=self._unix_resource_limits(),
        )
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + self.timeout
        try:
            process.stdin.write(
                json.dumps(
                    {
                        "skill_name": skill_name,
                        "source": source,
                        "arguments": arguments,
                        "capabilities": capabilities,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            process.stdin.flush()

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"Skill '{skill_name}' timed out")
                if process.poll() is not None and not selector.select(timeout=0):
                    break
                for key, _ in selector.select(timeout=min(0.2, remaining)):
                    line = key.fileobj.readline()
                    if not line:
                        continue
                    try:
                        message = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if message.get("type") == "RESULT":
                        return SkillResult(
                            success=bool(message.get("success")),
                            data=message.get("data"),
                            error=message.get("error"),
                            duration_ms=(time.perf_counter() - start) * 1000,
                        )
                    if message.get("type") == "RPC_CALL":
                        response = self._handle_rpc(
                            message.get("method", ""),
                            message.get("params", {}),
                            capabilities,
                            security_context,
                        )
                        process.stdin.write(json.dumps(response, ensure_ascii=False) + "\n")
                        process.stdin.flush()

            error = process.stderr.read()[-4000:] if process.stderr else ""
            return SkillResult(
                False,
                error=f"Skill worker exited without result: {error}",
                duration_ms=(time.perf_counter() - start) * 1000,
            )
        except TimeoutError as exc:
            return SkillResult(
                False,
                error=str(exc),
                duration_ms=(time.perf_counter() - start) * 1000,
            )
        finally:
            selector.close()
            self._kill_tree(process)

    def _handle_rpc(self, method, params, declared_caps, security_context):
        from kitt.runtime.safe_runtime import SafeRuntime

        safe_runtime = (
            self.runtime
            if hasattr(self.runtime, "execute")
            else SafeRuntime(
                workspace_root=getattr(self.runtime, "canonical_root", Path(".")),
                workspace_id=getattr(self.runtime, "workspace_id", ""),
                conversation_id=(
                    security_context.conversation_id
                    if security_context
                    else "skill_exec"
                ),
                tool_registry=getattr(self.runtime, "registry", None),
                repository_index=getattr(self.runtime, "repository_index", None),
                artifact_store=getattr(self.runtime, "artifacts", None),
                child_manager=getattr(self.runtime, "children", None),
                goal_service=getattr(self.runtime, "goals", None),
                db=getattr(self.runtime, "database", None),
            )
        )
        if security_context is None:
            return {"success": False, "error": "Missing parent security context"}

        # Never reconstruct a new unrestricted principal here. A skill can only
        # narrow its parent's capabilities and inherits the parent's path scope.
        skill_context = security_context.derive_skill_context(
            skill_id=f"skill:{method}",
            requested_capabilities=declared_caps,
            turn_id=security_context.turn_id,
        )
        result = safe_runtime.execute(
            method,
            params,
            origin="MODEL",
            security_context=skill_context,
        )
        return {
            "success": result.success,
            "data": result.data,
            "error": result.error,
            "requires_approval": result.requires_approval,
            "approval_action": result.approval_action,
        }
