"""Tests for P0-03, P1-05, P1-06 and runtime security hardening."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kitt.artifacts.remote_cache import RemoteArtifactCache
from kitt.artifacts.store import ArtifactStore
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository
from kitt.runtime.conversation_runtime import (
    ConversationRuntimeManager,
    RemoteRuntimeDriver,
)
from kitt.runtime.core_runtime import SafeRuntime
from kitt.runtime.rpc import RuntimeRPC
from kitt.scheduling.service import PersistentWakeScheduler
from kitt.vm_debian import MIRROR, IMAGE, _health, download_debian, prepare_debian
from kitt.vm_supervisor import _profile, inspect_profile
from kitt_protocol import RuntimeBackend


def test_vm_debian_health_accepts_python_314_and_newer(tmp_path):
    key = tmp_path / "id"
    known = tmp_path / "known"
    ok_payload = json.dumps({
        "version": 1,
        "status": "READY",
        "agent_version": "0.89.14",
        "agent_sha": "a" * 40,
        "python_minor": "3.15",
    }).encode()
    old_payload = json.dumps({
        "version": 1,
        "status": "READY",
        "agent_version": "0.89.14",
        "agent_sha": "a" * 40,
        "python_minor": "3.13",
    }).encode()
    with patch("subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=ok_payload)):
        assert _health(key, known, 2222, "ssh", "0.89.14", "a" * 40) is True
    with patch("subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=old_payload)):
        assert _health(key, known, 2222, "ssh", "0.89.14", "a" * 40) is False


def test_vm_debian_download_allows_mirror_and_disk_preflight(tmp_path):
    data = b"qcow2-test"
    expected = hashlib.sha512(data).hexdigest()

    class Resp:
        status = 200
        url = MIRROR + IMAGE

        def __init__(self):
            self._buf = BytesIO(data)

        def read(self, n=-1):
            return self._buf.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    dest = tmp_path / "debian.qcow2"
    with patch("kitt.vm_debian._DEBIAN_OPENER.open", return_value=Resp()):
        download_debian(dest, expected)
    assert dest.read_bytes() == data

    with patch("kitt.vm_debian.sys.platform", "linux"), \
         patch("kitt.vm_debian._program", return_value="/bin/mock"), \
         patch("shutil.disk_usage", return_value=SimpleNamespace(free=1024)):
        with pytest.raises(ValueError, match="Insufficient disk space"):
            prepare_debian(tmp_path / "vm2", agent_sha="a" * 40, image_sha512=expected)


def test_vm_supervisor_inspect_and_control_char_check(tmp_path):
    img = tmp_path / "base.qcow2"
    img.write_bytes(b"qcow2")
    img.chmod(0o400)
    key = tmp_path / "k"
    key.write_text("k")
    key.chmod(0o600)
    known = tmp_path / "kh"
    known.write_text("kh")
    known.chmod(0o600)
    prof = tmp_path / "profile.json"
    prof.write_text(json.dumps({
        "version": 1,
        "image": str(img),
        "sha256": hashlib.sha256(b"qcow2").hexdigest(),
        "ssh_key": str(key),
        "known_hosts": str(known),
        "ssh_user": "kitt",
        "host_key_alias": "kitt-sandbox",
        "memory_mib": 1024,
        "cpus": 1,
        "timeout_seconds": 60,
        "boot_seconds": 30,
        "gateway_port": 0,
    }))
    prof.chmod(0o600)
    with patch("kitt.vm_supervisor.sys.platform", "linux"), \
         patch("shutil.which", return_value="/bin/mock"), \
         patch("subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=b'{"format":"qcow2"}')):
        info = inspect_profile(prof)
    assert info["ready"] is True
    assert info["base_image_mode"] == "0o400"
    assert info["acceleration"] in {"kvm", "tcg"}

    bad_img = tmp_path / "bad\nname.qcow2"
    bad_img.write_bytes(b"qcow2")
    bad_prof = tmp_path / "bad.json"
    bad_prof.write_text(json.dumps({
        **json.loads(prof.read_text()),
        "image": str(bad_img),
        "sha256": hashlib.sha256(b"qcow2").hexdigest(),
    }))
    bad_prof.chmod(0o600)
    with pytest.raises(ValueError, match="Unsupported image path"):
        _profile(bad_prof)


def test_remote_artifact_cache_adaptive_zstd_and_incompressible_bypass():
    blobs = {}
    encodings_seen = []
    token = "z" * 40

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def handle_blob(self):
            digest = self.path.rsplit("/", 1)[1]
            if self.command == "PUT":
                enc = self.headers.get("Content-Encoding")
                encodings_seen.append(enc or "identity")
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                if enc == "zstd":
                    from compression import zstd
                    raw = zstd.ZstdDecompressor().decompress(raw, max_length=64 * 1024 * 1024)
                assert hashlib.sha256(raw).hexdigest() == digest
                blobs[digest] = raw
                body = json.dumps({"sha256": digest}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if digest not in blobs:
                self.send_error(404)
                return
            raw = blobs[digest]
            if self.command == "HEAD":
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                return
            from compression import zstd
            packed = zstd.compress(raw, level=1)
            self.send_response(200)
            self.send_header("Content-Encoding", "zstd")
            self.send_header("Content-Length", str(len(packed)))
            self.end_headers()
            self.wfile.write(packed)

        do_HEAD = do_PUT = do_GET = handle_blob

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cache = RemoteArtifactCache(
            {"endpoint": f"http://127.0.0.1:{server.server_port}", "transport_zstd": True},
            "lab", token,
        )
        compressible = b"kitt-snapshot-data-" * 1000
        d1 = hashlib.sha256(compressible).hexdigest()
        assert cache.push(BytesIO(compressible), d1, len(compressible))
        with cache.open_blob(d1) as s:
            assert s.read() == compressible

        incompressible = os.urandom(8192)
        d2 = hashlib.sha256(incompressible).hexdigest()
        assert cache.push(BytesIO(incompressible), d2, len(incompressible))
        assert encodings_seen == ["zstd", "identity"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_runtime_security_hardening_and_migration(tmp_path):
    db = HistoryDatabase(tmp_path)
    try:
        history = HistoryRepository(db)
        ws1 = history.get_or_create_workspace(str(tmp_path / "ws1"))
        ws2 = history.get_or_create_workspace(str(tmp_path / "ws2"))
        c1 = history.create_conversation(ws1["id"], "c1")
        c2 = history.create_conversation(ws2["id"], "c2")

        # 1. Reserved state keys & NUL check
        sr = SafeRuntime(str(tmp_path / "ws1"), ws1["id"], c1["id"])
        assert sr._is_operator_state_key("autopilot.task.verification")
        assert sr._is_operator_state_key("revision-ref:abc")
        assert sr._is_operator_state_key("workspace.review.pending")
        assert sr._is_operator_state_key("recipe.plan")

        with pytest.raises(PermissionError, match="sandbox_profile"):
            sr._op_process_start({"argv": ["echo"], "sandbox_profile": "full-access"}, "t1", None, "CLI")
        with pytest.raises(PermissionError, match="path-scoped"):
            sr._op_process_start({"argv": ["echo"]}, "t1", SimpleNamespace(is_path_scoped=True), "CLI")

        # 2. RemoteRuntimeDriver token_env & metadata SSRF protection
        with pytest.raises(PermissionError, match="KITT_REMOTE_RUNTIME_TOKEN"):
            RemoteRuntimeDriver().health("rt1", {"endpoint": "http://127.0.0.1:9999", "token_env": "OPENAI_API_KEY"})
        with pytest.raises(PermissionError, match="blocked"):
            RemoteRuntimeDriver().health("rt1", {"endpoint": "https://169.254.169.254"})

        # 3. Scheduler workspace isolation & RUNNING recovery
        fired = []
        sched = PersistentWakeScheduler(db, lambda cid, prompt, meta: fired.append((cid, prompt)), workspace_id=ws1["id"])
        sched.schedule(workspace_id=ws1["id"], conversation_id=c1["id"], prompt="ws1-task", run_at=0)
        sched.schedule(workspace_id=ws2["id"], conversation_id=c2["id"], prompt="ws2-task", run_at=0)
        assert sched.run_due_once() == 1
        assert [x[1] for x in fired] == ["ws1-task"]

        # 4. Migration transaction preserves source on target failure
        store = ArtifactStore(str(tmp_path / "artifacts"), db)
        mgr = ConversationRuntimeManager(tmp_path / "ws1", ws1["id"], c1["id"], db)
        fake_driver = RemoteRuntimeDriver(lambda _: "t" * 40)
        mgr.drivers[RuntimeBackend.REMOTE] = fake_driver

        with patch.object(fake_driver, "provision", return_value="src-rt"):
            mgr.provision(RuntimeBackend.REMOTE, {"endpoint": "http://127.0.0.1:1234", "image": "img@sha256:" + "a" * 64})
        with patch.object(fake_driver, "_request", return_value={"available": False}):
            with pytest.raises(RuntimeError, match="unavailable"):
                mgr.migrate_runtime({"endpoint": "http://127.0.0.1:2345", "runtime_id": "dst-rt"}, store, allow_sensitive=True)
        assert mgr.binding().runtime_id == "src-rt"
    finally:
        db.close()
