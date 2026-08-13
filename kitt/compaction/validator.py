from typing import Iterable, Tuple
class CompactionValidator:
    def validate(self, summary: str, mandatory_facts: Iterable[str]) -> Tuple[bool,dict]:
        missing=[fact for fact in mandatory_facts if fact and fact.lower() not in summary.lower()]
        return not missing, {"missing_facts":missing,"non_empty":bool(summary.strip())}
