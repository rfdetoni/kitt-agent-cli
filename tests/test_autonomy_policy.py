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
        self.assertEqual(engine.evaluate_command("rtk cat .env"), "DENY")

    def test_policy_deny_requires_approval_in_interactive_mode(self):
        import tempfile
        from pathlib import Path
        from kitt.tools.registry import ToolRegistry
        from kitt.security.context import ExecutionSecurityContext

        with tempfile.TemporaryDirectory() as tmpdir:
            reg_sup = ToolRegistry(root_dir=tmpdir)
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

            # In supervised mode, a model-invoked command that policy restricts (DENY)
            # must return requires_approval=True so UI shows the permission popup modal:
            res_sup = reg_sup.execute_tool(
                "run_command",
                {"command": "rtk find . -maxdepth 3 -type f | sort"},
                turn_id="turn_test",
                conversation_id="conv_test",
                workspace_id="ws_test",
                origin="MODEL",
                security_context=sec_ctx,
            )
            self.assertFalse(res_sup.success)
            self.assertTrue(res_sup.requires_approval)
            self.assertEqual(res_sup.metadata.get("approval_action"), "run_command")

            # In read_only mode, it remains strictly hard-denied without approval popup:
            reg_ro = ToolRegistry(root_dir=tmpdir)
            reg_ro.policy = PolicyEngine(root_dir=tmpdir, autonomy=AutonomyPolicy.preset("read_only"))
            res_ro = reg_ro.execute_tool(
                "run_command",
                {"command": "rtk find . -maxdepth 3 -type f | sort"},
                turn_id="turn_test",
                conversation_id="conv_test",
                workspace_id="ws_test",
                origin="MODEL",
                security_context=sec_ctx,
            )
            # Test SafeRuntime.execute for process.run
            from kitt.runtime.safe_runtime import SafeRuntime
            rt_sup = SafeRuntime(
                workspace_root=tmpdir,
                workspace_id="ws_test",
                conversation_id="conv_test",
                tool_registry=reg_sup,
            )
            res_rt_sup = rt_sup.execute(
                "process.run",
                {"command": "rtk find . -maxdepth 3 -type f | sort"},
                turn_id="turn_test",
                origin="MODEL",
                security_context=sec_ctx,
            )
            self.assertFalse(res_rt_sup.success)
            self.assertTrue(res_rt_sup.requires_approval)
            self.assertEqual(res_rt_sup.approval_action, "run_command")

            # SafeRuntime in read_only mode:
            rt_ro = SafeRuntime(
                workspace_root=tmpdir,
                workspace_id="ws_test",
                conversation_id="conv_test",
                tool_registry=reg_ro,
            )
            res_rt_ro = rt_ro.execute(
                "process.run",
                {"command": "rtk find . -maxdepth 3 -type f | sort"},
                turn_id="turn_test",
                origin="MODEL",
                security_context=sec_ctx,
            )
            self.assertFalse(res_rt_ro.success)
            self.assertFalse(res_rt_ro.requires_approval)
            self.assertIn("read_only", res_rt_ro.error)


if __name__ == "__main__":
    unittest.main()
