"""Scoped worker cache transport: bounded streamed bodies, adaptive Zstd, and digest verification."""
from __future__ import annotations

from compression import zstd
import hashlib
import json
import re
import tempfile
import urllib.error
import urllib.request

from kitt.llm.http_security import secure_urlopen
from kitt.runtime.conversation_runtime import _remote_endpoint, _runtime_id

MAX_BLOB = 64 * 1024 * 1024
ADAPTIVE_MIN_BYTES = 4096
ZSTD_WINDOW_LOG_MAX = 20
_INCOMPRESSIBLE_MAGIC = (
    b"\x1f\x8b",  # gzip
    b"PK\x03\x04",  # zip
    b"\x28\xb5\x2f\xfd",  # zstd
    b"\x89PNG",  # png
)


class _VerifiedZstdStream:
    """Read-only adapter decompressing a bounded Zstd HTTP stream with SHA-256 verification."""

    def __init__(self, response, *, expected_digest: str | None = None,
                 max_compressed: int = MAX_BLOB, max_decompressed: int = MAX_BLOB):
        self._response = response
        self._expected_digest = expected_digest
        self._max_compressed = max_compressed
        self._max_decompressed = max_decompressed
        self._compressed_seen = 0
        self._decompressed_seen = 0
        self._digest = hashlib.sha256()
        self._decoder = zstd.ZstdDecompressor(
            options={zstd.DecompressionParameter.window_log_max: ZSTD_WINDOW_LOG_MAX}
        )
        self._buffer = bytearray()
        self._eof = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def close(self):
        self._response.close()

    def _fill(self) -> None:
        while not self._buffer and not self._eof:
            chunk = self._response.read(64 * 1024)
            if not chunk:
                self._eof = True
                if not self._decoder.eof or self._decoder.unused_data:
                    raise ValueError("incomplete or trailing compressed blob frame")
                if self._expected_digest and self._digest.hexdigest() != self._expected_digest:
                    raise ValueError("remote compressed blob SHA-256 mismatch")
                return
            self._compressed_seen += len(chunk)
            if self._compressed_seen > self._max_compressed:
                raise ValueError("compressed blob exceeds size limit")
            if self._decoder.eof:
                raise ValueError("unexpected concatenated Zstd frame")
            decoded = self._decoder.decompress(
                chunk, max_length=self._max_decompressed - self._decompressed_seen + 1
            )
            self._decompressed_seen += len(decoded)
            if self._decompressed_seen > self._max_decompressed:
                raise ValueError("decompressed blob exceeds size limit")
            if decoded:
                self._digest.update(decoded)
                self._buffer.extend(decoded)

    def read(self, amt: int = -1) -> bytes:
        if amt is None or amt < 0:
            out = bytearray()
            while True:
                self._fill()
                if not self._buffer:
                    break
                out.extend(self._buffer)
                self._buffer.clear()
            return bytes(out)
        if amt == 0:
            return b""
        while len(self._buffer) < amt and not self._eof:
            self._fill()
        take = bytes(self._buffer[:amt])
        del self._buffer[:amt]
        return take


