import re
from typing import List
from kitt.domain.entities import EditBlock

class SearchReplaceParser:
    """Parser for SEARCH/REPLACE diff blocks emitted by the LLM."""

    BLOCK_REGEX = re.compile(
        r'(?:([a-zA-Z0-9_\-./]+\.[a-zA-Z0-9]+)\s*\n)?<<<<<<< SEARCH\r?\n([\s\S]*?)\r?\n=======\r?\n([\s\S]*?)\r?\n>>>>>>> REPLACE',
        re.MULTILINE
    )

    def parse(self, text: str) -> List[EditBlock]:
        blocks: List[EditBlock] = []
        for match in self.BLOCK_REGEX.finditer(text):
            file_path = match.group(1) or ""
            search_content = match.group(2)
            replace_content = match.group(3)

            if not file_path:
                prefix = text[:match.start()].rstrip()
                lines = prefix.splitlines()
                if lines:
                    last_line = lines[-1].strip()
                    if re.match(r'^[a-zA-Z0-9_\-./]+\.[a-zA-Z0-9]+$', last_line):
                        file_path = last_line

            if not file_path:
                continue

            is_new_file = len(search_content) == 0
            is_deletion = len(replace_content) == 0 and len(search_content) > 0

            blocks.append(EditBlock(
                file_path=file_path,
                search_content=search_content,
                replace_content=replace_content,
                is_new_file=is_new_file,
                is_deletion=is_deletion
            ))

        return blocks
