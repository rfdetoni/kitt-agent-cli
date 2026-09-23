from __future__ import annotations

import tempfile
from pathlib import Path

from kitt.settings.runtime_flags import (
    VERIFY_FULL_FLAG,
    get_runtime_flag,
    load_runtime_flags,
    set_runtime_flag,
)


def test_persisted_flag_is_authoritative_over_environment(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "flags.json"
        monkeypatch.setenv(VERIFY_FULL_FLAG, "1")
        assert get_runtime_flag(VERIFY_FULL_FLAG, path=path) is True
        set_runtime_flag(VERIFY_FULL_FLAG, False, path=path)
        assert get_runtime_flag(VERIFY_FULL_FLAG, path=path) is False
        assert load_runtime_flags(path)["flags"][VERIFY_FULL_FLAG] is False
