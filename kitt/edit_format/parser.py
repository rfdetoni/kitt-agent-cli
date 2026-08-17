import re
from typing import List
from kitt.domain.entities import EditBlock

class SearchReplaceParser:
    """Parser for SEARCH/REPLACE diff blocks emitted by the LLM."""

    BLOCK_REGEX = re.compile(
        r'(?:([a-zA-Z0-9_\-./]+\.[a-zA-Z0-9]+)\s*\n)?<<<<<<< SEARCH\r?\n([\s\S]*?)(?:\r?\n)?=======\r?\n([\s\S]*?)\r?\n>>>>>>> REPLACE',
        re.MULTILINE
    )

    FILENAME_EXTRACTOR = re.compile(r'([a-zA-Z0-9_\-./]+\.[a-zA-Z0-9]+)')

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
                    for line in reversed(lines[-3:]):
                        line_clean = line.strip(":`'\"#* ")
                        found = self.FILENAME_EXTRACTOR.findall(line_clean)
                        if found:
                            file_path = found[-1]
                            break

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

        if not blocks and text.strip():
            clean = text.strip()
            first_line = clean.splitlines()[0].strip(":`'\"#* ")
            m_file = self.FILENAME_EXTRACTOR.search(first_line)
            if m_file and len(clean.splitlines()) > 1:
                target_file = m_file.group(1)
                body = "\n".join(clean.splitlines()[1:]).strip()
                code_m = re.search(r'```(?:[a-zA-Z0-9_\-]+)?\s*\n([\s\S]*?)\n```', body)
                replace_content = code_m.group(1) if code_m else body
                if replace_content and (code_m or target_file.endswith(('.html', '.css', '.js', '.ts', '.tsx', '.jsx', '.py', '.json', '.md', '.txt', '.sh'))):
                    blocks.append(EditBlock(
                        file_path=target_file,
                        search_content="",
                        replace_content=replace_content,
                        is_new_file=False,
                        is_deletion=False
                    ))

        return blocks
