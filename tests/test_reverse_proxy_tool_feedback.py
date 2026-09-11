import io
import unittest
import urllib.error
from unittest.mock import patch

from kitt.llm.domain import ProviderProtocolError
from kitt.llm.providers.base import LLMRequest
from kitt.llm.providers.kitt_reverse_proxy import (
    KittReverseProxyAdapter,
    normalize_native_tool_messages,
)


class ReverseProxyToolFeedbackRegressionTests(unittest.TestCase):
    def test_preflight_rejection_consumes_original_tool_call_id(self):
        envelope = (
            '<kitt-tool>{"id":"call_patch123","name":"kitt_runtime",'
            '"arguments":{"operation":"patch.apply","arguments":'
            '{"patch":"*** /dev/null\\n--- script.py"}}}</kitt-tool>'
        )
        feedback = (
            "apply_patch was rejected before approval: arguments.patch needs a filename "
            "plus <<<<<<< SEARCH, =======, and >>>>>>> REPLACE. For a new file leave "
            "SEARCH empty. Retry with one complete envelope."
        )

        normalized = normalize_native_tool_messages([
            {"role": "assistant", "content": envelope},
            {"role": "user", "content": feedback},
        ])

        self.assertEqual([message["role"] for message in normalized], ["assistant", "tool"])
        self.assertEqual(
            normalized[0]["tool_calls"][0]["id"],
            "call_patch123",
        )
        self.assertEqual(normalized[1]["tool_call_id"], "call_patch123")
        self.assertEqual(normalized[1]["name"], "kitt_runtime")
        self.assertEqual(normalized[1]["content"], feedback)

    def test_invalid_tool_request_surfaces_proxy_error_detail(self):
        class FakeHTTPError(urllib.error.HTTPError):
            def __init__(self):
                super().__init__(
                    "http://127.0.0.1:3000/v1/chat/completions",
                    400,
                    "Bad Request",
                    {},
                    None,
                )
                self._fp = io.BytesIO(
                    b'{"error":{"message":"tool_call_id desconhecido para esta conversa: call_patch123",'
                    b'"code":"invalid_tool_request"}}'
                )

            def read(self, *args):
                return self._fp.read(*args)

        request = LLMRequest(
            model="gemini-web",
            messages=[{"role": "user", "content": "continue"}],
            base_url="http://127.0.0.1:3000",
        )

        with patch(
            "kitt.llm.providers.kitt_reverse_proxy.secure_urlopen",
            side_effect=FakeHTTPError(),
        ):
            with self.assertRaises(ProviderProtocolError) as caught:
                list(KittReverseProxyAdapter().stream(request))

        message = str(caught.exception)
        self.assertIn("invalid_tool_request", message)
        self.assertIn("tool_call_id desconhecido", message)


if __name__ == "__main__":
    unittest.main()
