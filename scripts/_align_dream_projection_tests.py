from pathlib import Path

prune_path = Path("tests/dreaming/test_prune_and_index.py")
text = prune_path.read_text(encoding="utf-8")
old = '        mem_file = self.root / ".kitt" / "memory" / "MEMORY.md"\n'
if text.count(old) != 1:
    raise RuntimeError(f"prune projection path: expected 1 match, got {text.count(old)}")
new = '''        mem_file = (\n            self.root\n            / ".kitt"\n            / "workspaces"\n            / self.workspace_id\n            / "memory"\n            / "MEMORY.md"\n        )\n'''
prune_path.write_text(text.replace(old, new, 1), encoding="utf-8")

security_path = Path("tests/dreaming/test_transactions_and_security.py")
text = security_path.read_text(encoding="utf-8")
if text.count(old) != 2:
    raise RuntimeError(f"security projection path: expected 2 matches, got {text.count(old)}")
security_path.write_text(text.replace(old, new), encoding="utf-8")
