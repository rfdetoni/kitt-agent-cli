from pathlib import Path
from typing import List, Tuple
from kitt.domain.entities import EditBlock, EditResult

class DiffApplier:
    """Exact & fuzzy-match validator and file system edit applier for small model diff resilience."""

    def _normalize_text(self, text: str) -> str:
        return "\n".join([line.strip() for line in text.strip().splitlines() if line.strip()])

    def _find_fuzzy_replacement(self, current_content: str, search_content: str) -> Tuple[bool, str]:
        if search_content in current_content:
            return True, search_content

        # Fuzzy whitespace matching
        norm_search = self._normalize_text(search_content)
        lines = current_content.splitlines()
        search_lines = search_content.splitlines()

        n_search = len(search_lines)
        for i in range(len(lines) - n_search + 1):
            chunk = "\n".join(lines[i:i + n_search])
            if self._normalize_text(chunk) == norm_search:
                return True, chunk

        return False, ""

    def apply(self, blocks: List[EditBlock], root_dir: str = ".") -> EditResult:
        root_path = Path(root_dir).resolve()
        applied_files: List[str] = []
        created_files: List[str] = []
        deleted_files: List[str] = []
        errors: List[str] = []
        replacements: List[Tuple[Path, str, str]] = []

        if not blocks:
            return EditResult(success=False, errors=["No valid SEARCH/REPLACE blocks found."])

        # Validate SEARCH blocks
        for block in blocks:
            full_path = (root_path / block.file_path).resolve()
            if block.is_new_file or not full_path.exists():
                continue

            try:
                current_content = full_path.read_text(encoding='utf-8', errors='ignore')
                found, target = self._find_fuzzy_replacement(current_content, block.search_content)
                if not found:
                    errors.append(
                        f"SEARCH block mismatch in '{block.file_path}'. Expected:\n---\n{block.search_content}\n---"
                    )
                else:
                    replacements.append((full_path, target, block.replace_content))
            except Exception as e:
                errors.append(f"Could not read file '{block.file_path}': {e}")

        if errors:
            return EditResult(success=False, errors=errors)

        # Apply edits
        for block in blocks:
            full_path = (root_path / block.file_path).resolve()
            if block.is_deletion:
                if full_path.exists():
                    full_path.unlink()
                    deleted_files.append(block.file_path)
                continue

            full_path.parent.mkdir(parents=True, exist_ok=True)
            if not full_path.exists() or block.is_new_file:
                full_path.write_text(block.replace_content, encoding='utf-8')
                created_files.append(block.file_path)
            else:
                current_content = full_path.read_text(encoding='utf-8', errors='ignore')
                found, target = self._find_fuzzy_replacement(current_content, block.search_content)
                if found:
                    updated_content = current_content.replace(target, block.replace_content, 1)
                    full_path.write_text(updated_content, encoding='utf-8')
                    applied_files.append(block.file_path)

        return EditResult(
            success=True,
            applied_files=applied_files,
            created_files=created_files,
            deleted_files=deleted_files,
            errors=[]
        )
