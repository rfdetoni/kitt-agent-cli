"""Remote JSON compression keeps wire and decoded limits independent."""
from compression import zstd
from email.message import Message
from io import BytesIO
import json
from unittest.mock import patch

import pytest

from kitt.runtime.conversation_runtime import RemoteRuntimeDriver, RuntimeOutcomeUnknown


def test_remote_zstd_roundtrip_and_bounded_response():
    driver = RemoteRuntimeDriver(lambda _: "x" * 48)
    config = {"endpoint": "http://127.0.0.1:7348", "transport_zstd": True}
    raw = json.dumps({"success": True, "stdout": "x" * 4096}).encode()
    packed = zstd.compress(raw, level=1)

    def response(data):
        stream = BytesIO(data)
        stream.headers = Message()
        stream.headers["Content-Encoding"] = "zstd"
        return stream

    with patch("kitt.runtime.conversation_runtime.secure_urlopen", return_value=response(packed)) as open_http:
        assert driver.execute("lab", ["echo", "x" * 4096], config)["stdout"] == "x" * 4096
        request = open_http.call_args.args[0]
        assert request.get_header("Content-encoding") == "zstd"
        assert request.get_header("Accept-encoding") == "zstd"
        assert json.loads(zstd.decompress(request.data))["argv"][0] == "echo"

    for invalid in (packed[:-1], packed + packed, zstd.compress(b"x" * (1024 * 1024 + 1))):
        with patch("kitt.runtime.conversation_runtime.secure_urlopen", return_value=response(invalid)) as open_http:
            with pytest.raises(RuntimeOutcomeUnknown):
                driver.execute("lab", ["true"], config)
            assert open_http.call_count == 1
