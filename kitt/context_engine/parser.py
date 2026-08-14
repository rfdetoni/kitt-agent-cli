import re
import ast
from pathlib import Path
from typing import List, Optional, Set
from kitt.domain.entities import Tag, FileTags

JAVA_PACKAGE_REGEX = re.compile(r'^\s*package\s+([a-zA-Z0-9_.]+)\s*;', re.MULTILINE)
JAVA_IMPORT_REGEX = re.compile(r'^\s*import\s+(?:static\s+)?([a-zA-Z0-9_.*]+)\s*;', re.MULTILINE)
JAVA_TYPE_REGEX = re.compile(r'^\s*(public|protected|private|static|abstract|final|\s)*\s*(class|interface|enum|record)\s+([A-Za-z0-9_]+)', re.MULTILINE)
JAVA_METHOD_REGEX = re.compile(r'^\s*(?:@\w+(?:\([^)]*\))?\s*)*(public|protected|private|static|abstract|final|synchronized|\s)*\s*(?:<[^>]+>\s*)?([A-Za-z0-9_<>,.?[\]]+)\s+([A-Za-z0-9_]+)\s*\(([^)]*)\)', re.MULTILINE | re.DOTALL)
JAVA_REF_REGEX = re.compile(r'\b([A-Z][A-Za-z0-9_]*)\b')

