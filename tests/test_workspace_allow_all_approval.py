from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from kitt.core.autonomy_policy import AutonomyPolicy
from kitt.ui.runtime_actions import _resolve_approval_once


class TestWorkspaceAllowAllApproval(unittest.IsolatedAsyncioTestCase):
    def make_ui(self, *, autonomy_response=None):
        policy = SimpleNamespace(autonomy=AutonomyPolicy.preset("supervised"))
        store = SimpleNamespace(set_preset=Mock(side_effect=AutonomyPolicy.preset))
        state = SimpleNamespace(
            pending_approval={"approval_id": "req-1", "tool_name": "run_command"},
            pending_approvals=[{"approval_id": "req-1", "tool_name": "run_command"}],
            active_overlay=None,
            status_text="APPROVAL",
            add_toast=Mock(),
        )
        bridge = SimpleNamespace(
            daemon_mode=True,
            resolve_approval=AsyncMock(return_value={"status": "ok", "decision": "approved"}),
            remember_approval=AsyncMock(return_value={"status": "ok"}),
            set_autonomy=AsyncMock(return_value=autonomy_response or {"status": "ok", "preset": "autonomous"}),
        )
        ui = SimpleNamespace(
            state=state,
            bridge=bridge,
            runtime=SimpleNamespace(autonomy_store=store, processor=SimpleNamespace(
                registry=SimpleNamespace(policy=policy))),
            application=None,
            _ensure_daemon_management=AsyncMock(return_value=True),
        )
        return ui, policy

    async def test_allow_all_approves_pending_then_enables_autonomous_for_workspace(self):
        ui, policy = self.make_ui()
        order = []
        async def approve(*args):
            order.append("approve")
            return {"status": "ok", "decision": "approved"}
        async def set_mode(*args):
            order.append("autonomous")
            return {"status": "ok", "preset": "autonomous"}
        ui.bridge.resolve_approval.side_effect = approve
        ui.bridge.set_autonomy.side_effect = set_mode

        await _resolve_approval_once(ui, "allow_all")

        self.assertEqual(order, ["approve", "autonomous"])
        self.assertEqual(policy.autonomy.level, "autonomous")
        self.assertEqual(ui.state.pending_approvals, [])
        ui.runtime.autonomy_store.set_preset.assert_called_once_with("autonomous")
        ui.bridge.remember_approval.assert_not_awaited()

    async def test_mode_change_failure_does_not_reopen_consumed_approval(self):
        ui, policy = self.make_ui(autonomy_response={"status": "error", "error": "unavailable"})

        await _resolve_approval_once(ui, "allow_all")

        self.assertEqual(ui.state.pending_approvals, [])
        self.assertEqual(policy.autonomy.level, "supervised")
        ui.bridge.resolve_approval.assert_awaited_once_with("req-1", True)
        ui.state.add_toast.assert_any_call(
            "Ação aprovada; allow-all não ativado: unavailable", persistent=True,
        )

    async def test_remembering_one_tool_does_not_enable_global_allow_all(self):
        ui, policy = self.make_ui()

        await _resolve_approval_once(ui, "always_workspace")

        ui.bridge.remember_approval.assert_awaited_once()
        ui.bridge.set_autonomy.assert_not_awaited()
        self.assertEqual(policy.autonomy.level, "supervised")
