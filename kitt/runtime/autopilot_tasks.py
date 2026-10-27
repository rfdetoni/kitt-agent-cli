"""Recoverable, bounded autonomy checkpoints and evidence-backed test gates."""
from __future__ import annotations

import json
import os
from pathlib import Path


class AutopilotTaskService:
    STATE_KEY = "autopilot.task.checkpoint"
    VERIFY_KEY = "autopilot.task.verification"
    EXCLUDED = frozenset({
        ".git", ".kitt", "node_modules", ".venv", "venv", "target",
        "build", "dist", ".gradle", ".idea", ".next", "__pycache__",
    })

    def __init__(self, runtime):
        self.runtime = runtime

    def _snapshots(self):
        service = getattr(self.runtime.registry, "workspace_snapshot_service", None)
        if service is None:
            raise RuntimeError("workspace snapshot ledger is unavailable")
        if self.runtime.state is None:
            raise RuntimeError("durable conversation state is unavailable")
        return service

    def _record(self) -> dict:
        value = self.runtime.state.get(self.STATE_KEY)
        if not isinstance(value, dict) or not value.get("snapshot_id"):
            raise ValueError("No recoverable task checkpoint was established")
        return value

    def checkpoint(self, args: dict, turn_id: str) -> dict:
        service = self._snapshots()
        if not isinstance(args, dict) or set(args) - {"paths"}:
            raise ValueError("Unsupported checkpoint arguments")
        paths = args.get("paths")
        if paths is None:
            paths = sorted(
                entry.name for entry in self.runtime.root.iterdir()
                if entry.name not in self.EXCLUDED and not entry.is_symlink()
            )
        if (not isinstance(paths, list) or not paths or len(paths) > 64
                or not all(isinstance(path, str) and path for path in paths)):
            raise ValueError("Checkpoint requires 1..64 file/directory paths")
        if any(
            Path(path).is_absolute() or
            set(Path(path).parts) & {".git", ".kitt"} or
            ".." in Path(path).parts
            for path in paths
        ):
            raise PermissionError("Checkpoint cannot include protected or external paths")
        snapshot = service.capture(
            conversation_id=self.runtime.conversation_id,
            turn_id=turn_id, paths=paths,
        )
        record = {
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_turn": turn_id, "paths": list(snapshot.changed_paths),
        }
        self.runtime.state.set(self.STATE_KEY, record)
        self.runtime.state.delete(self.VERIFY_KEY)
        return record

    def diff(self) -> dict:
        record = self._record()
        changes = self._snapshots().diff(
            record["snapshot_id"],
            conversation_id=self.runtime.conversation_id,
            turn_id=record["snapshot_turn"],
        )
        return {
            "checkpoint": record, "changes": changes,
            "expected_current": {item["path"]: item["current_sha256"] for item in changes},
        }

    def rollback(self, args: dict) -> dict:
        if not isinstance(args, dict) or set(args) - {"expected_current"}:
            raise ValueError("Unsupported rollback arguments")
        expected = args.get("expected_current")
        if not isinstance(expected, dict):
            raise ValueError("Rollback requires exact expected_current hashes from diff")
        record = self._record()
        restored = self._snapshots().restore(
            record["snapshot_id"],
            conversation_id=self.runtime.conversation_id,
            turn_id=record["snapshot_turn"],
            expected_current=expected,
        )
        self.runtime.state.delete(self.VERIFY_KEY)
        return {"restored_paths": restored, "checkpoint_id": record["snapshot_id"]}

    def _commands(self) -> list[list[str]]:
        value = os.environ.get("KITT_AUTOPILOT_VALIDATION", "").strip()
        if value:
            commands = json.loads(value)
        else:
            root = (self.runtime.autopilot_worktree.directory()
                    if hasattr(self.runtime, "autopilot_worktree")
                    else self.runtime.root)
            commands = []
            if (root / "pom.xml").is_file():
                commands.append(["mvn", "test"])
            if (root / "build.gradle").is_file() or (root / "build.gradle.kts").is_file():
                commands.append(["gradle", "test"])
            if (root / "package.json").is_file():
                commands.append(["npm", "test"])
            if (root / "Cargo.toml").is_file():
                commands.append(["cargo", "test"])
            if (root / "pyproject.toml").is_file():
                commands.append(["python", "-m", "pytest", "-q"])
        if (not isinstance(commands, list) or not 1 <= len(commands) <= 8
                or any(not isinstance(cmd, list) or not cmd or len(cmd) > 32
                       or not all(isinstance(x, str) and x and len(x) <= 1024 for x in cmd)
                       for cmd in commands)):
            raise ValueError("No bounded project validation commands are available")
        return commands

    def verify(self, args: dict, *, allow_network: bool) -> dict:
        if not isinstance(args, dict) or set(args) - {"network"}:
            raise ValueError("Unsupported validation arguments")
        network = args.get("network", False)
        if type(network) is not bool:
            raise ValueError("Validation network flag must be boolean")
        if network and not allow_network:
            raise PermissionError("No delegated network.access capability")
        runner = getattr(self.runtime.registry, "process_runner", None)
        if runner is None:
            raise RuntimeError("strong sandbox process runner unavailable")
        checks = []
        task = (self.runtime.autopilot_worktree.current()
                if hasattr(self.runtime, "autopilot_worktree") else None)
        task_cwd = task["path"] if task else "."
        self.runtime.state.delete(self.VERIFY_KEY)
        for command in self._commands():
            result = runner.run(
                command, cwd=task_cwd, timeout_seconds=1800,
                sandbox_profile="workspace-write+network" if network else "workspace-write",
                require_strong_sandbox=True,
                prefer_docker_toolchain=True,
            )
            checks.append({
                "argv": command,
                "exit_code": result.returncode,
                "timed_out": result.timed_out,
                "stdout": result.stdout[-2048:],
                "stderr": result.stderr[-4096:],
            })
            if result.returncode != 0 or result.timed_out:
                return {"ok": False, "checks": checks}
        report = {"ok": True, "checks": checks}
        if (task is not None or
                os.environ.get("KITT_AUTOPILOT_PUBLISH_ENABLED", "").lower() == "true"):
            from kitt.runtime.autopilot_publish import inventory
            workspace = (self.runtime.autopilot_worktree.directory()
                         if task is not None else self.runtime.root)
            snapshot = inventory(workspace)
            report["publication_snapshot"] = {
                "digest": snapshot["digest"], "head": snapshot["head"],
            }
        self.runtime.state.set(self.VERIFY_KEY, report)
        return report