class SymbolParser:
    """Multi-language symbol extractor for definitions and references."""

    TS_JS_DEF_REGEX = re.compile(
        r'^\s*(export\s+)?(async\s+)?(function|class|interface|type|const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)',
        re.MULTILINE
    )

    PY_DEF_REGEX = re.compile(
        r'^\s*(async\s+)?(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)',
        re.MULTILINE
    )

    GENERIC_DEF_REGEX = re.compile(
        r'^\s*(pub\s+)?(fn|struct|enum|class|def|func|type)\s+([A-Za-z0-9_]+)',
        re.MULTILINE
    )

    def extract_file_tags(self, file_path: Path, relative_path: str) -> Optional[FileTags]:
        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            return None

        ext = file_path.suffix.lower()
        tags: List[Tag] = []

        if ext == '.py':
            tags = self._extract_python_tags(content)
        elif ext in ['.java']:
            tags = self._extract_java_tags(content)
        elif ext in ['.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs']:
            tags = self._extract_ts_js_tags(content)
        elif ext == '.go':
            tags = self._extract_go_tags(content)
        elif ext in ['.rs']:
            tags = self._extract_rust_tags(content)
        else:
            tags = self._extract_generic_tags(content)

        return FileTags(path=relative_path, tags=tags)

    def _extract_python_tags(self, content: str) -> List[Tag]:
        tags: List[Tag] = []
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    sig = f"def {node.name}(...)"
                    tags.append(Tag(kind='def', name=node.name, line=node.lineno, signature=sig, sub_kind='function'))
                elif isinstance(node, ast.ClassDef):
                    sig = f"class {node.name}:"
                    tags.append(Tag(kind='def', name=node.name, line=node.lineno, signature=sig, sub_kind='class'))
                elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    tags.append(Tag(kind='ref', name=node.id, line=node.lineno, signature=node.id, sub_kind='ref'))
        except SyntaxError:
            lines = content.splitlines()
            for idx, line in enumerate(lines, start=1):
                match = self.PY_DEF_REGEX.match(line)
                if match:
                    name = match.group(3)
                    tags.append(Tag(kind='def', name=name, line=idx, signature=line.strip(), sub_kind='definition'))

        return tags

    def _extract_java_tags(self, content: str) -> List[Tag]:
        tags: List[Tag] = []
        clean = self._strip_java_comments_and_strings(content)
        lines = clean.splitlines()
        package = ""
        package_match = JAVA_PACKAGE_REGEX.search(clean)
        if package_match:
            package = package_match.group(1)
            tags.append(Tag(kind='ref', name=package, line=1, signature=f"package {package}", sub_kind='package'))

        for match in JAVA_IMPORT_REGEX.finditer(clean):
            imported = match.group(1)
            line_no = clean[:match.start()].count("\n") + 1
            tags.append(Tag(kind='ref', name=imported.split(".")[-1].rstrip("*"), line=line_no, signature=f"import {imported}", sub_kind='import'))

        pending = ""
        pending_line = 1

        for idx, line in enumerate(lines, start=1):
            t_match = JAVA_TYPE_REGEX.match(line)
            if t_match:
                kind = t_match.group(2)
                name = t_match.group(3)
                sig = f"{kind} {package + '.' if package else ''}{name}"
                tags.append(Tag(kind='def', name=name, line=idx, signature=sig, sub_kind=kind))
                continue

            stripped = line.strip()
            if pending:
                pending += " " + stripped
            elif "(" in stripped and not stripped.startswith(("if ", "for ", "while ", "switch ", "catch ")):
                pending = stripped
                pending_line = idx
            if pending and ")" in pending:
                m_match = JAVA_METHOD_REGEX.match(pending)
                if m_match:
                    name = m_match.group(3)
                    ret_type = m_match.group(2)
                    if name not in {'if', 'for', 'while', 'switch', 'catch', 'new'}:
                        params = " ".join(m_match.group(4).split())
                        if ret_type in {"public", "protected", "private"} and name[:1].isupper():
                            tags.append(Tag(kind='def', name=name, line=pending_line, signature=f"{name}({params}) constructor", sub_kind='constructor'))
                        else:
                            sig = f"{ret_type} {name}({params})"
                            tags.append(Tag(kind='def', name=name, line=pending_line, signature=sig, sub_kind='method'))
                else:
                    ctor = re.match(r'^\s*(public|protected|private)?\s*([A-Z][A-Za-z0-9_]*)\s*\(', pending)
                    if ctor:
                        name = ctor.group(2)
                        tags.append(Tag(kind='def', name=name, line=pending_line, signature=f"{name}(...) constructor", sub_kind='constructor'))
                pending = ""
                continue

            # Constructor: public ClassName(...)
            ctor = re.match(r'^\s*(public|protected|private)?\s*([A-Z][A-Za-z0-9_]*)\s*\(', line)
            if ctor:
                name = ctor.group(2)
                if name not in {'if', 'for', 'while', 'switch', 'catch'}:
                    tags.append(Tag(kind='def', name=name, line=idx, signature=f"{name}(...) constructor", sub_kind='constructor'))
                continue

            for ref in JAVA_REF_REGEX.findall(line):
                if ref not in {'String', 'Integer', 'Boolean', 'List', 'Map', 'Set', 'Object', 'Class'}:
                    tags.append(Tag(kind='ref', name=ref, line=idx, signature=ref, sub_kind='type_ref'))

        return tags

    @staticmethod
    def _strip_java_comments_and_strings(content: str) -> str:
        out = []
        i = 0
        state = "code"
        while i < len(content):
            ch = content[i]
            nxt = content[i + 1] if i + 1 < len(content) else ""
            if state == "code" and ch == "/" and nxt == "/":
                state = "line_comment"; out.extend("  "); i += 2; continue
            if state == "code" and ch == "/" and nxt == "*":
                state = "block_comment"; out.extend("  "); i += 2; continue
            if state == "code" and ch in {'"', "'"}:
                state = ch; out.append(" "); i += 1; continue
            if state == "line_comment":
                out.append("\n" if ch == "\n" else " ")
                if ch == "\n":
                    state = "code"
                i += 1; continue
            if state == "block_comment":
                out.append("\n" if ch == "\n" else " ")
                if ch == "*" and nxt == "/":
                    out.append(" "); i += 2; state = "code"; continue
                i += 1; continue
            if state in {'"', "'"}:
                out.append("\n" if ch == "\n" else " ")
                if ch == "\\":
                    i += 2; out.append(" "); continue
                if ch == state:
                    state = "code"
                i += 1; continue
            out.append(ch)
            i += 1
        return "".join(out)

    def _extract_ts_js_tags(self, content: str) -> List[Tag]:
        tags: List[Tag] = []
        lines = content.splitlines()
        for idx, line in enumerate(lines, start=1):
            match = self.TS_JS_DEF_REGEX.match(line)
            if match:
                name = match.group(4)
                sig = line.strip().rstrip('{').rstrip(';')
                tags.append(Tag(kind='def', name=name, line=idx, signature=sig, sub_kind=match.group(3)))

        return tags

    def _extract_go_tags(self, content: str) -> List[Tag]:
        tags: List[Tag] = []
        go_def = re.compile(r'^\s*(func|type)\s+([A-Za-z0-9_]+)', re.MULTILINE)
        lines = content.splitlines()
        for idx, line in enumerate(lines, start=1):
            m = go_def.match(line)
            if m:
                tags.append(Tag(kind='def', name=m.group(2), line=idx, signature=line.strip(), sub_kind=m.group(1)))
        return tags

    def _extract_rust_tags(self, content: str) -> List[Tag]:
        tags: List[Tag] = []
        rs_def = re.compile(r'^\s*(pub(\([^)]+\))?\s+)?(fn|struct|enum|trait|type|impl)\s+([A-Za-z0-9_]+)', re.MULTILINE)
        lines = content.splitlines()
        for idx, line in enumerate(lines, start=1):
            m = rs_def.match(line)
            if m:
                tags.append(Tag(kind='def', name=m.group(4), line=idx, signature=line.strip(), sub_kind=m.group(3)))
        return tags

    def _extract_generic_tags(self, content: str) -> List[Tag]:
        tags: List[Tag] = []
        lines = content.splitlines()
        for idx, line in enumerate(lines, start=1):
            match = self.GENERIC_DEF_REGEX.match(line)
            if match:
                name = match.group(3)
                tags.append(Tag(kind='def', name=name, line=idx, signature=line.strip(), sub_kind='def'))
        return tags
