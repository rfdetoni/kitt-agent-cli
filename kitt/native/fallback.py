from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import stat
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

_TEXT_EXTENSIONS = {
    ".py", ".java", ".kt", ".kts", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift",
    ".toml", ".yaml", ".yml", ".json", ".xml", ".md", ".txt", ".properties",
}
_IGNORED_DIRS = {".git", ".kitt", "node_modules", "target", "build", "dist", ".venv", "venv", "__pycache__"}


def _sha(text: str | bytes) -> str:
    data = text if isinstance(text, bytes) else text.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _estimate_tokens(text: str) -> int:
    return (len(text) + 3) // 4


def _iter_files(root: Path) -> Iterable[Path]:
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS and not d.startswith(".git")]
        for name in files:
            path = Path(base) / name
            if path.suffix.lower() not in _TEXT_EXTENSIONS:
                continue
            try:
                if path.stat().st_size > 4 * 1024 * 1024:
                    continue
            except OSError:
                continue
            yield path


def _compact_line(line: str, match_start: int, match_len: int) -> str:
    if len(line) <= 360:
        return line
    left = max(0, match_start - 140)
    right = min(len(line), match_start + max(1, match_len) + 140)
    return ("…" if left else "") + line[left:right] + ("…" if right < len(line) else "")


def search(root: Path, query: str, *, regex: bool = False, case_sensitive: bool = False,
           max_results: int = 50, max_per_file: int = 8, context_lines: int = 1,
           token_budget: int = 1200) -> dict[str, Any]:
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(query if regex else re.escape(query), flags)
    hits: list[dict[str, Any]] = []
    per_file: dict[str, int] = defaultdict(int)
    total = 0
    used_tokens = 0
    matched_files: set[str] = set()
    for path in _iter_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        lines = text.splitlines()
        for index, line in enumerate(lines):
            match = pattern.search(line)
            if not match:
                continue
            total += 1
            if per_file[rel] >= max_per_file or len(hits) >= max_results:
                continue
            start = max(0, index - context_lines)
            end = min(len(lines), index + context_lines + 1)
            rendered = _compact_line(line, match.start(), match.end() - match.start())
            before, after = lines[start:index], lines[index + 1:end]
            cost = _estimate_tokens(rendered + "\n".join(before + after)) + 12
            if hits and used_tokens + cost > token_budget:
                continue
            used_tokens += cost
            per_file[rel] += 1
            matched_files.add(rel)
            hits.append({
                "path": rel, "line": index + 1, "column": match.start() + 1,
                "text": rendered, "before": before, "after": after,
                "score": 1.0 / (1.0 + index / 10000.0),
            })
    return {
        "hits": hits, "matched_files": len(matched_files), "total_matches_seen": total,
        "omitted_matches": max(0, total - len(hits)), "estimated_tokens": used_tokens,
    }


def _python_symbols(rel: str, text: str) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line.encode("utf-8")))
    out: list[dict[str, Any]] = []
    stack: list[str] = []

    class Visitor(ast.NodeVisitor):
        def _record(self, node: ast.AST, name: str, kind: str) -> None:
            start_line = int(getattr(node, "lineno", 1))
            end_line = int(getattr(node, "end_lineno", start_line))
            start_byte = offsets[start_line - 1]
            end_byte = offsets[min(end_line, len(lines))]
            raw = text.encode("utf-8")[start_byte:end_byte]
            qname = "::".join([*stack, name]) if stack else name
            out.append({
                "id": f"{rel}::{qname}", "path": rel, "name": name,
                "qualified_name": qname, "kind": kind, "start_line": start_line,
                "end_line": end_line, "start_byte": start_byte, "end_byte": end_byte,
                "source_hash": _sha(raw),
            })

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._record(node, node.name, "class")
            stack.append(node.name); self.generic_visit(node); stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._record(node, node.name, "function")
            stack.append(node.name); self.generic_visit(node); stack.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.visit_FunctionDef(node)  # type: ignore[arg-type]

    Visitor().visit(tree)
    return out


