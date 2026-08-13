import re
from typing import List, Tuple
from kitt.domain.entities import Constraint, ConstraintKind

FILE_EXTENSIONS = r'(?:py|html|css|js|ts|tsx|jsx|json|md|txt|toml|yaml|yml|sh|rs|go|c|cpp|h|hpp|java|sql|xml|env|ini|cfg)'
PATH_PATTERN = re.compile(r'\b(?:[a-zA-Z0-9_\-\.]+/)*[a-zA-Z0-9_\-]+\.' + FILE_EXTENSIONS + r'\b', re.IGNORECASE)
SYMBOL_PATTERN = re.compile(r'\b[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*\b')
QUOTED_PATTERN = re.compile(r'["\']([^"\']+)["\"]')
NEGATIVE_SPAN_PATTERN = re.compile(r'\b(?:don\'t|do not|never|sem|sem usar|without|no|nao|não)\b\s+[^.,;\n]+', re.IGNORECASE)
COMMAND_PATTERN = re.compile(r'/(?:add|drop|files|memory|remember|repomap|model|router|diff|commit|undo|run|ask|code|clear|help|skills|setup-skills)\b')

class DeterministicExtractor:
    """Extracts explicit file paths, code symbols, negative constraints, and spans from user prompts."""

    def extract_paths(self, text: str) -> List[str]:
        matches = PATH_PATTERN.findall(text)
        return list(dict.fromkeys(matches))

    def extract_symbols(self, text: str) -> List[str]:
        words = SYMBOL_PATTERN.findall(text)
        keywords = {
            "the", "and", "for", "with", "this", "that", "from", "import", "code", "file", "path", "test",
            "crie", "create", "make", "build", "add", "update", "delete", "remove", "change", "fix", "show", "get", "set"
        }
        symbols = [w for w in words if w.lower() not in keywords and ("_" in w or (w[0].isupper() and len(w) > 1))]
        return list(dict.fromkeys(symbols))

    def extract_quoted_terms(self, text: str) -> List[str]:
        return list(dict.fromkeys(QUOTED_PATTERN.findall(text)))

    def extract_constraints(self, text: str) -> List[Constraint]:
        constraints: List[Constraint] = []
        for match in NEGATIVE_SPAN_PATTERN.finditer(text):
            span_text = match.group(0).strip()
            constraints.append(
                Constraint(
                    text=span_text,
                    kind='NEGATIVE',
                    source_start=match.start(),
                    source_end=match.end(),
                    mandatory=True
                )
            )
        return constraints

    def is_trivial_prompt(self, text: str) -> bool:
        clean = text.strip()
        if len(clean) > 150 or len(clean.split()) > 25:
            return False
        return clean.lower() in {"oi", "olá", "ola", "hello", "hi"} or clean.startswith("/")
