from __future__ import annotations

import json
import re
from typing import List, Literal

from kitt.domain.entities import EditBlock


PatchFormat = Literal["search_replace", "unified_diff", "begin_patch", "unknown"]


def detect_patch_format(text: str) -> PatchFormat:
    value = str(text or "")
    if "<<<<<<< SEARCH" in value and ">>>>>>> REPLACE" in value:
        return "search_replace"
    if re.search(r"(?m)^\*\*\* Begin Patch\r?$", value):
        return "begin_patch"
    if re.search(r"(?m)^diff --git ", value):
        return "unified_diff"
    if (
        re.search(r"(?m)^--- .+$", value)
        and re.search(r"(?m)^\+\+\+ .+$", value)
        and re.search(r"(?m)^@@ ", value)
    ):
        return "unified_diff"
    return "unknown"


class SearchReplaceParser:
    """Parser for SEARCH/REPLACE blocks with an explicit, stable file target."""

    BLOCK_REGEX = re.compile(
        r'(?:^([a-zA-Z0-9_\-./]+\.[a-zA-Z][a-zA-Z0-9]*)\r?\n)?^<<<<<<< SEARCH\r?\n([\s\S]*?)(?:\r?\n)?=======\r?\n([\s\S]*?)\r?\n>>>>>>> REPLACE',
        re.MULTILINE,
    )
    FILENAME_EXTRACTOR = re.compile(r'[a-zA-Z0-9_\-./]+\.[a-zA-Z][a-zA-Z0-9]*')
    FILE_DIRECTIVE = re.compile(r'^\*\*\* (?:Update|Add|Delete) File:\s*(.+?)\s*$')

    @classmethod
    def _explicit_file(cls, prefix: str) -> str:
        """Only accept a standalone path or a patch file directive.

        Never search preceding SEARCH/REPLACE body text for filename-shaped
        substrings; version numbers and documentation references are data.
        """
        for line in reversed(prefix.splitlines()):
            clean = line.strip()
            directive = cls.FILE_DIRECTIVE.fullmatch(clean)
            candidate = (
                directive.group(1).strip()
                if directive
                else clean.strip(":`'\"#* ")
            )
            if cls.FILENAME_EXTRACTOR.fullmatch(candidate):
                return candidate
        return ""

    def parse(self, text: str) -> List[EditBlock]:
        blocks: List[EditBlock] = []
        active_file = ""
        previous_end = 0

        for match in self.BLOCK_REGEX.finditer(text):
            # Inspect only text outside completed blocks. Subsequent blocks
            # without a header belong to the last explicitly selected file.
            prefix = text[previous_end:match.start()]
            file_path = match.group(1) or self._explicit_file(prefix) or active_file
            if not file_path:
                return []
            active_file = file_path
            previous_end = match.end()

            search_content = match.group(2)
            replace_content = match.group(3)
            blocks.append(EditBlock(
                file_path=file_path,
                search_content=search_content,
                replace_content=replace_content,
                is_new_file=len(search_content) == 0,
                is_deletion=len(replace_content) == 0 and len(search_content) > 0,
            ))

        if blocks:
            return blocks

        # Preserve the existing whole-file fallback only when the first line
        # is itself an explicit file path and this is not a malformed diff.
        if not text.strip() or "<<<<<<< SEARCH" in text or ">>>>>>> REPLACE" in text:
            return []
        clean = text.strip()
        lines = clean.splitlines()
        first_line = lines[0].strip(":`'\"#* ")
        if not self.FILENAME_EXTRACTOR.fullmatch(first_line) or len(lines) <= 1:
            return []
        body = "\n".join(lines[1:]).strip()
        code_m = re.search(r'```(?:[a-zA-Z0-9_-]+)?\s*\n([\s\S]*?)\n```', body)
        replacement = code_m.group(1) if code_m else body
        if replacement and (
            code_m or first_line.endswith(
                ('.html', '.css', '.js', '.ts', '.tsx', '.jsx', '.py',
                 '.json', '.md', '.txt', '.sh')
            )
        ):
            return [EditBlock(
                file_path=first_line,
                search_content="",
                replace_content=replacement,
                is_new_file=False,
                is_deletion=False,
            )]
        return []