class RemoteArtifactCache:
    def __init__(self, config, runtime_id, token, *, max_blob_bytes: int = MAX_BLOB):
        endpoint, self.loopback = _remote_endpoint(str(config.get("endpoint") or ""))
        self.root = f"{endpoint}/v1/runtimes/{_runtime_id(runtime_id)}/blobs/"
        if not isinstance(token, str) or len(token) < 32:
            raise PermissionError("remote cache requires worker authentication")
        self.token = token
        self.max_blob = max(1024, min(int(max_blob_bytes), MAX_BLOB))
        self.timeout = max(1.0, min(float(config.get("timeout_seconds", 120)), 120.0))
        self.zstd_enabled = config.get("transport_zstd") is True

    def _request(self, digest, method, *, data=None, length=None,
                 content_encoding: str | None = None,
                 original_length: int | None = None):
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid blob SHA-256")
        accept = "zstd, identity" if self.zstd_enabled else "identity"
        headers = {"Authorization": "Bearer " + self.token, "Accept-Encoding": accept}
        if length is not None:
            if type(length) is not int or not 0 <= length <= self.max_blob:
                raise ValueError("blob exceeds worker size limit")
            headers.update({"Content-Length": str(length), "Content-Type": "application/octet-stream"})
            if content_encoding:
                headers["Content-Encoding"] = content_encoding
            if original_length is not None:
                headers["X-Kitt-Uncompressed-Size"] = str(original_length)
                headers["X-Kitt-Original-Sha256"] = digest
        return urllib.request.Request(self.root + digest, data=data, headers=headers, method=method)

    def _maybe_compress_stream(self, source, size: int) -> tuple[object, int, str | None, object | None]:
        """Adaptively compress stream to temp file if smaller and compressible."""
        if not self.zstd_enabled or size < ADAPTIVE_MIN_BYTES:
            return source, size, None, None
        head = source.read(min(4096, size))
        if any(head.startswith(sig) for sig in _INCOMPRESSIBLE_MAGIC):
            if hasattr(source, "seek"):
                source.seek(0)
                return source, size, None, None
        compressor = zstd.ZstdCompressor(level=1)
        temp = tempfile.TemporaryFile()
        try:
            compressed = compressor.compress(head)
            if compressed:
                temp.write(compressed)
            while chunk := source.read(64 * 1024):
                out = compressor.compress(chunk)
                if out:
                    temp.write(out)
                if temp.tell() >= size:
                    temp.close()
                    if hasattr(source, "seek"):
                        source.seek(0)
                    else:
                        raise ValueError("non-seekable source exceeded raw size during adaptive check")
                    return source, size, None, None
            tail = compressor.flush()
            if tail:
                temp.write(tail)
            packed_size = temp.tell()
            if packed_size >= size:
                temp.close()
                if hasattr(source, "seek"):
                    source.seek(0)
                return source, size, None, None
            temp.seek(0)
            return temp, packed_size, "zstd", temp
        except Exception:
            temp.close()
            raise

    def push(self, source, digest, size):
        if type(size) is not int or not 0 <= size <= self.max_blob:
            raise ValueError("blob exceeds worker size limit")
        try:
            with secure_urlopen(self._request(digest, "HEAD"), timeout=self.timeout,
                                max_body_bytes=self.max_blob, local_only=self.loopback) as response:
                if response.headers.get("Content-Length") != str(size):
                    raise ValueError("remote blob size mismatch")
                return False
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if code != 404:
                raise RuntimeError(f"remote cache HEAD failed: HTTP {code}") from exc
        body_stream, upload_len, encoding, owned_temp = self._maybe_compress_stream(source, size)
        try:
            req = self._request(
                digest, "PUT", data=body_stream, length=upload_len,
                content_encoding=encoding, original_length=size if encoding else None,
            )
            try:
                with secure_urlopen(req, timeout=self.timeout, max_body_bytes=4096,
                                    local_only=self.loopback) as response:
                    value = json.loads(response.read())
                    if not isinstance(value, dict) or value.get("sha256") != digest:
                        raise ValueError("remote cache returned wrong digest")
            except urllib.error.HTTPError as exc:
                code = exc.code
                exc.close()
                if code == 415 and encoding == "zstd" and hasattr(source, "seek"):
                    source.seek(0)
                    req_raw = self._request(digest, "PUT", data=source, length=size)
                    with secure_urlopen(req_raw, timeout=self.timeout, max_body_bytes=4096,
                                        local_only=self.loopback) as response:
                        value = json.loads(response.read())
                        if not isinstance(value, dict) or value.get("sha256") != digest:
                            raise ValueError("remote cache returned wrong digest") from exc
                    return True
                raise RuntimeError(f"remote cache PUT failed: HTTP {code}") from exc
        finally:
            if owned_temp is not None:
                owned_temp.close()
        return True

    def open_blob(self, digest):
        request = self._request(digest, "GET")
        try:
            response = secure_urlopen(request, timeout=self.timeout,
                                      max_body_bytes=self.max_blob, local_only=self.loopback)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise RuntimeError(f"remote cache GET failed: HTTP {code}") from exc
        encodings = [e.strip().lower() for e in (response.headers.get_all("Content-Encoding") or []) if e.strip()]
        if encodings == ["zstd"]:
            return _VerifiedZstdStream(
                response, expected_digest=digest,
                max_compressed=self.max_blob, max_decompressed=self.max_blob,
            )
        if not encodings or encodings == ["identity"]:
            return response
        response.close()
        raise ValueError("unsupported remote blob Content-Encoding")
