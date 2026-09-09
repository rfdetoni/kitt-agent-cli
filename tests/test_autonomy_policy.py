import unittest
from kitt.core.autonomy_policy import AutonomyPolicy
from kitt.tools.policy_engine import PolicyEngine

class TestAutonomyPolicy(unittest.TestCase):
    def test_preset_definitions(self):
        ro = AutonomyPolicy.preset("read_only")
        self.assertFalse(ro.allow_file_write_auto)
        self.assertFalse(ro.allow_run_command_auto)
        self.assertFalse(ro.allow_child_spawn_auto)

        sup = AutonomyPolicy.preset("supervised")
        self.assertFalse(sup.allow_file_write_auto)
        self.assertTrue(sup.allow_child_spawn_auto)

        bal = AutonomyPolicy.preset("balanced")
        self.assertTrue(bal.allow_file_write_auto)
        self.assertFalse(bal.allow_run_command_auto)

        aut = AutonomyPolicy.preset("autonomous")
        self.assertTrue(aut.allow_file_write_auto)
        self.assertTrue(aut.allow_run_command_auto)

        self.assertEqual(AutonomyPolicy.preset("allow_all").level, "autonomous")
        self.assertEqual(AutonomyPolicy.preset("ask").level, "supervised")
        self.assertEqual(AutonomyPolicy.preset("deny").level, "read_only")

    def test_run_command_menu_modes(self):
        command = {"command": "git status"}
        self.assertEqual(PolicyEngine(autonomy=AutonomyPolicy.preset("allow_all")).evaluate_tool("run_command", command), "ALLOW")
        self.assertEqual(PolicyEngine(autonomy=AutonomyPolicy.preset("ask")).evaluate_tool("run_command", command), "ASK")
        self.assertEqual(PolicyEngine(autonomy=AutonomyPolicy.preset("deny")).evaluate_tool("run_command", command), "DENY")

    def test_policy_engine_read_only_mode(self):
        engine = PolicyEngine(autonomy=AutonomyPolicy.preset("read_only"))
        self.assertEqual(engine.evaluate_tool("read_file", {"path": "src/app.py"}), "ALLOW")
        self.assertEqual(engine.evaluate_tool("write_file", {"path": "src/app.py"}), "DENY")
        self.assertEqual(engine.evaluate_tool("apply_patch", {"patch": "diff"}), "DENY")
        self.assertEqual(engine.evaluate_tool("run_command", {"command": "pytest"}), "DENY")

    def test_from_dict_preserves_flag_overrides(self):
        policy = AutonomyPolicy.from_dict({"level": "read_only", "allow_run_command_auto": True})
        self.assertTrue(policy.allow_run_command_auto)

    def test_preset_invalid_level_raises_valueerror(self):
        with self.assertRaises(ValueError):
            AutonomyPolicy.preset("autonmous")

    def test_model_and_nonmodel_origin_agree(self):
        for level in ("read_only", "supervised", "balanced", "autonomous"):
            engine = PolicyEngine(autonomy=AutonomyPolicy.preset(level))
            for tool_name, args in [
                ("apply_patch", {"patch": "diff"}),
                ("write_file", {"path": "test.txt", "content": "x"}),
                ("run_command", {"command": "pytest"}),
                ("child_spawn", {"task": "sub"}),
                ("read_file", {"path": "test.txt"}),
            ]:
                res_model = engine.evaluate_tool(tool_name, args, origin="MODEL")
                res_ui = engine.evaluate_tool(tool_name, args, origin="UI")
                self.assertEqual(res_model, res_ui, f"Mismatch for {tool_name} at {level}: MODEL={res_model}, UI={res_ui}")

    def test_denied_regardless_of_autonomy(self):
        for level in ("read_only", "supervised", "balanced", "autonomous"):
            engine = PolicyEngine(autonomy=AutonomyPolicy.preset(level))
            self.assertEqual(engine.evaluate_tool("run_command", {"command": "cat /etc/passwd"}), "DENY")
            self.assertEqual(engine.evaluate_tool("run_command", {"command": "rm -rf /"}), "DENY")
            self.assertEqual(engine.evaluate_tool("run_command", {"command": "git push"}), "DENY")
            self.assertEqual(engine.evaluate_tool("run_command", {"command": "git status; rm -rf ."}), "DENY")

    def test_rtk_proxy_evaluation(self):
        engine = PolicyEngine()
        self.assertEqual(engine.evaluate_command("rtk git status"), "ALLOW")
        self.assertEqual(engine.evaluate_command("rtk proxy git status"), "ALLOW")
        self.assertEqual(engine.evaluate_command("rtk git diff"), "ALLOW")
        self.assertEqual(engine.evaluate_command("rtk pytest"), "ASK")
        self.assertEqual(engine.evaluate_command("rtk cargo test"), "ASK")
        self.assertEqual(engine.evaluate_command("rtk find jetbrains-plugin/src extensions/kitt-ai/src backend -type f"), "ASK")
        self.assertEqual(engine.evaluate_command("rtk rg -n '^(def |async def |.*fun |function |export function|class )$' backend jetbrains-plugin extensions"), "ASK")
        self.assertEqual(engine.evaluate_command("rtk cat .env"), "DENY")
        self.assertEqual(engine.evaluate_command("rtk find . -delete"), "DENY")
        self.assertEqual(engine.evaluate_command("echo '$HOME'"), "ASK")

    def test_safe_find_requires_and_accepts_approval(self):
        import tempfile
        from kitt.tools.registry import ToolRegistry
        from kitt.security.context import ExecutionSecurityContext
        from kitt.runtime.safe_runtime import SafeRuntime

        with tempfile.TemporaryDirectory() as tmpdir:
            reg_sup = ToolRegistry(root_dir=tmpdir)
            reg_ro = None
            try:
                # Use a command with identical semantics on GitHub-hosted Linux,
                # macOS and Windows runners. `find` is a different executable on
                # Windows and made the approval test platform-dependent.
                command = 'python -c "print(\'ok\')"'
                sec_ctx = ExecutionSecurityContext.from_dict({
                    "workspace_id": "ws_test",
                    "conversation_id": "conv_test",
                    "turn_id": "turn_test",
                    "origin": "MODEL",
                    "principal_type": "assistant",
                    "principal_id": "agent-1",
                    "capabilities": ["process.run"],
                    "trace_id": "trace-1",
                })

                res_sup = reg_sup.execute_tool(
                    "run_command", {"command": command}, turn_id="turn_test",
                    conversation_id="conv_test", workspace_id="ws_test",
                    origin="MODEL", security_context=sec_ctx,
                )
                self.assertFalse(res_sup.success)
                self.assertTrue(res_sup.requires_approval)

                approval_id = "req_command"
                action_hash = reg_sup.policy.generate_action_hash("run_command", {"command": command})
                reg_sup.approval_manager.register_request(
                    "turn_test", "conv_test", "ws_test", action_hash, approval_id, "run_command"
                )
                grant = reg_sup.approval_manager.issue_grant(
                    "turn_test", "conv_test", "ws_test", action_hash, approval_id
                )
                approved = reg_sup.execute_tool(
                    "run_command", {"command": command}, turn_id="turn_test",
                    conversation_id="conv_test", workspace_id="ws_test",
                    origin="MODEL", grant=grant, expected_approval_id=approval_id,
                    security_context=sec_ctx,
                )
                self.assertTrue(approved.success, approved.error)

                runtime_approval_id = "req_runtime_command"
                reg_sup.approval_manager.register_request(
                    "runtime_turn", "conv_test", "ws_test", action_hash,
                    runtime_approval_id, "run_command",
                )
                runtime_grant = reg_sup.approval_manager.issue_grant(
                    "runtime_turn", "conv_test", "ws_test", action_hash, runtime_approval_id,
                )
                rt_sup = SafeRuntime(
                    workspace_root=tmpdir, workspace_id="ws_test",
                    conversation_id="conv_test", tool_registry=reg_sup,
                )
                approved_runtime = rt_sup.execute(
                    "process.run", {"command": command}, turn_id="runtime_turn",
                    origin="MODEL", security_context=sec_ctx,
                    approval_grant=runtime_grant,
                    expected_approval_id=runtime_approval_id,
                )
                self.assertTrue(approved_runtime.success, approved_runtime.error)

                reg_ro = ToolRegistry(root_dir=tmpdir)
                reg_ro.policy = PolicyEngine(
                    root_dir=tmpdir, autonomy=AutonomyPolicy.preset("read_only")
                )
                res_ro = reg_ro.execute_tool(
                    "run_command", {"command": command}, turn_id="turn_test",
                    conversation_id="conv_test", workspace_id="ws_test",
                    origin="MODEL", security_context=sec_ctx,
                )
                self.assertFalse(res_ro.success)
                self.assertFalse(res_ro.requires_approval)

                res_rt_sup = rt_sup.execute(
                    "process.run", {"command": command}, turn_id="turn_test",
                    origin="MODEL", security_context=sec_ctx,
                )
                self.assertFalse(res_rt_sup.success)
                self.assertTrue(res_rt_sup.requires_approval)
                self.assertEqual(res_rt_sup.approval_action, "run_command")

                rt_ro = SafeRuntime(
                    workspace_root=tmpdir, workspace_id="ws_test",
                    conversation_id="conv_test", tool_registry=reg_ro,
                )
                res_rt_ro = rt_ro.execute(
                    "process.run", {"command": command}, turn_id="turn_test",
                    origin="MODEL", security_context=sec_ctx,
                )
                self.assertFalse(res_rt_ro.success)
                self.assertFalse(res_rt_ro.requires_approval)
                self.assertIn("read_only", res_rt_ro.error)
            finally:
                if reg_ro is not None:
                    reg_ro.close()
                reg_sup.close()


if __name__ == "__main__":
    unittest.main()