class UnifiedDiffParser:
    """Strict unified-diff parser translated into the existing EditBlock model.

    The parser intentionally does not apply line numbers directly. Instead, each
    hunk becomes an anchored SEARCH/REPLACE block, preserving the existing path
    policy, approval-integrity, transaction, rollback and post-edit validation
    boundaries.
    """

    HUNK_HEADER = re.compile(
        r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
        r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?: .*)?$"
    )

    @staticmethod
    def _strip_fence(text: str) -> str:
        clean = str(text or "").strip()
        match = re.fullmatch(
            r"```(?:diff|patch)?\s*\n([\s\S]*?)\n```",
            clean,
            re.IGNORECASE,
        )
        return match.group(1) if match else clean

    @staticmethod
    def _normalize_path(raw: str) -> str:
        value = raw.strip()
        if "\t" in value:
            value = value.split("\t", 1)[0]
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except (TypeError, ValueError, json.JSONDecodeError):
                value = value.strip('"')
        if value == "/dev/null":
            return value
        if value.startswith(("a/", "b/")):
            value = value[2:]
        return value

    def parse(self, text: str) -> List[EditBlock]:
        source = self._strip_fence(text)
        if not source:
            return []

        lines = source.splitlines()
        blocks: List[EditBlock] = []
        index = 0

        while index < len(lines):
            if not lines[index].startswith("--- "):
                index += 1
                continue
            if index + 1 >= len(lines) or not lines[index + 1].startswith("+++ "):
                return []

            old_path = self._normalize_path(lines[index][4:])
            new_path = self._normalize_path(lines[index + 1][4:])
            if old_path == "/dev/null" and new_path == "/dev/null":
                return []
            target_path = new_path if new_path != "/dev/null" else old_path
            if not target_path:
                return []

            is_new_file = old_path == "/dev/null"
            is_deletion = new_path == "/dev/null"
            index += 2
            file_blocks: List[EditBlock] = []

            while index < len(lines):
                current = lines[index]
                if current.startswith("diff --git ") or current.startswith("--- "):
                    break
                if not current.startswith("@@ "):
                    index += 1
                    continue

                header = self.HUNK_HEADER.match(current)
                if not header:
                    return []
                old_count = int(header.group("old_count") or "1")
                new_count = int(header.group("new_count") or "1")
                index += 1

                search_lines: list[str] = []
                replace_lines: list[str] = []
                observed_old = 0
                observed_new = 0
                last_prefix = ""
                new_no_newline = False

                while index < len(lines) and (
                    observed_old < old_count or observed_new < new_count
                ):
                    body_line = lines[index]
                    if body_line.startswith(("@@ ", "diff --git ")):
                        return []
                    if body_line.startswith(r"\ No newline at end of file"):
                        if last_prefix == "+":
                            new_no_newline = True
                        index += 1
                        continue
                    if not body_line or body_line[0] not in " +-":
                        return []

                    prefix = body_line[0]
                    payload = body_line[1:]
                    last_prefix = prefix
                    if prefix in " -":
                        search_lines.append(payload)
                        observed_old += 1
                    if prefix in " +":
                        replace_lines.append(payload)
                        observed_new += 1
                    index += 1

                if observed_old != old_count or observed_new != new_count:
                    return []
                if (
                    index < len(lines)
                    and lines[index].startswith(r"\ No newline at end of file")
                ):
                    if last_prefix == "+":
                        new_no_newline = True
                    index += 1

                search_content = "\n".join(search_lines)
                replace_content = "\n".join(replace_lines)

                if is_new_file:
                    file_blocks.append(EditBlock(
                        file_path=target_path,
                        search_content="",
                        replace_content=(
                            replace_content
                            if new_no_newline or not replace_content
                            else replace_content + "\n"
                        ),
                        is_new_file=True,
                        is_deletion=False,
                    ))
                elif is_deletion:
                    file_blocks.append(EditBlock(
                        file_path=target_path,
                        search_content=search_content,
                        replace_content="",
                        is_new_file=False,
                        is_deletion=True,
                    ))
                else:
                    # Pure insertion hunks without context cannot be safely
                    # translated into the current content-anchored mutation
                    # engine. Require at least one context/deleted line.
                    if not search_content:
                        return []
                    file_blocks.append(EditBlock(
                        file_path=target_path,
                        search_content=search_content,
                        replace_content=replace_content,
                        is_new_file=False,
                        is_deletion=False,
                    ))

            if not file_blocks:
                return []
            if (is_new_file or is_deletion) and len(file_blocks) != 1:
                return []
            blocks.extend(file_blocks)

        return blocks


class BeginPatchUpdateParser:
    """Parse a bounded *** Begin Patch / *** Update File change set.

    Supports existing-file updates only. Convert each content-anchored hunk to
    EditBlock so path policy, ambiguity detection, approvals and rollback all
    stay in the existing mutation engine. Never guess a target or position.
    """

    HEADER = "*** Update File: "

    def parse(self, text: str) -> List[EditBlock]:
        source = UnifiedDiffParser._strip_fence(text)
        lines = source.splitlines()
        if len(lines) < 4 or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
            return []

        blocks: List[EditBlock] = []
        index = 1
        while index < len(lines) - 1:
            directive = lines[index]
            if not directive.startswith(self.HEADER):
                # Add/Delete/Move are intentionally unsupported here.
                return []
            path = directive[len(self.HEADER):].strip()
            if not path:
                return []
            index += 1
            file_hunks = 0
            while index < len(lines) - 1 and (
                lines[index] == "@@" or lines[index].startswith("@@ ")
            ):
                index += 1
                original: list[str] = []
                replacement: list[str] = []
                changes = False
                while index < len(lines) - 1:
                    line = lines[index]
                    if line == "@@" or line.startswith("@@ ") or line.startswith("*** "):
                        break
                    if not line or line[0] not in " +-":
                        return []
                    prefix, value = line[0], line[1:]
                    if prefix in " -":
                        original.append(value)
                    if prefix in " +":
                        replacement.append(value)
                    changes |= prefix in "-+"
                    index += 1

                # No pure insertion at an unanchored position, and no no-op
                # hunks. EditApplier will reject ambiguous/missing context.
                if not original or not changes:
                    return []
                blocks.append(EditBlock(
                    file_path=path,
                    search_content="\n".join(original),
                    replace_content="\n".join(replacement),
                    is_new_file=False,
                    is_deletion=False,
                ))
                file_hunks += 1
            if not file_hunks:
                return []
        return blocks


class PatchParser:
    """Accept explicitly supported patch dialects through one mutation engine."""

    def __init__(self):
        self.search_replace = SearchReplaceParser()
        self.unified_diff = UnifiedDiffParser()
        self.begin_patch = BeginPatchUpdateParser()

    def format(self, text: str) -> PatchFormat:
        return detect_patch_format(text)

    def parse(self, text: str) -> List[EditBlock]:
        patch_format = self.format(text)
        if patch_format == "unified_diff":
            return self.unified_diff.parse(text)
        if patch_format == "begin_patch":
            return self.begin_patch.parse(text)
        blocks = self.search_replace.parse(text)
        if blocks:
            return blocks
        if patch_format == "unknown":
            return self.unified_diff.parse(text)
        return []
