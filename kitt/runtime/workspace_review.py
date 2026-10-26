"""Hash-guarded review/apply/rewind over the existing workspace filesystem.

This is not an alternate file mutation authority. SafeRuntime requires repo.write,
runs the configured write policy (including approvals) before apply/rewind, and
the underlying WorkspaceFileSystem performs atomic optimistic writes.
"""
from __future__ import annotations

import difflib
import hashlib
import secrets
import copy
from pathlib import Path

from kitt.security.workspace_fs import WorkspaceFileSystem


_MAX_TOTAL = 16 * 1024
_MAX_FILES = 8


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class WorkspaceReview:
    def __init__(self, runtime):
        self.runtime = runtime
        self.fs = WorkspaceFileSystem(Path(runtime.root))
        self.state = runtime.state

    def _store(self):
        if self.state is None:
            raise RuntimeError("Workspace review requires persistent runtime state")
        return self.state

    def _path(self, path: str, security_context) -> str:
        if not isinstance(path, str):
            raise ValueError("review path must be text")
        rel = self.fs.relative(path)
        if (
            rel == "." or rel.startswith(".git/") or rel == ".git"
            or rel.startswith(".kitt/") or rel == ".kitt"
        ):
            raise PermissionError("review forbids Git metadata and KITT control-plane files")
        if security_context is not None:
            security_context.assert_path_allowed(rel)
        return rel

    @staticmethod
    def _owner(context):
        if context is None:
            return None
        if not hasattr(context, "principal_id"):
            return None
        return [context.principal_type, context.principal_id,
                context.workspace_id, context.conversation_id]

    def _load(self, identifier: str, security_context):
        if not isinstance(identifier, str) or not identifier.startswith("review_") or len(identifier) != 39:
            raise ValueError("invalid review id")
        record = self._store().get(identifier)
        if not isinstance(record, dict) or not isinstance(record.get("files"), list):
            raise ValueError("review not found in current conversation")
        if record.get("owner") != self._owner(security_context):
            raise PermissionError("review belongs to another execution principal")
        for file in record["files"]:
            self._path(file["path"], security_context)
        return copy.deepcopy(record)

    def preview(self, args: dict, security_context):
        if not isinstance(args, dict) or set(args) != {"files"}:
            raise ValueError("review.preview requires files")
        files = args["files"]
        if not isinstance(files, list) or not 1 <= len(files) <= _MAX_FILES:
            raise ValueError("review must contain 1..8 files")
        record = []
        size = 0
        seen = set()
        for item in files:
            if not isinstance(item, dict) or set(item) != {"path", "content"}:
                raise ValueError("review file requires path and UTF-8 content")
            rel = self._path(item["path"], security_context)
            content = item["content"]
            if rel in seen or not isinstance(content, str):
                raise ValueError("duplicate path or invalid content")
            seen.add(rel)
            proposed = content.encode("utf-8")
            if self.fs.is_safe_directory(rel):
                raise PermissionError("review target must not be a directory")
            if self.fs.exists_regular(rel):
                previous = self.fs.read(rel, max_bytes=_MAX_TOTAL).content
                try:
                    original = previous.decode("utf-8", "strict")
                except UnicodeError as exc:
                    raise ValueError("review supports UTF-8 files only") from exc
                existed = True
            else:
                original, previous, existed = "", b"", False
            size += len(previous) + len(proposed)
            if size > _MAX_TOTAL:
                raise ValueError("review total original + proposed content exceeds 16 KiB")
            record.append({
                "path": rel, "before": original, "after": content,
                "before_sha256": _sha(previous) if existed else None,
                "after_sha256": _sha(proposed), "existed": existed,
            })
        identifier = "review_" + secrets.token_hex(16)
        self._store().set(identifier, {"status": "PREVIEW", "files": record, "owner": self._owner(security_context)}, ttl_seconds=86400)
        return self.diff({"review_id": identifier}, security_context)

    def diff(self, args: dict, security_context):
        if not isinstance(args, dict) or set(args) != {"review_id"}:
            raise ValueError("review.diff requires review_id")
        record = self._load(args["review_id"], security_context)
        changes = []
        for item in record["files"]:
            lines = list(difflib.unified_diff(
                item["before"].splitlines(keepends=True),
                item["after"].splitlines(keepends=True),
                fromfile="a/" + item["path"], tofile="b/" + item["path"], n=3,
            ))
            diff = "".join(lines)
            changes.append({
                "path": item["path"], "before_sha256": item["before_sha256"],
                "after_sha256": item["after_sha256"], "diff": diff[:_MAX_TOTAL],
            })
        return {"review_id": args["review_id"], "status": record["status"], "changes": changes}

    def _validate(self, record, expect_after: bool):
        for item in record["files"]:
            rel = item["path"]
            present = self.fs.exists_regular(rel)
            expected = item["after_sha256"] if expect_after else item["before_sha256"]
            if present != (expected is not None):
                raise ValueError(f"review conflict: existence changed for {rel}")
            if present and self.fs.read(rel, max_bytes=_MAX_TOTAL).sha256 != expected:
                raise ValueError(f"review conflict: content changed for {rel}")

    def _write(self, item, reverse: bool):
        rel = item["path"]
        expected = item["after_sha256"] if reverse else item["before_sha256"]
        original_existed = bool(item["existed"])
        if reverse and not original_existed:
            self.fs.unlink(rel, expected_sha256=expected)
        else:
            text = item["before"] if reverse else item["after"]
            self.fs.atomic_write(rel, text, expected_sha256=expected, expected_exists=expected is not None)

    def authorization_manifest(self, args, security_context):
        if not isinstance(args, dict) or set(args) != {"review_id"}:
            raise ValueError("invalid review authorization request")
        record = self._load(args["review_id"], security_context)
        return {
            "status": record["status"], "review_id": args["review_id"],
            "files": [
                {"path": f["path"], "before_sha256": f["before_sha256"],
                 "after_sha256": f["after_sha256"]}
                for f in record["files"]
            ],
        }

    def _transition(self, args, security_context, *, reverse: bool):
        if not isinstance(args, dict) or set(args) != {"review_id"}:
            raise ValueError("review transition requires review_id")
        record = self._load(args["review_id"], security_context)
        expected_status = "APPLIED" if reverse else "PREVIEW"
        if record["status"] != expected_status:
            raise ValueError(f"review requires status {expected_status}")
        self._validate(record, expect_after=reverse)
        done = []
        try:
            for item in (list(reversed(record["files"])) if reverse else record["files"]):
                self._write(item, reverse)
                done.append(item)
        except Exception:
            # Best effort rollback of partial transitions, guarded by the hashes
            # we just wrote. A conflicting concurrent edit must never be overwritten.
            for item in reversed(done):
                try:
                    self._write(item, not reverse)
                except Exception:
                    pass
            raise
        # Keep the durable state transition in the same compensation boundary.
        # If SQLite persistence fails, roll back the writes using CAS guards.
        record["status"] = "REWOUND" if reverse else "APPLIED"
        try:
            self._store().set(args["review_id"], record, ttl_seconds=86400)
        except Exception:
            for item in reversed(done):
                try:
                    self._write(item, not reverse)
                except Exception:
                    pass
            raise
        return {
            "review_id": args["review_id"], "status": record["status"],
            "changed_paths": [item["path"] for item in record["files"]],
        }

    def apply(self, args: dict, security_context):
        return self._transition(args, security_context, reverse=False)

    def rewind(self, args: dict, security_context):
        return self._transition(args, security_context, reverse=True)
