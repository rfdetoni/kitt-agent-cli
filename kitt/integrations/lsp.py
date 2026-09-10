from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kitt.tools.process_runner import sanitized_subprocess_env


@dataclass(frozen=True)
class LanguageServerSpec:
    name: str
    executables: tuple[str, ...]
    args: tuple[str, ...]
    extensions: tuple[str, ...]


@dataclass(frozen=True)
class LanguageServerStatus:
    name: str
    executable: str | None
    available: bool
    extensions: tuple[str, ...]


class LanguageServerCatalog:
    DEFAULTS = (
        LanguageServerSpec("pyright", ("pyright-langserver",), ("--stdio",), (".py", ".pyi")),
        LanguageServerSpec("typescript", ("typescript-language-server",), ("--stdio",), (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")),
        LanguageServerSpec("rust-analyzer", ("rust-analyzer",), (), (".rs",)),
        LanguageServerSpec("gopls", ("gopls",), ("serve",), (".go",)),
        LanguageServerSpec("jdtls", ("jdtls",), (), (".java",)),
    )

    def __init__(self, specs: tuple[LanguageServerSpec, ...] | None = None):
        self.specs = specs or self.DEFAULTS

    @staticmethod
    def _resolve(spec: LanguageServerSpec) -> str | None:
        for executable in spec.executables:
            resolved = shutil.which(executable)
            if resolved:
                return resolved
        return None

    def statuses(self) -> list[LanguageServerStatus]:
        result: list[LanguageServerStatus] = []
        for spec in self.specs:
            executable = self._resolve(spec)
            result.append(LanguageServerStatus(spec.name, executable, bool(executable), spec.extensions))
        return result

    def for_path(self, path: str | Path) -> tuple[LanguageServerSpec, str] | None:
        suffix = Path(path).suffix.casefold()
        for spec in self.specs:
            if suffix in spec.extensions:
                executable = self._resolve(spec)
                if executable:
                    return spec, executable
        return None


class LspProtocolError(RuntimeError):
    pass


class LanguageServerClient:
    """Bounded one-shot JSON-RPC/LSP client for explicit semantic queries."""

    MAX_MESSAGE_BYTES = 4 * 1024 * 1024

    def __init__(self, root_dir: str | Path, catalog: LanguageServerCatalog | None = None):
        self.root = Path(root_dir).resolve()
        self.catalog = catalog or LanguageServerCatalog()

    def _safe_path(self, raw: str | Path) -> Path:
        value = Path(raw)
        target = (self.root / value).resolve() if not value.is_absolute() else value.resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError(f"LSP path escapes workspace: {raw}") from exc
        if not target.is_file():
            raise FileNotFoundError(str(raw))
        return target

    @staticmethod
    def _position(line: int, column: int) -> dict[str, int]:
        return {"line": max(0, int(line) - 1), "character": max(0, int(column))}

    def request(
        self,
        path: str,
        method: str,
        *,
        line: int = 1,
        column: int = 0,
        params: dict[str, Any] | None = None,
        timeout_seconds: float = 8.0,
    ) -> dict[str, Any]:
        target = self._safe_path(path)
        resolved = self.catalog.for_path(target)
        if resolved is None:
            return {"backend": "lsp", "available": False, "method": method, "result": None}
        spec, executable = resolved
        proc = subprocess.Popen(
            [executable, *spec.args],
            cwd=self.root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env=sanitized_subprocess_env(),
            text=False,
            close_fds=True,
        )
        inbox: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        stop = threading.Event()

        def reader() -> None:
            try:
                while not stop.is_set() and proc.stdout is not None:
                    headers: dict[str, str] = {}
                    while True:
                        raw = proc.stdout.readline()
                        if not raw:
                            return
                        if raw in (b"\r\n", b"\n"):
                            break
                        text = raw.decode("ascii", "replace").strip()
                        if ":" in text:
                            key, value = text.split(":", 1)
                            headers[key.strip().lower()] = value.strip()
                    length = int(headers.get("content-length", "0") or "0")
                    if length <= 0 or length > self.MAX_MESSAGE_BYTES:
                        raise LspProtocolError(f"Invalid LSP content length: {length}")
                    payload = proc.stdout.read(length)
                    if len(payload) != length:
                        raise LspProtocolError("Truncated LSP message")
                    decoded = json.loads(payload.decode("utf-8"))
                    if isinstance(decoded, dict):
                        inbox.put(decoded)
            except BaseException as exc:
                inbox.put(exc)

        thread = threading.Thread(target=reader, name=f"kitt-lsp-{spec.name}", daemon=True)
        thread.start()
        next_id = 0

        def send(method_name: str, payload: dict[str, Any] | None = None, *, notification=False):
            nonlocal next_id
            message: dict[str, Any] = {"jsonrpc": "2.0", "method": method_name, "params": payload or {}}
            request_id = None
            if not notification:
                next_id += 1
                request_id = next_id
                message["id"] = request_id
            raw = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            if proc.stdin is None:
                raise LspProtocolError("Language server stdin unavailable")
            proc.stdin.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii") + raw)
            proc.stdin.flush()
            return request_id

        def await_id(request_id: int, deadline: float):
            while time.monotonic() < deadline:
                try:
                    message = inbox.get(timeout=min(0.1, max(0.01, deadline - time.monotonic())))
                except queue.Empty:
                    continue
                if isinstance(message, BaseException):
                    raise LspProtocolError(str(message)) from message
                if message.get("id") == request_id:
                    if "error" in message:
                        raise LspProtocolError(str(message["error"])[:2000])
                    return message.get("result")
            raise TimeoutError(f"LSP request timed out: {method}")

        deadline = time.monotonic() + max(1.0, min(float(timeout_seconds), 30.0))
        try:
            init_id = send(
                "initialize",
                {
                    "processId": None,
                    "rootUri": self.root.as_uri(),
                    "capabilities": {
                        "textDocument": {
                            "definition": {}, "hover": {}, "references": {},
                            "documentSymbol": {}, "diagnostic": {}, "callHierarchy": {},
                        }
                    },
                    "workspaceFolders": [{"uri": self.root.as_uri(), "name": self.root.name}],
                },
            )
            await_id(init_id, deadline)
            send("initialized", {}, notification=True)
            source = target.read_text(encoding="utf-8", errors="replace")
            language_id = {
                ".py": "python", ".pyi": "python", ".js": "javascript", ".jsx": "javascriptreact",
                ".ts": "typescript", ".tsx": "typescriptreact", ".rs": "rust", ".go": "go", ".java": "java",
            }.get(target.suffix.casefold(), target.suffix.lstrip("."))
            uri = target.as_uri()
            send(
                "textDocument/didOpen",
                {"textDocument": {"uri": uri, "languageId": language_id, "version": 1, "text": source}},
                notification=True,
            )
            request_params = params or {"textDocument": {"uri": uri}, "position": self._position(line, column)}
            request_id = send(method, request_params)
            result = await_id(request_id, deadline)
            return {"backend": f"lsp:{spec.name}", "available": True, "method": method, "result": result}
        finally:
            stop.set()
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
