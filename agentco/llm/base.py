from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    route: str
    tokens_in: int
    tokens_out: int
    cost_usd: float


class Provider(Protocol):
    name: str

    def complete(self, *, system: str, prompt: str, tier: str, json_mode: bool) -> LLMResult: ...


class LLMError(RuntimeError):
    pass


class PolicyViolation(LLMError):
    """A data-class routing rule was broken (e.g. CONFIDENTIAL data to a non-Gemini provider)."""
