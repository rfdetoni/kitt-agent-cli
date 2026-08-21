from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List, Tuple

from kitt.domain.entities import EditBlock, EditResult, FileSnapshot
from kitt.edit_format.changeset import ChangeSetTracker
from kitt.edit_format.transaction import workspace_mutation_lock
from kitt.security.workspace_fs import DEFAULT_MAX_FILE_BYTES, WorkspaceFileSystem
from kitt.tools.path_policy import WorkspacePathPolicy


_MAX_CHANGESET_FILES = 128
_MAX_CHANGESET_JOURNAL_BYTES = 32 * 1024 * 1024


class DiffApplier:
    """Atomic-at-workspace-boundary SEARCH/REPLACE applier.

    Every file is prepared first, optimistic preconditions are checked at the
    write boundary, each path is mutated only once, and a failed multi-file
    commit rolls back already-applied paths using post-write hashes.
    """

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

    @staticmethod
    def _text_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _prepare(
        self,
        blocks: List[EditBlock],
        fs: WorkspaceFileSystem,
        allow_overwrite_existing: bool,
    ) -> tuple[list[dict], list[FileSnapshot], list[str]]:
        grouped: Dict[str, list[EditBlock]] = {}
        order: list[str] = []
        errors: list[str] = []

        for block in blocks:
            try:
                self.validate_and_resolve_path(block.file_path, Path(fs.root))
                rel = fs.relative(block.file_path)
            except Exception as exc:
                errors.append(f"Validation error for '{block.file_path}': {exc}")
                continue
            if rel not in grouped:
                grouped[rel] = []
                order.append(rel)
            grouped[rel].append(block)

        prepared: list[dict] = []
        snapshots: list[FileSnapshot] = []
        if errors:
            return prepared, snapshots, errors

        for rel in order:
            path_blocks = grouped[rel]
            try:
                try:
                    current_data = fs.read(rel)
                    initial_exists = True
                    initial_content = current_data.content.decode("utf-8")
                    initial_hash = current_data.sha256
                except FileNotFoundError:
                    initial_exists = False
                    initial_content = ""
                    initial_hash = None

                snapshots.append(
                    FileSnapshot(
                        relative_path=rel,
                        existed=initial_exists,
                        content=initial_content if initial_exists else None,
                    )
                )
                working_exists = initial_exists
                working = initial_content

                for index, block in enumerate(path_blocks):
                    if block.is_deletion:
                        if not working_exists:
                            raise ValueError(f"File '{rel}' does not exist for deletion")
                        if index != len(path_blocks) - 1:
                            raise ValueError(f"Deletion for '{rel}' must be the final block for that path")
                        working_exists = False
                        working = ""
                        continue

                    if block.is_new_file:
                        if working_exists and not allow_overwrite_existing:
                            raise ValueError(
                                f"Cannot overwrite existing file '{rel}' with is_new_file=True"
                            )
                        working_exists = True
                        working = block.replace_content
                        continue

                    if not working_exists:
                        raise ValueError(f"File '{rel}' does not exist and is_new_file=False")
                    if not block.search_content:
                        raise ValueError(f"SEARCH block is empty for existing file '{rel}'")
                    found, target, count = self._find_fuzzy_replacement(
                        working, block.search_content
                    )
                    if not found:
                        raise ValueError(f"SEARCH block mismatch in '{rel}'")
                    if count > 1:
                        raise ValueError(
                            f"Ambiguous SEARCH block in '{rel}': matched {count} occurrences"
                        )
                    working = working.replace(target, block.replace_content, 1)

                prepared.append(
                    {
                        "rel": rel,
                        "initial_exists": initial_exists,
                        "initial_content": initial_content,
                        "initial_hash": initial_hash,
                        "final_exists": working_exists,
                        "final_content": working,
                        "final_hash": self._text_hash(working) if working_exists else None,
                    }
                )
            except Exception as exc:
                errors.append(str(exc))

        return prepared, snapshots, errors

    @staticmethod
    def _verify_initial(fs: WorkspaceFileSystem, item: dict) -> None:
        rel = item["rel"]
        try:
            current = fs.read(rel)
            exists = True
        except FileNotFoundError:
            current = None
            exists = False
        if exists != item["initial_exists"]:
            raise RuntimeError(f"File '{rel}' existence changed during patch preparation")
        if exists and current and current.sha256 != item["initial_hash"]:
            raise RuntimeError(f"File '{rel}' changed during patch preparation")

    @staticmethod
    def _rollback_one(fs: WorkspaceFileSystem, item: dict) -> None:
        rel = item["rel"]
        final_exists = item["final_exists"]
        final_hash = item["final_hash"]
        if item["initial_exists"]:
            fs.atomic_write(
                rel,
                item["initial_content"],
                expected_exists=final_exists,
                expected_sha256=final_hash if final_exists else None,
            )
        elif final_exists:
            fs.unlink(rel, expected_exists=True, expected_sha256=final_hash)

    def apply(
        self,
        blocks: List[EditBlock],
        root_dir: str = ".",
        allow_overwrite_existing: bool = False,
        *,
        workspace_id: str = "",
        conversation_id: str = "",
        turn_id: str = "",
    ) -> EditResult:
        root = Path(root_dir).resolve()
        fs = WorkspaceFileSystem(root, max_file_bytes=DEFAULT_MAX_FILE_BYTES)
        self.tracker.root_dir = root
        if not blocks:
            return EditResult(success=False, errors=["No valid SEARCH/REPLACE blocks found."])

        mutation_lock = workspace_mutation_lock(root)
        with mutation_lock:
            prepared, snapshots, errors = self._prepare(
                blocks, fs, allow_overwrite_existing
            )
            if errors:
                return EditResult(success=False, errors=errors)
            if len(prepared) > _MAX_CHANGESET_FILES:
                return EditResult(
                    success=False,
                    errors=[f"Patch exceeds {_MAX_CHANGESET_FILES} files per changeset"],
                )
            journal_bytes = sum(
                len(item["initial_content"].encode("utf-8"))
                + (len(item["final_content"].encode("utf-8")) if item["final_exists"] else 0)
                for item in prepared
            )
            if journal_bytes > _MAX_CHANGESET_JOURNAL_BYTES:
                return EditResult(
                    success=False,
                    errors=[f"Patch undo journal exceeds {_MAX_CHANGESET_JOURNAL_BYTES} bytes"],
                )

            # Validate every path before performing the first mutation.
            try:
                for item in prepared:
                    self._verify_initial(fs, item)
            except Exception as exc:
                return EditResult(success=False, errors=[f"Patch precondition failed: {exc}"])

            applied_items: list[dict] = []
            applied_files: list[str] = []
            created_files: list[str] = []
            deleted_files: list[str] = []
            try:
                for item in prepared:
                    rel = item["rel"]
                    if item["final_exists"]:
                        fs.atomic_write(
                            rel,
                            item["final_content"],
                            expected_exists=item["initial_exists"],
                            expected_sha256=(
                                item["initial_hash"] if item["initial_exists"] else None
                            ),
                        )
                        if item["initial_exists"]:
                            applied_files.append(rel)
                        else:
                            created_files.append(rel)
                    elif item["initial_exists"]:
                        fs.unlink(
                            rel,
                            expected_exists=True,
                            expected_sha256=item["initial_hash"],
                        )
                        deleted_files.append(rel)
                    applied_items.append(item)
            except Exception as exc:
                rollback_errors: list[str] = []
                for applied in reversed(applied_items):
                    try:
                        self._rollback_one(fs, applied)
                    except Exception as rb_exc:
                        rollback_errors.append(f"{applied['rel']}: {rb_exc}")
                suffix = (
                    f"; rollback failures: {'; '.join(rollback_errors)}"
                    if rollback_errors
                    else "; all earlier mutations rolled back"
                )
                return EditResult(
                    success=False,
                    errors=[f"Patch application stopped: {exc}{suffix}"],
                )

            post_hashes = {item["rel"]: item["final_hash"] for item in prepared}
            post_exists = {item["rel"]: item["final_exists"] for item in prepared}
            post_contents = {
                item["rel"]: item["final_content"] if item["final_exists"] else None
                for item in prepared
            }
            try:
                changeset = self.tracker.record_changeset(
                    description=f"Applied {len(blocks)} edit block(s)",
                    snapshots=snapshots,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    post_hashes=post_hashes,
                    post_exists=post_exists,
                    post_contents=post_contents,
                )
            except Exception as exc:
                rollback_errors: list[str] = []
                for applied in reversed(applied_items):
                    try:
                        self._rollback_one(fs, applied)
                    except Exception as rb_exc:
                        rollback_errors.append(f"{applied['rel']}: {rb_exc}")
                suffix = f"; rollback failures: {'; '.join(rollback_errors)}" if rollback_errors else "; file changes rolled back"
                return EditResult(success=False, errors=[f"Undo journal persistence failed: {exc}{suffix}"])
            return EditResult(
                success=True,
                applied_files=applied_files,
                created_files=created_files,
                deleted_files=deleted_files,
                errors=[],
                changeset=changeset,
            )
