from pathlib import Path
from typing import List, Tuple, Set
from kitt.domain.entities import EditBlock, EditResult, FileSnapshot
from kitt.edit_format.changeset import ChangeSetTracker

class DiffApplier:
    """Path-contained, transactional validator and edit applier with ChangeSet tracking."""

    FORBIDDEN_NAMES: Set[str] = {".git", ".env"}

    def __init__(self, changeset_tracker: ChangeSetTracker = None):
        self.tracker = changeset_tracker or ChangeSetTracker()

    def _normalize_text(self, text: str) -> str:
        return "\n".join([line.strip() for line in text.strip().splitlines() if line.strip()])

    def _find_fuzzy_replacement(self, current_content: str, search_content: str) -> Tuple[bool, str]:
        if search_content in current_content:
            return True, search_content

        norm_search = self._normalize_text(search_content)
        lines = current_content.splitlines()
        search_lines = search_content.splitlines()

        n_search = len(search_lines)
        for i in range(len(lines) - n_search + 1):
            chunk = "\n".join(lines[i:i + n_search])
            if self._normalize_text(chunk) == norm_search:
                return True, chunk

        return False, ""

    def validate_and_resolve_path(self, file_path: str, root_path: Path) -> Path:
        raw_p = Path(file_path)
        if raw_p.is_absolute():
            full_path = raw_p.resolve()
        else:
            full_path = (root_path / raw_p).resolve()

        if not full_path.is_relative_to(root_path):
            raise ValueError(f"Path containment violation: '{file_path}' resolves outside workspace ({root_path}).")

        rel = full_path.relative_to(root_path)
        for part in rel.parts:
            if part in self.FORBIDDEN_NAMES or part.startswith(".env"):
                raise ValueError(f"Access denied to protected file or directory: '{rel}'.")

        return full_path

    def apply(self, blocks: List[EditBlock], root_dir: str = ".") -> EditResult:
        root_path = Path(root_dir).resolve()
        self.tracker.root_dir = root_path

        applied_files: List[str] = []
        created_files: List[str] = []
        deleted_files: List[str] = []
        errors: List[str] = []
        snapshots: List[FileSnapshot] = []

        if not blocks:
            return EditResult(success=False, errors=["No valid SEARCH/REPLACE blocks found."])

        # Step 1: Validate paths & SEARCH block matches
        for block in blocks:
            try:
                full_path = self.validate_and_resolve_path(block.file_path, root_path)
                rel_path = str(full_path.relative_to(root_path))
                snapshot = self.tracker.create_snapshot(rel_path)
                snapshots.append(snapshot)

                if not block.is_new_file and full_path.exists():
                    current_content = full_path.read_text(encoding='utf-8', errors='ignore')
                    found, _ = self._find_fuzzy_replacement(current_content, block.search_content)
                    if not found:
                        errors.append(
                            f"SEARCH block mismatch in '{block.file_path}'. Expected:\n---\n{block.search_content}\n---"
                        )
            except Exception as e:
                errors.append(f"Validation error for '{block.file_path}': {e}")

        if errors:
            return EditResult(success=False, errors=errors)

        # Step 2: Apply edits transactionally
        for block in blocks:
            full_path = self.validate_and_resolve_path(block.file_path, root_path)
            rel_path = str(full_path.relative_to(root_path))

            if block.is_deletion:
                if full_path.exists():
                    full_path.unlink()
                    deleted_files.append(rel_path)
                continue

            full_path.parent.mkdir(parents=True, exist_ok=True)
            if not full_path.exists() or block.is_new_file:
                full_path.write_text(block.replace_content, encoding='utf-8')
                created_files.append(rel_path)
            else:
                current_content = full_path.read_text(encoding='utf-8', errors='ignore')
                found, target = self._find_fuzzy_replacement(current_content, block.search_content)
                if found:
                    updated_content = current_content.replace(target, block.replace_content, 1)
                    full_path.write_text(updated_content, encoding='utf-8')
                    applied_files.append(rel_path)

        changeset = self.tracker.record_changeset(
            description=f"Applied {len(blocks)} edit block(s)",
            snapshots=snapshots
        )

        return EditResult(
            success=True,
            applied_files=applied_files,
            created_files=created_files,
            deleted_files=deleted_files,
            errors=[],
            changeset=changeset
        )
