import io

from kitt.tools.process_runner import _HeadTailCapture


def test_head_tail_capture_keeps_1k_prefix_and_8k_tail():
    payload = b"A" * 1024 + b"M" * (64 * 1024) + b"Z" * (8 * 1024)
    capture = _HeadTailCapture(256 * 1024)

    capture.consume(io.BytesIO(payload))
    rendered = capture.render()

    assert capture.truncated
    assert capture.total_bytes == len(payload)
    assert rendered.startswith(b"A" * 1024)
    assert rendered.endswith(b"Z" * 8000)
    assert b"KITT capture omitted" in rendered
    assert len(rendered) <= 1024 + (8 * 1024)


def test_head_tail_capture_respects_smaller_explicit_limit():
    payload = b"H" * 1024 + b"T" * 8192
    capture = _HeadTailCapture(4096)

    capture.consume(io.BytesIO(payload))
    rendered = capture.render()

    assert capture.truncated
    assert rendered.startswith(b"H" * 1024)
    assert rendered.endswith(b"T" * 3000)
    assert len(rendered) <= 4096
