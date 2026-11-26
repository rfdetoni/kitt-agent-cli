"""Scoped worker cache transport: bounded streamed bodies and digest verification."""
from __future__ import annotations

import re
import urllib.error
import urllib.request

from kitt.llm.http_security import secure_urlopen
from kitt.runtime.conversation_runtime import _remote_endpoint, _runtime_id

MAX_BLOB = 8 * 1024 * 1024


class RemoteArtifactCache:
    def __init__(self, config, runtime_id, token):
        endpoint, self.loopback = _remote_endpoint(str(config.get("endpoint") or ""))
        self.root = f"{endpoint}/v1/runtimes/{_runtime_id(runtime_id)}/blobs/"
        if not isinstance(token, str) or len(token) < 32:
            raise PermissionError("remote cache requires worker authentication")
        self.token = token
        self.timeout = max(1.0, min(float(config.get("timeout_seconds", 120)), 120.0))

    def _request(self, digest, method, *, data=None, length=None):
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid blob SHA-256")
        headers = {"Authorization": "Bearer " + self.token, "Accept-Encoding": "identity"}
        if length is not None:
            if type(length) is not int or not 0 <= length <= MAX_BLOB:
                raise ValueError("blob exceeds worker size limit")
            headers.update({"Content-Length": str(length), "Content-Type": "application/octet-stream"})
        return urllib.request.Request(self.root + digest, data=data, headers=headers, method=method)

    def push(self, source, digest, size):
        if type(size) is not int or not 0 <= size <= MAX_BLOB:
            raise ValueError("blob exceeds worker size limit")
        try:
            with secure_urlopen(self._request(digest, "HEAD"), timeout=self.timeout,
                                max_body_bytes=MAX_BLOB, local_only=self.loopback) as response:
                if response.headers.get("Content-Length") != str(size):
                    raise ValueError("remote blob size mismatch")
                return False
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if code != 404:
                raise RuntimeError(f"remote cache HEAD failed: HTTP {code}") from exc
        try:
            with secure_urlopen(self._request(digest, "PUT", data=source, length=size),
                                timeout=self.timeout, max_body_bytes=4096,
                                local_only=self.loopback) as response:
                import json
                value = json.loads(response.read())
                if not isinstance(value, dict) or value.get("sha256") != digest:
                    raise ValueError("remote cache returned wrong digest")
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise RuntimeError(f"remote cache PUT failed: HTTP {code}") from exc
        return True

    def open_blob(self, digest):
        try:
            return secure_urlopen(self._request(digest, "GET"), timeout=self.timeout,
                                  max_body_bytes=MAX_BLOB, local_only=self.loopback)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise RuntimeError(f"remote cache GET failed: HTTP {code}") from exc
