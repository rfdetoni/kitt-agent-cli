import tempfile
import unittest
from pathlib import Path

from kitt.core.runtime import KittRuntime
from kitt.skills.executable import ExecutableSkillRunner, validate_skill_ast


class TestExecutableSkillsSandbox(unittest.TestCase):
    """Rigorous tests for isolated subprocess execution and security sandbox of executable skills."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        (self.root / "src").mkdir(parents=True, exist_ok=True)
        (self.root / "src" / "sample.txt").write_text("Hello from file in workspace", encoding="utf-8")

        self.skills_dir = self.root / ".kitt" / "skills"
        self.skills_dir.mkdir(parents=True, exist_ok=True)

        self.runtime = KittRuntime.build(str(self.root))
        self.runner = ExecutableSkillRunner(self.runtime, timeout_seconds=3.0)

    def tearDown(self):
        self.runtime.close()
        self.temp_dir.cleanup()

    def _create_skill(self, name: str, skill_py: str, capabilities=None):
        sdir = self.skills_dir / name
        sdir.mkdir(parents=True, exist_ok=True)
        caps = capabilities or []
        caps_str = "\n".join(f"  - {c}" for c in caps)
        (sdir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Test skill\ncapabilities:\n{caps_str}\n---\n",
            encoding="utf-8",
        )
        (sdir / "skill.py").write_text(skill_py, encoding="utf-8")

    def test_01_safe_skill_executes_in_subprocess(self):
        """Verify safe skill computes result and returns cleanly from worker subprocess."""
        code = """
def execute(ctx, args):
    a = args.get("a", 0)
    b = args.get("b", 0)
    return {"sum": a + b, "msg": "computed in sandbox"}
"""
        self._create_skill("math_adder", code)
        res = self.runner.execute("math_adder", {"a": 15, "b": 27})
        self.assertTrue(res.success, f"Failed with: {res.error}")
        self.assertEqual(res.data, {"sum": 42, "msg": "computed in sandbox"})
        self.assertGreater(res.duration_ms, 0)

    def test_02_rpc_capability_delegation_to_safe_runtime(self):
        """Verify skill RPC calls are delegated to host SafeRuntime and validated."""
        code = """
def execute(ctx, args):
    content = ctx.read_file("src/sample.txt")
    return {"content": content}
"""
        # Create with declared capability
        self._create_skill("file_reader", code, capabilities=["repo.read"])
        res = self.runner.execute("file_reader", {})
        self.assertTrue(res.success, f"Failed with: {res.error}")
        self.assertEqual(res.data.get("content"), "Hello from file in workspace")

    def test_03_ast_security_blocks_forbidden_constructs(self):
        """Verify forbidden modules and calls are statically blocked before subprocess spawn."""
        bad_codes = [
            "import os\ndef execute(ctx, args): return os.getcwd()",
            "import subprocess\ndef execute(ctx, args): return subprocess.run('ls')",
            "def execute(ctx, args): return eval('1 + 1')",
            "def execute(ctx, args): return open('secret.txt').read()",
            "def execute(ctx, args): return ().__class__.__bases__[0].__subclasses__()",
        ]

        for i, bad_code in enumerate(bad_codes):
            skill_name = f"malicious_{i}"
            self._create_skill(skill_name, bad_code)
            res = self.runner.execute(skill_name, {})
            self.assertFalse(res.success, f"Should have blocked: {bad_code}")
            self.assertIn("blocked", (res.error or "").lower())

    def test_04_timeout_enforcement_kills_hung_worker(self):
        """Verify infinite loops or hanging skills are killed after timeout."""
        code = """
def execute(ctx, args):
    x = 0
    while True:
        x += 1
    return x
"""
        short_runner = ExecutableSkillRunner(self.runtime, timeout_seconds=1.0)
        self._create_skill("infinite_loop", code)
        res = short_runner.execute("infinite_loop", {})
        self.assertFalse(res.success)
        self.assertIn("timed out", (res.error or "").lower())

    def test_05_undeclared_capability_rpc_is_denied(self):
        """Verify RPC call for capability not declared in SKILL.md is denied by SafeRuntime."""
        code = """
def execute(ctx, args):
    # Attempt process.run without process.run capability
    return ctx.run_process("ls")
"""
        # Skill has NO capabilities declared
        self._create_skill("unauthorized_runner", code, capabilities=[])
        res = self.runner.execute("unauthorized_runner", {})
        self.assertFalse(res.success)
        self.assertTrue(any(w in (res.error or "").lower() for w in ("not granted", "denied", "failed", "permission")))
