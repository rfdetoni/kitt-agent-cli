from hashlib import sha256
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
import json
import threading

import pytest

from kitt.artifacts.remote_cache import RemoteArtifactCache


def test_remote_cache_skips_existing_verified_blob_and_streams_missing_data():
    blobs = {}
    calls = []
    token = "x" * 32

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def handle_blob(self):
            assert self.headers["Authorization"] == "Bearer " + token
            digest = self.path.rsplit("/", 1)[1]
            calls.append(self.command)
            if self.command == "PUT":
                data = self.rfile.read(int(self.headers["Content-Length"]))
                assert sha256(data).hexdigest() == digest
                blobs[digest] = data
                body = json.dumps({"sha256": digest}).encode()
            elif digest not in blobs:
                self.send_error(404)
                return
            else:
                body = blobs[digest]
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        do_HEAD = do_PUT = do_GET = handle_blob

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cache = RemoteArtifactCache({"endpoint": f"http://127.0.0.1:{server.server_port}"}, "lab", token)
        data = b"payload" * 10000
        digest = sha256(data).hexdigest()
        assert cache.push(BytesIO(data), digest, len(data))
        assert not cache.push(BytesIO(data), digest, len(data))
        with cache.open_blob(digest) as stream:
            assert stream.read() == data
        assert calls == ["HEAD", "PUT", "HEAD", "GET"]
        with pytest.raises(ValueError):
            cache.open_blob("../private")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
