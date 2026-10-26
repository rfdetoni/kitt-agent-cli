"""Browser diagnosis is bounded, read-only and does not echo untrusted content."""
from types import SimpleNamespace

from kitt.runtime.core_runtime import SafeRuntime
from kitt.security.capabilities import CAP_BROWSER_READ
from kitt.runtime.core_runtime import OPERATION_REGISTRY


def test_browser_diagnosis_returns_live_bounded_events_not_raw_content():
    class Gateway:
        def execute(self, operation, arguments):
            assert operation == "diagnose"
            assert arguments == {}
            return {
                "action": "diagnose", "counters": {
                    "console": 1, "pageerror": 0, "requestfailed": 1, "http_error": 0
                },
                "recent": [{"kind": "console", "severity": "error",
                            "origin": "https://example.org", "fingerprint": "a" * 16,
                            "sensitive": "Bearer secret"}],
                "truncated": False,
            }
    runtime = object.__new__(SafeRuntime)
    runtime.conversation_id = "test"
    runtime.registry = SimpleNamespace(get_browser_gateway=lambda conversation: Gateway())
    result = runtime._op_browser_diagnose({})
    assert result.success
    assert result.data["counters"]["console"] == 1
    assert result.data["recent"][0]["fingerprint"] == "a" * 16
    assert "Bearer secret" not in str(result.data)
    assert OPERATION_REGISTRY["browser.diagnose"].required_capability == CAP_BROWSER_READ
    assert not runtime._op_browser_diagnose({"url": "https://example.com"}).success
