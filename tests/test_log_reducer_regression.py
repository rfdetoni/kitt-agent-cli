from kitt.tools.log_reducer import LogReducer


def test_log_reducer_preserves_first_compiler_error_and_final_exit_code():
    raw = "\n".join(
        ["header", *(f"noise {i}" for i in range(100)),
         "src/Main.java:19: error: cannot find symbol",
         "  symbol: class MissingType", "  location: class Main",
         *(f"another output {i}" for i in range(100)),
         "BUILD FAILURE", "Process exited with code 1"]
    )
    result = LogReducer().reduce_log(raw, max_lines=12)
    assert "src/Main.java:19: error" in result
    assert "symbol: class MissingType" in result
    assert "location: class Main" in result
    assert "Process exited with code 1" in result
    assert len(result.splitlines()) <= 12


def test_log_reducer_preserves_short_logs_and_enforces_small_budget():
    reducer = LogReducer()
    short = "pytest start\n1 passed in 0.20s"
    assert reducer.reduce_log(short) == short
    assert reducer.reduce_log("failed\nmore output", max_lines=0) == ""
    assert len(reducer.reduce_log("error: boom\nx\ny\nz", max_lines=2).splitlines()) <= 2
