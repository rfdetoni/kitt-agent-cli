from kitt.native.bridge import NativeCodeEngine


def test_replace_block_uses_exact_unique_context(tmp_path, monkeypatch):
    target = tmp_path / "sample.py"
    target.write_text("a = 1\nb = 2\na = 1\n", encoding="utf-8")
    engine = NativeCodeEngine(str(tmp_path))
    monkeypatch.setattr(engine, "_native", None)

    try:
        engine.replace_block("sample.py", "a = 1", "a = 3")
    except ValueError as exc:
        assert "ambiguous" in str(exc)
    else:
        raise AssertionError("ambiguous block replacement must fail")

    result = engine.replace_block(
        "sample.py",
        "a = 1\nb = 2",
        "a = 3\nb = 4",
    )
    assert result["changed"] is True
    assert result["replacements"] == 1
    assert target.read_text(encoding="utf-8") == "a = 3\nb = 4\na = 1\n"
