import os
import tempfile
from pathlib import Path
from typing import List, Tuple, Set
from kitt.domain.entities import EditBlock, EditResult, FileSnapshot
from kitt.edit_format.changeset import ChangeSetTracker
from kitt.tools.path_policy import WorkspacePathPolicy

class DiffApplier:
    """Path-contained, transactional validator and edit applier with ChangeSet tracking."""

    def __init__(self, changeset_tracker: ChangeSetTracker = None):
        self.tracker = changeset_tracker or ChangeSetTracker()

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise

    def _find_fuzzy_replacement(self, current_content: str, search_content: str) -> Tuple[bool, str, int]:
        import difflib
        if not search_content:
            return False, "", 0

        count = current_content.count(search_content)
        if count > 0:
            return True, search_content, count

        # Fallback to difflib Levenshtein similarity
        lines = current_content.splitlines()
        search_lines = search_content.splitlines()
        n_search = len(search_lines)
        
        if n_search == 0 or len(lines) == 0:
            return False, "", 0

        best_ratio = 0.0
        best_chunk = ""
        second_best_ratio = 0.0

        for i in range(len(lines) - n_search + 1):
            chunk = "\n".join(lines[i:i + n_search])
            ratio = difflib.SequenceMatcher(None, chunk, search_content).ratio()
            
            if ratio > best_ratio:
                second_best_ratio = best_ratio
                best_ratio = ratio
                best_chunk = chunk
            elif ratio > second_best_ratio:
                second_best_ratio = ratio

        # Accept if we have a strong match (>= 0.8) and it's not ambiguous (clear winner)
        if best_ratio >= 0.8:
            if best_ratio - second_best_ratio < 0.05 and second_best_ratio >= 0.8:
                # Ambiguous match
                return False, "", 0
            return True, best_chunk, 1

        return False, "", 0

    def validate_and_resolve_path(self, file_path: str, root_path: Path) -> Path:
        policy = WorkspacePathPolicy(root_dir=str(root_path))
        is_safe, full_path, err = policy.validate_path(file_path)
        if not is_safe or not full_path:
            raise ValueError(err or f"Access denied to path '{file_path}'.")
        return full_path

    def apply(self, blocks: List[EditBlock], root_dir: str = ".", allow_overwrite_existing: bool = False) -> EditResult:
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

                if block.is_new_file and full_path.exists() and not allow_overwrite_existing:
                    errors.append(f"Cannot overwrite existing file '{block.file_path}' with is_new_file=True.")
                    continue

                if not block.is_new_file and not full_path.exists():
                    errors.append(f"File '{block.file_path}' does not exist and is_new_file=False.")
                    continue

                if not block.is_new_file and full_path.exists():
                    if not block.search_content:
                        errors.append(f"SEARCH block is empty for existing file '{block.file_path}'.")
                        continue

                    current_content = full_path.read_text(encoding='utf-8', errors='ignore')
                    found, match_target, match_count = self._find_fuzzy_replacement(current_content, block.search_content)
                    if not found:
                        errors.append(
                            f"SEARCH block mismatch in '{block.file_path}'. Expected:\n---\n{block.search_content}\n---"
                        )
                    elif match_count > 1:
                        errors.append(
                            f"Ambiguous SEARCH block in '{block.file_path}': matched {match_count} occurrences."
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
                existed_before = full_path.exists()
                self._atomic_write(full_path, block.replace_content)
                if existed_before:
                    applied_files.append(rel_path)
                else:
                    created_files.append(rel_path)
            else:
                current_content = full_path.read_text(encoding='utf-8', errors='ignore')
                found, target, _ = self._find_fuzzy_replacement(current_content, block.search_content)
                if found:
                    updated_content = current_content.replace(target, block.replace_content, 1)
                    self._atomic_write(full_path, updated_content)
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
