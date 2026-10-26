"""Browser diagnosis is bounded, read-only and does not echo untrusted content."""
from types import SimpleNamespace

from kitt.runtime.core_runtime import SafeRuntime
from kitt.security.capabilities import CAP_BROWSER_READ
from kitt.runtime.core_runtime import OPERATION_REGISTRY


def test_browser_diagnosis_reports_counts_without_echoing_page_content():
    class Gateway:
        def execute(self, operation, arguments):
            assert operation == "inspect"
            assert arguments == {}
            return {
                "console_errors": ["secret=super-secret"],
                "forms": ["password value private"],
                "attacker_secret": "private",
                "failed_requests": [{"authorization": "Bearer secret"}],
            }
    runtime = object.__new__(SafeRuntime)
    runtime.conversation_id = "test"
    runtime.registry = SimpleNamespace(get_browser_gateway=lambda conversation: Gateway())
    result = runtime._op_browser_diagnose({})
    assert result.success
    assert result.data["metrics"]["console_errors"] == 1
    assert result.data["has_failed_requests"] is True
    assert "super-secret" not in str(result.data)
    assert "private" not in str(result.data)
    assert OPERATION_REGISTRY["browser.diagnose"].required_capability == CAP_BROWSER_READ
    assert not runtime._op_browser_diagnose({"url": "https://example.com"}).success
