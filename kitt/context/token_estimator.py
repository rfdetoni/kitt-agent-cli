"""Calibrated multi-level token estimation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Dict, Any, Protocol, Optional
from kitt.router.models import ModelCapabilities


@dataclass(frozen=True)
class TokenEstimate:
    count: int
    method: str  # provider|calibrated_lang|char_fallback
    error_margin: float
    estimator_version: str = "calibrated-v1"


class TokenEstimator(Protocol):
    def count_text(self, text: str, profile: Optional[ModelCapabilities] = None) -> TokenEstimate: ...
    def count_messages(self, messages: Sequence[Dict[str, Any]], profile: Optional[ModelCapabilities] = None) -> TokenEstimate: ...


class CalibratedTokenEstimator:
    """Calibrated token estimator with provider fallback and language multipliers."""

    version = "calibrated-v1"

    def __init__(self):
        # Character-per-token multipliers for code and prose
        self.char_ratios = {
            "python": 3.8,
            "json": 3.5,
            "markdown": 4.0,
            "default": 3.9
        }

    def count_text(
        self,
        text: str,
        profile: Optional[ModelCapabilities] = None,
        language: Optional[str] = None,
    ) -> TokenEstimate:
        if not text:
            return TokenEstimate(count=0, method="char_fallback", error_margin=0.0, estimator_version=self.version)

        lang_key = (language or "").lower().lstrip(".")
        if lang_key in ("py", "pyw", "python"):
            ratio = self.char_ratios["python"]
            method = "calibrated_lang"
        elif lang_key in ("json", "json5", "jsonc"):
            ratio = self.char_ratios["json"]
            method = "calibrated_lang"
        elif lang_key in ("md", "markdown", "rst"):
            ratio = self.char_ratios["markdown"]
            method = "calibrated_lang"
        elif lang_key in self.char_ratios:
            ratio = self.char_ratios[lang_key]
            method = "calibrated_lang"
        else:
            ratio = self.char_ratios["default"]
            method = "calibrated_lang" if not language else "char_fallback"

        margin = 0.08
        tokens = max(1, int(len(text) / ratio))
        return TokenEstimate(count=tokens, method=method, error_margin=margin, estimator_version=self.version)

    def count_messages(self, messages: Sequence[Dict[str, Any]], profile: Optional[ModelCapabilities] = None) -> TokenEstimate:
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            total += self.count_text(content, profile).count + 4
        return TokenEstimate(count=total, method="calibrated_lang", error_margin=0.08, estimator_version=self.version)
