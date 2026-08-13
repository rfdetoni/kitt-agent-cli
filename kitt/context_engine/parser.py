import re
import ast
from pathlib import Path
from typing import List, Optional, Set
from kitt.domain.entities import Tag, FileTags

JAVA_PACKAGE_REGEX = re.compile(r'^\s*package\s+([a-zA-Z0-9_.]+)\s*;', re.MULTILINE)
JAVA_IMPORT_REGEX = re.compile(r'^\s*import\s+([a-zA-Z0-9_.]+)\s*;', re.MULTILINE)
JAVA_TYPE_REGEX = re.compile(r'^\s*(public|protected|private|static|abstract|final|\s)*\s*(class|interface|enum|record)\s+([A-Za-z0-9_]+)', re.MULTILINE)
JAVA_METHOD_REGEX = re.compile(r'^\s*(public|protected|private|static|abstract|final|synchronized|\s)*\s*([A-Za-z0-9_<>\[\]]+)\s+([A-Za-z0-9_]+)\s*\(([^)]*)\)', re.MULTILINE)
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
        lines = content.splitlines()

        for idx, line in enumerate(lines, start=1):
            t_match = JAVA_TYPE_REGEX.match(line)
            if t_match:
                kind = t_match.group(2)
                name = t_match.group(3)
                sig = f"{kind} {name}"
                tags.append(Tag(kind='def', name=name, line=idx, signature=sig, sub_kind=kind))
                continue

            m_match = JAVA_METHOD_REGEX.match(line)
            if m_match:
                name = m_match.group(3)
                ret_type = m_match.group(2)
                if name not in {'if', 'for', 'while', 'switch', 'catch'}:
                    sig = f"{ret_type} {name}(...)"
                    tags.append(Tag(kind='def', name=name, line=idx, signature=sig, sub_kind='method'))
                continue

            for ref in JAVA_REF_REGEX.findall(line):
                if ref not in {'String', 'Integer', 'Boolean', 'List', 'Map', 'Set', 'Object', 'Class'}:
                    tags.append(Tag(kind='ref', name=ref, line=idx, signature=ref, sub_kind='type_ref'))

        return tags

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
