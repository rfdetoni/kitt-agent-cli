from pathlib import Path

from kitt.native.bridge import NativeCodeEngine


def test_search_symbol_edit_and_references(tmp_path: Path):
    source = tmp_path / "service.py"
    source.write_text(
        "class Service:\n"
        "    def authenticate(self, token):\n"
        "        return validate(token)\n\n"
        "def validate(token):\n"
        "    return bool(token)\n",
        encoding="utf-8",
    )
    engine = NativeCodeEngine(str(tmp_path))
    result = engine.search("authenticate", token_budget=300)
    assert result["hits"]
    symbols = engine.find_symbols("authenticate")
    assert symbols
    symbol_id = symbols[0]["id"]
    read = engine.read_symbol(symbol_id)
    assert "authenticate" in read["source"]
    refs = engine.references("validate")
    assert any(r["line"] == 3 for r in refs)
    edit = engine.replace_symbol(
        symbol_id,
        "    def authenticate(self, token):\n        return token == 'ok'\n",
        expected_hash=read["symbol"]["source_hash"],
    )
    assert edit["changed"]
    assert "token == 'ok'" in source.read_text(encoding="utf-8")


def test_edit_rejects_stale_hash(tmp_path: Path):
    path = tmp_path / "a.py"
    path.write_text("def f():\n    return 1\n", encoding="utf-8")
    engine = NativeCodeEngine(str(tmp_path))
    sym = engine.find_symbols("f")[0]
    try:
        engine.replace_symbol(sym["id"], "def f():\n    return 2\n", expected_hash="stale")
    except RuntimeError as exc:
        assert "conflict" in str(exc).lower()
    else:
        raise AssertionError("stale edit hash must fail")


def test_output_compression_never_expands(tmp_path: Path):
    engine = NativeCodeEngine(str(tmp_path))
    raw = "\n".join(f"[INFO] routine build line {i}" for i in range(500))
    result = engine.compress_output(["mvn", "test"], raw, "", 0)
    assert result["output_bytes"] <= result["raw_bytes"]
    if result["changed"]:
        assert result["omitted_lines"] > 0
