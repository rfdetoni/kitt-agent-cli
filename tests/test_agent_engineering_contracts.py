from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from kitt.context.epoch import build_context_epoch
from kitt.core.runtime import KittRuntime
from kitt.extensions.errors import PluginPermissionError
from kitt.extensions.manifest import parse_manifest_data
from kitt.extensions.plugins.api import ToolAPI
from kitt.tools.approval import ApprovalManager


def test_saved_permission_is_scoped_to_executable_identity():
    approval = ApprovalManager(workspace_id="ws")
    approval.remember(
        "write_file",
        "**",
        "allow",
        "workspace",
        executable_identity="CHILD:child-a",
    )

    assert (
        approval.check_remembered(
            "write_file",
            "src/app.py",
            workspace_id="ws",
            executable_identity="CHILD:child-a",
        )
        == "allow"
    )
    assert (
        approval.check_remembered(
            "write_file",
            "src/app.py",
            workspace_id="ws",
            executable_identity="CHILD:child-b",
        )
        is None
    )
    saved = approval.saved_permissions(workspace_id="ws")
    assert len(saved) == 1
    assert saved[0].executable_identity == "CHILD:child-a"


def test_plugin_capabilities_narrow_declared_tool_exports():
    manifest = parse_manifest_data(
        {
            "name": "bounded",
            "version": "1.0.0",
            "api_version": "1",
            "entrypoint": "plugin:main",
            "permissions": ["tools.register"],
            "capabilities": {"tools": ["declared_tool"]},
        }
    )
    assert manifest.capabilities.tools == ("declared_tool",)

    api = ToolAPI(
        manifest.name,
        manifest.permissions,
        tool_registry=None,
        declared_tools=manifest.capabilities.tools,
    )
    with pytest.raises(PluginPermissionError):
        api.register("undeclared_tool", lambda _args: "no")


def test_context_epoch_changes_when_effective_policy_context_changes():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        processor = SimpleNamespace(
            root_path=Path(tmp),
            event_ledger=None,
            registry=SimpleNamespace(
                policy=SimpleNamespace(
                    autonomy=SimpleNamespace(
                        to_dict=lambda: {"level": "supervised"}
                    )
                ),
                approval_manager=SimpleNamespace(remembered_rules=[]),
                runtime_operation_names=lambda: ("repo.read",),
            ),
        )
        common = dict(
            processor=processor,
            conversation_id="conv",
            turn_id="turn",
            memory_context="memory",
            harness_context="harness",
            repo_map="repo",
            files_context="file evidence",
            guidelines_context="guideline",
            skills_context="skill",
            tool_definitions=[{"name": "kitt_runtime"}],
            provider_profile=SimpleNamespace(
                backend="local",
                model="model",
                base_url="",
                protocol="",
                context_window=8192,
                max_output_tokens=1024,
                supports_tools=True,
                supports_json=True,
                enforce_local_limits=True,
            ),
        )
        first = build_context_epoch(
            **common,
            policy_context={"formatting": "a"},
        )
        same = build_context_epoch(
            **common,
            policy_context={"formatting": "a"},
        )
        changed = build_context_epoch(
            **common,
            policy_context={"formatting": "b"},
        )

        assert first.snapshot_digest == same.snapshot_digest
        assert first.epoch_id == same.epoch_id
        assert changed.epoch_id != first.epoch_id


def test_workspace_snapshot_restores_existing_and_removes_created_files():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        existing = root / "existing.txt"
        created = root / "created.txt"
        existing.write_text("before\n", encoding="utf-8")

        with KittRuntime.build(tmp) as runtime:
            conversation = runtime.history.new_conversation("snapshot")
            snapshot = runtime.workspace_snapshots.capture(
                conversation_id=conversation["id"],
                turn_id="snapshot-turn",
                paths=["existing.txt", "created.txt"],
            )

            existing.write_text("after\n", encoding="utf-8")
            created.write_text("new\n", encoding="utf-8")

            restored = runtime.workspace_snapshots.restore(
                snapshot.snapshot_id,
                conversation_id=conversation["id"],
                turn_id="snapshot-turn",
            )

            assert existing.read_text(encoding="utf-8") == "before\n"
            assert not created.exists()
            assert set(restored) == {"existing.txt", "created.txt"}