_GENERIC_DEFS = re.compile(
    r"^\s*(?:(?:public|private|protected|static|final|abstract|async|export|pub|internal|open)\s+)*"
    r"(?:(class|interface|enum|record|struct|trait|fn|func|function|def|type)\s+)?"
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*(?:\([^;{}]*\))?\s*(?:\{|:)", re.MULTILINE
)


def _generic_symbols(rel: str, text: str) -> list[dict[str, Any]]:
    out = []
    encoded = text.encode("utf-8")
    for match in _GENERIC_DEFS.finditer(text):
        name = match.group(2)
        if name in {"if", "for", "while", "switch", "catch", "else", "try", "do", "new", "return"}:
            continue
        start_line = text.count("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        if line_end < 0:
            line_end = len(text)
        # Fallback deliberately uses a single-line range; native Tree-sitter is authoritative.
        start_byte = len(text[:match.start()].encode("utf-8"))
        end_byte = len(text[:line_end].encode("utf-8"))
        kind = match.group(1) or "callable"
        out.append({
            "id": f"{rel}::{name}", "path": rel, "name": name,
            "qualified_name": name, "kind": kind, "start_line": start_line,
            "end_line": start_line, "start_byte": start_byte, "end_byte": end_byte,
            "source_hash": _sha(encoded[start_byte:end_byte]),
        })
    return out


def symbols_in_file(root: Path, rel: str) -> list[dict[str, Any]]:
    path = (root / rel).resolve()
    if root not in path.parents and path != root:
        raise PermissionError("path escapes repository root")
    text = path.read_text(encoding="utf-8", errors="replace")
    return _python_symbols(rel, text) if path.suffix.lower() == ".py" else _generic_symbols(rel, text)


def find_symbols(root: Path, query: str, limit: int = 50) -> list[dict[str, Any]]:
    q = query.casefold()
    out = []
    for path in _iter_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            syms = symbols_in_file(root, rel)
        except (OSError, SyntaxError):
            continue
        out.extend(s for s in syms if q in s["name"].casefold() or q in s["qualified_name"].casefold() or q in s["id"].casefold())
        if len(out) >= limit * 4:
            break
    out.sort(key=lambda s: (not (s["name"].casefold() == q or s["qualified_name"].casefold() == q), len(s["qualified_name"]), s["path"]))
    return out[:max(1, min(limit, 500))]


def read_symbol(root: Path, symbol_id: str) -> dict[str, Any] | None:
    rel = symbol_id.split("::", 1)[0]
    path = root / rel
    if not path.is_file():
        return None
    raw = path.read_bytes()
    for sym in symbols_in_file(root, rel):
        if sym["id"] == symbol_id:
            source = raw[sym["start_byte"]:sym["end_byte"]].decode("utf-8", "replace")
            return {"symbol": sym, "source": source}
    return None


def references(root: Path, symbol_id: str, limit: int = 100) -> list[dict[str, Any]]:
    target = symbol_id.rsplit("::", 1)[-1]
    pattern = re.compile(rf"\b{re.escape(target)}\b")
    out = []
    for path in _iter_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        syms = symbols_in_file(root, rel)
        for index, line in enumerate(text.splitlines(), 1):
            if not pattern.search(line):
                continue
            if any(s["name"] == target and s["start_line"] == index for s in syms):
                continue
            containers = [s for s in syms if s["start_line"] <= index <= s["end_line"]]
            containers.sort(key=lambda s: s["end_line"] - s["start_line"])
            out.append({
                "path": rel, "line": index,
                "containing_symbol": containers[0]["id"] if containers else None,
                "target_name": target, "kind": "fallback_reference",
            })
            if len(out) >= limit:
                return out
    return out


def replace_symbol(root: Path, symbol_id: str, replacement: str, expected_hash: str | None = None,
                   validate_syntax: bool = True) -> dict[str, Any]:
    current = read_symbol(root, symbol_id)
    if not current:
        raise KeyError(f"symbol not found: {symbol_id}")
    sym = current["symbol"]
    path = root / sym["path"]
    # The compatibility parser is structural only for Python.  For other
    # languages its ranges are intentionally approximate, so editing through
    # them could truncate a declaration/body. Fail closed and let apply_patch
    # handle those platforms when the Rust/Tree-sitter engine is unavailable.
    if path.suffix.lower() != ".py":
        raise RuntimeError(
            "structural editing for this language requires the KITT native engine; use apply_patch fallback"
        )
    if expected_hash and expected_hash != sym["source_hash"]:
        raise RuntimeError("optimistic edit conflict: symbol hash changed")
    raw = path.read_bytes()
    replacement_bytes = replacement.encode("utf-8")
    updated = raw[:sym["start_byte"]] + replacement_bytes + raw[sym["end_byte"]:]
    if raw == updated:
        return {
            "path": sym["path"], "old_hash": sym["source_hash"],
            "new_hash": sym["source_hash"], "changed": False,
        }
    if validate_syntax:
        ast.parse(updated.decode("utf-8", "strict"))
    original_mode = stat.S_IMODE(path.stat().st_mode)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            tmp.write(updated)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        os.chmod(tmp_path, original_mode)
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
    return {
        "path": sym["path"], "old_hash": sym["source_hash"],
        "new_hash": _sha(replacement_bytes), "changed": True,
    }


def dependency_edges(root: Path, max_symbols: int = 10000) -> dict[str, list[str]]:
    all_symbols = find_symbols(root, "", max_symbols)
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sym in all_symbols:
        by_name[sym["name"]].append(sym)
    callish = re.compile(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*(?:\(|\.)")
    result: dict[str, list[str]] = {}
    for sym in all_symbols:
        current = read_symbol(root, sym["id"])
        if not current:
            continue
        deps: list[str] = []
        for name in set(callish.findall(current["source"])):
            if name == sym["name"]:
                continue
            candidates = by_name.get(name, [])
            if len(candidates) == 1:
                deps.append(candidates[0]["id"])
        if deps:
            result[sym["id"]] = sorted(set(deps))
    return result


def compress_output(argv: list[str], stdout: str, stderr: str, returncode: int) -> dict[str, Any]:
    raw = stdout if not stderr else stderr if not stdout else stdout + "\n" + stderr
    cmd = Path(argv[0]).name.casefold() if argv else ""
    for suffix in (".exe", ".cmd", ".bat"):
        if cmd.endswith(suffix):
            cmd = cmd[:-len(suffix)]
            break
    if cmd in {"python", "python3", "py"} and len(argv) >= 3 and argv[1] == "-m":
        cmd = str(argv[2]).casefold()
    family = (
        "search" if cmd in {"grep", "rg", "ripgrep"} else
        "vcs" if cmd in {"git", "gh", "glab"} else
        "build_test" if cmd in {"mvn", "mvnw", "gradle", "gradlew", "cargo", "go", "pytest", "npm", "pnpm", "yarn", "bun", "npx", "jest", "vitest", "playwright", "rspec", "phpunit", "composer", "dotnet", "make", "cmake", "ninja", "sbt"} else
        "diagnostics" if cmd in {"ruff", "mypy", "eslint", "biome", "prettier", "tsc", "shellcheck", "hadolint", "golangci-lint", "checkstyle", "spotbugs", "pmd"} else
        "infra" if cmd in {"docker", "podman", "kubectl", "oc", "terraform", "terragrunt", "pulumi", "helm", "aws", "gcloud", "az"} else
        "listing" if cmd in {"ls", "find", "tree", "wc", "cat", "head", "tail"} else "generic"
    )
    lines = raw.splitlines()
    candidate = raw
    omitted = 0
    if family == "search":
        groups: dict[str, list[str]] = defaultdict(list)
        search_re = re.compile(r"^(.*?):(\d+)(?::\d+)?:?(.*)$")
        for line in lines:
            m = search_re.match(line)
            if not m:
                continue
            if len(groups[m.group(1)]) < 8:
                groups[m.group(1)].append(f"{m.group(2)}:{m.group(3).strip()}")
            else:
                omitted += 1
        if groups:
            candidate = "\n".join(f"{path}\n" + "\n".join("  " + x for x in rows) for path, rows in sorted(groups.items())[:40])
    elif family in {"build_test", "diagnostics", "infra"} and len(lines) > 30:
        markers = ("error", "failed", "failure", "exception", "assert", "tests run", "test result", "build success", "build failure", "warning", "caused by", "traceback")
        keep: set[int] = set()
        for i, line in enumerate(lines):
            if any(marker in line.casefold() for marker in markers):
                keep.update(range(max(0, i - 1), min(len(lines), i + 2)))
        if not keep:
            keep.update(range(max(0, len(lines) - 40), len(lines)))
        indices = sorted(keep)[:140]
        candidate = "\n".join(lines[i] for i in indices)
        omitted = max(0, len(lines) - len(indices))
    elif len(lines) > 120:
        candidate = "\n".join(lines[:70] + [f"… {len(lines)-110} lines omitted …"] + lines[-40:])
        omitted = len(lines) - 110
    if omitted:
        candidate += f"\n… {omitted} routine lines omitted; raw output retained by KITT"
    if not candidate.strip() or len(candidate) >= len(raw):
        candidate, omitted = raw, 0
    return {
        "output": candidate, "family": family, "changed": len(candidate) < len(raw),
        "raw_bytes": len(raw.encode()), "output_bytes": len(candidate.encode()),
        "omitted_lines": omitted, "raw_sha256": _sha(raw),
    }
