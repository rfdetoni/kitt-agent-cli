from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from kitt.domain.entities import EditBlock, EditResult
from kitt.edit_format.changeset import ChangeSetTracker
from kitt.security.workspace_fs import DEFAULT_MAX_FILE_BYTES, WorkspaceFileSystem
from kitt.tools.path_policy import WorkspacePathPolicy


class DiffApplier:
    """Transactional-ish SEARCH/REPLACE applier with race-resistant final IO."""

    def __init__(self, changeset_tracker: ChangeSetTracker = None):
        self.tracker = changeset_tracker or ChangeSetTracker()

    def _find_fuzzy_replacement(
        self,
        current_content: str,
        search_content: str,
    ) -> Tuple[bool, str, int]:
        import difflib
        if not search_content:
            return False, "", 0
        count = current_content.count(search_content)
        if count:
            return True, search_content, count

        lines = current_content.splitlines()
        search_lines = search_content.splitlines()
        n_search = len(search_lines)
        if n_search == 0 or not lines:
            return False, "", 0

        best_ratio = 0.0
        second_best = 0.0
        best_chunk = ""
        for index in range(len(lines) - n_search + 1):
            chunk = "\n".join(lines[index:index + n_search])
            ratio = difflib.SequenceMatcher(None, chunk, search_content).ratio()
            if ratio > best_ratio:
                second_best = best_ratio
                best_ratio = ratio
                best_chunk = chunk
            elif ratio > second_best:
                second_best = ratio
        if best_ratio >= 0.8:
            if best_ratio - second_best < 0.05 and second_best >= 0.8:
                return False, "", 0
            return True, best_chunk, 1
        return False, "", 0

    def validate_and_resolve_path(self, file_path: str, root_path: Path) -> Path:
        policy = WorkspacePathPolicy(root_dir=str(root_path))
        safe, full_path, error = policy.validate_path(file_path)
        if not safe or not full_path:
            raise ValueError(error or f"Access denied to path '{file_path}'.")
        return full_path

    def apply(
        self,
        blocks: List[EditBlock],
        root_dir: str = ".",
        allow_overwrite_existing: bool = False,
    ) -> EditResult:
        root = Path(root_dir).resolve()
        fs = WorkspaceFileSystem(root, max_file_bytes=DEFAULT_MAX_FILE_BYTES)
        self.tracker.root_dir = root
        if not blocks:
            return EditResult(success=False, errors=["No valid SEARCH/REPLACE blocks found."])

        prepared: list[dict] = []
        errors: list[str] = []
        snapshots = []

        for block in blocks:
            try:
                try:
                    rel = fs.relative(block.file_path)
                except PermissionError as exc:
                    message = str(exc)
                    if "Parent traversal" in message:
                        raise ValueError(f"Path containment violation: {message}") from exc
                    raise ValueError(f"Access denied: {message}") from exc
                # Preserve existing ChangeSet API, but never use this snapshot as
                # authority for the subsequent write.
                snapshots.append(self.tracker.create_snapshot(rel))
                try:
                    current_data = fs.read(rel)
                    exists = True
                    current = current_data.content.decode("utf-8", errors="ignore")
                    current_hash = current_data.sha256
                except FileNotFoundError:
                    exists = False
                    current = ""
                    current_hash = None

                if block.is_new_file and exists and not allow_overwrite_existing:
                    errors.append(f"Cannot overwrite existing file '{rel}' with is_new_file=True.")
                    continue
                if not block.is_new_file and not exists:
                    errors.append(f"File '{rel}' does not exist and is_new_file=False.")
                    continue

                target = ""
                if exists and not block.is_new_file and not block.is_deletion:
                    if not block.search_content:
                        errors.append(f"SEARCH block is empty for existing file '{rel}'.")
                        continue
                    found, target, count = self._find_fuzzy_replacement(current, block.search_content)
                    if not found:
                        errors.append(f"SEARCH block mismatch in '{rel}'.")
                        continue
                    if count > 1:
                        errors.append(f"Ambiguous SEARCH block in '{rel}': matched {count} occurrences.")
                        continue

                prepared.append({
                    "block": block,
                    "rel": rel,
                    "exists": exists,
                    "current": current,
                    "current_hash": current_hash,
                    "target": target,
                })
            except Exception as exc:
                errors.append(f"Validation error for '{block.file_path}': {exc}")

        if errors:
            return EditResult(success=False, errors=errors)

        applied_files: list[str] = []
        created_files: list[str] = []
        deleted_files: list[str] = []

        # Revalidate exact content hash at the write boundary. If another
        # process changes a file after validation, the entire operation stops.
        try:
            for item in prepared:
                block = item["block"]
                rel = item["rel"]
                if block.is_deletion:
                    if item["exists"]:
                        latest = fs.read(rel)
                        if latest.sha256 != item["current_hash"]:
                            raise RuntimeError(f"File '{rel}' changed during patch application")
                        fs.unlink(rel)
                        deleted_files.append(rel)
                    continue

                if not item["exists"] or block.is_new_file:
                    fs.atomic_write(
                        rel,
                        block.replace_content,
                        expected_sha256=item["current_hash"] if item["exists"] else None,
                    )
                    (applied_files if item["exists"] else created_files).append(rel)
                    continue

                updated = item["current"].replace(item["target"], block.replace_content, 1)
                fs.atomic_write(
                    rel,
                    updated,
                    expected_sha256=item["current_hash"],
                )
                applied_files.append(rel)
        except Exception as exc:
            return EditResult(
                success=False,
                applied_files=applied_files,
                created_files=created_files,
                deleted_files=deleted_files,
                errors=[f"Patch application stopped: {exc}"],
            )

        changeset = self.tracker.record_changeset(
            description=f"Applied {len(blocks)} edit block(s)",
            snapshots=snapshots,
        )
        return EditResult(
            success=True,
            applied_files=applied_files,
            created_files=created_files,
            deleted_files=deleted_files,
            errors=[],
            changeset=changeset,
        )
