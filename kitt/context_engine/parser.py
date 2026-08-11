import re
import ast
from pathlib import Path
from typing import List, Optional
from kitt.domain.entities import Tag, FileTags

class SymbolParser:
    """Fast, multi-language definition and signature extractor."""

    TS_JS_DEF_REGEX = re.compile(
        r'^\s*(export\s+)?(async\s+)?(function|class|interface|type|const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)',
        re.MULTILINE
    )

    PY_DEF_REGEX = re.compile(
        r'^\s*(async\s+)?(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)',
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
        elif ext in ['.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs']:
            tags = self._extract_ts_js_tags(content)
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
        except SyntaxError:
            lines = content.splitlines()
            for idx, line in enumerate(lines, start=1):
                match = self.PY_DEF_REGEX.match(line)
                if match:
                    name = match.group(3)
                    tags.append(Tag(kind='def', name=name, line=idx, signature=line.strip(), sub_kind='definition'))

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

    def _extract_generic_tags(self, content: str) -> List[Tag]:
        tags: List[Tag] = []
        lines = content.splitlines()
        generic_regex = re.compile(r'^\s*(pub\s+)?(fn|struct|enum|class|def|func)\s+([A-Za-z0-9_]+)')
        for idx, line in enumerate(lines, start=1):
            match = generic_regex.match(line)
            if match:
                name = match.group(3)
                tags.append(Tag(kind='def', name=name, line=idx, signature=line.strip(), sub_kind='def'))
        return tags
