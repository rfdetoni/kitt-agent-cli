"""Explicitly preauthorized, verified GitHub publication without guest credentials.

Publishes workspace file changes through GitHub's Git Data API using a trusted
runtime-side token; no git push, credential helper, hooks, or host shell runs.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from urllib import error, request


MAX_FILES = 5000
MAX_BYTES = 64 * 1024 * 1024
MAX_CHANGES = 100
EXCLUDED = {
    ".git", ".kitt", ".venv", "venv", "node_modules", "dist",
    "build", "target", ".gradle", ".next", "__pycache__", ".pytest_cache",
}
SOURCE_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
SHA_RE = re.compile(r"[0-9a-f]{40}")


def _git(root: Path, *args: str) -> bytes:
    binary = shutil.which("git")
    if not binary or Path(binary).resolve().is_relative_to(root):
        raise RuntimeError("trusted Git executable unavailable")
    environment = {
        "PATH": os.defpath,
        "HOME": str(root / ".kitt-nonexistent-home"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }
    process = subprocess.run(
        [binary, "-c", "core.fsmonitor=false", "-c",
         "core.hooksPath=/dev/null", *args],
        cwd=root, env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=15, check=False,
    )
    if process.returncode != 0:
        raise RuntimeError("Cannot inspect trusted local Git workspace")
    return process.stdout


def _safe_path(value: str) -> bool:
    path = Path(value)
    parts = path.parts
    return (
        bool(value) and not path.is_absolute()
        and ".." not in parts and not (set(parts) & EXCLUDED)
        and all(
            not part.startswith(".env")
            and not part.endswith((".pem", ".key", ".p12", ".pfx"))
            for part in parts
        )
    )


def inventory(root: Path) -> dict:
    root = root.resolve()
    # Sparse trees are not allowed: absent files could otherwise be deleted.
    if _git(root, "config", "--bool", "core.sparseCheckout").strip() == b"true":
        raise ValueError("Sparse checkouts cannot be automatically published")
    head = _git(root, "rev-parse", "--verify", "HEAD").decode().strip()
    if not SHA_RE.fullmatch(head):
        raise ValueError("Invalid local HEAD")
    tracked_raw = _git(root, "ls-files", "--cached", "-z").split(b"\x00")
    all_raw = _git(root, "ls-files", "--cached", "--others",
                   "--exclude-standard", "-z").split(b"\x00")
    try:
        tracked = {item.decode("utf-8") for item in tracked_raw if item}
        names = {item.decode("utf-8") for item in all_raw if item}
    except UnicodeDecodeError as exc:
        raise ValueError("Git path encoding unsupported for publication") from exc
    if len(names) > MAX_FILES:
        raise ValueError("Workspace publication file limit exceeded")
    files = {}
    digest = hashlib.sha256(head.encode())
    total = 0
    for rel in sorted(names):
        if not _safe_path(rel):
            continue
        candidate = root / rel
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(root):
            raise PermissionError(f"Unsafe symbolic link in publication: {rel}")
        if any(part.is_symlink() for part in candidate.parents if part != root and part.is_relative_to(root)):
            raise PermissionError(f"Unsafe parent symlink in publication: {rel}")
        if candidate.is_dir():
            continue
        if not candidate.exists():
            if rel in tracked:
                digest.update(f"DELETE:{rel}\n".encode())
            continue
        if not candidate.is_file():
            raise PermissionError(f"Unsafe special file in publication: {rel}")
        size = candidate.stat().st_size
        total += size
        if total > MAX_BYTES:
            raise ValueError("Workspace publication size limit exceeded")
        data = candidate.read_bytes()
        mode = "100755" if (candidate.stat().st_mode & stat.S_IXUSR) else "100644"
        blob_sha = hashlib.sha1(
            b"blob " + str(len(data)).encode() + b"\0" + data
        ).hexdigest()
        files[rel] = {"mode": mode, "sha": blob_sha, "data": data}
        digest.update(f"FILE:{rel}:{mode}:{blob_sha}\n".encode())
    return {
        "head": head, "files": files,
        "tracked": tracked, "digest": digest.hexdigest(),
    }


class GithubPublisher:
    """GitHub REST requests execute in the trusted runtime process only."""

    def __init__(self, repo: str, token: str):
        if not SOURCE_RE.fullmatch(repo):
            raise ValueError("Operator publication repo must be owner/name")
        if not token or len(token) > 4096:
            raise PermissionError("Operator GitHub publication token is absent")
        self.prefix = "https://api.github.com/repos/" + repo
        self.token = token

    def api(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = request.Request(
            self.prefix + path, data=data, method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "KITT-Autopilot-Publisher",
                "Content-Type": "application/json",
            },
        )
        try:
            with request.urlopen(req, timeout=20) as response:
                data = response.read(2 * 1024 * 1024 + 1)
        except error.HTTPError as exc:
            raise RuntimeError(
                f"GitHub publication rejected (HTTP {exc.code})"
            ) from None
        except error.URLError:
            raise RuntimeError("GitHub API unavailable; publication not confirmed") from None
        if len(data) > 2 * 1024 * 1024:
            raise RuntimeError("GitHub API response exceeds publication limit")
        result = json.loads(data)
        if not isinstance(result, dict):
            raise RuntimeError("Invalid GitHub publication API response")
        return result

    def publish(self, manifest: dict, branch: str, message: str) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9._/-]{1,100}", branch) or ".." in branch:
            raise ValueError("Invalid preauthorized branch")
        remote = self.api("GET", "/git/ref/heads/" + branch)
        remote_sha = str(remote.get("object", {}).get("sha", ""))
        if remote_sha != manifest["head"]:
            raise RuntimeError("Remote branch changed or local checkout diverged")
        base = self.api("GET", "/git/commits/" + remote_sha)
        tree_sha = str(base.get("tree", {}).get("sha", ""))
        if not SHA_RE.fullmatch(tree_sha):
            raise RuntimeError("Invalid remote Git tree")
        remote_tree = self.api("GET", "/git/trees/" + tree_sha + "?recursive=1")
        if remote_tree.get("truncated"):
            raise RuntimeError("Remote tree truncated; refuse partial publication")
        entries = {
            item["path"]: item for item in remote_tree.get("tree", [])
            if isinstance(item, dict) and item.get("type") == "blob"
        }
        changes = []
        for name, blob in manifest["files"].items():
            current = entries.get(name)
            if current is not None and (
                current.get("sha") == blob["sha"]
                and current.get("mode") == blob["mode"]
            ):
                continue
            changes.append((name, blob))
        for name in manifest["tracked"] - set(manifest["files"]):
            if _safe_path(name) and name in entries:
                changes.append((name, None))
        if len(changes) > MAX_CHANGES:
            raise ValueError("Too many modified files for atomic publication")
        if not changes:
            return {"published": False, "unchanged": True, "head": remote_sha}
        tree_entries = []
        for name, blob in sorted(changes):
            if blob is None:
                tree_entries.append({"path": name, "mode": "100644",
                                     "type": "blob", "sha": None})
            else:
                stored = self.api("POST", "/git/blobs", {
                    "content": base64.b64encode(blob["data"]).decode("ascii"),
                    "encoding": "base64",
                })
                tree_entries.append({
                    "path": name, "mode": blob["mode"], "type": "blob",
                    "sha": stored["sha"],
                })
        updated_tree = self.api("POST", "/git/trees", {
            "base_tree": tree_sha, "tree": tree_entries,
        })["sha"]
        commit = self.api("POST", "/git/commits", {
            "message": message, "tree": updated_tree, "parents": [remote_sha],
        })["sha"]
        # force=False is the non-fast-forward guard against another writer.
        self.api("PATCH", "/git/refs/heads/" + branch, {
            "sha": commit, "force": False,
        })
        return {"published": True, "commit": commit, "branch": branch,
                "changed_files": len(changes)}


def publish_verified(runtime, args: dict, *, allow_network: bool) -> dict:
    if not allow_network:
        raise PermissionError("Publication requires delegated network.access")
    if os.environ.get("KITT_AUTOPILOT_PUBLISH_ENABLED", "").lower() != "true":
        raise PermissionError("Autopilot remote publication is not operator enabled")
    if not isinstance(args, dict) or set(args) - {"message"}:
        raise ValueError("Unsupported publication arguments")
    message = args.get("message")
    if (not isinstance(message, str) or not 1 <= len(message.strip()) <= 160
            or any(char in message for char in ("\n", "\r", "\x00"))):
        raise ValueError("Publication requires a bounded single-line commit message")
    report = runtime.state.get("autopilot.task.verification") if runtime.state else None
    if not isinstance(report, dict) or report.get("ok") is not True:
        raise PermissionError("Successful autopilot.verify evidence is required")
    snapshot = report.get("publication_snapshot")
    if not isinstance(snapshot, dict):
        raise PermissionError("Verification lacks publication workspace fingerprint")
    now = inventory(runtime.root)
    if (now["digest"] != snapshot.get("digest")
            or now["head"] != snapshot.get("head")):
        raise PermissionError("Workspace changed since validation; rerun autopilot.verify")
    repo = os.environ.get("KITT_AUTOPILOT_PUBLISH_REPO", "").strip()
    branch = os.environ.get("KITT_AUTOPILOT_PUBLISH_BRANCH", "main").strip()
    token = os.environ.get("KITT_AUTOPILOT_GITHUB_TOKEN", "")
    result = GithubPublisher(repo, token).publish(now, branch, message.strip())
    if result.get("published"):
        runtime.state.delete("autopilot.task.verification")
    return result
