"""llm.complete(): the single entry point for model calls.

Data-class rules (code-enforced, not prompt-enforced):
  INTERNAL / CONFIDENTIAL -> paid Gemini only (the offline fake is allowed for tests).
  PUBLIC                  -> Gemini; OmniRoute only when OMNIROUTE_ENABLED (Phase 3, not built yet).
Every call writes a cost_events row.
"""
from __future__ import annotations

from ..config import get_settings
from ..models import DataClass
from ..scope import CompanyScope
from .base import LLMError, LLMResult, PolicyViolation
from .fake import FakeProvider

ALLOWED: dict[DataClass, set[str]] = {
    DataClass.PUBLIC: {"gemini", "fake", "omniroute"},
    DataClass.INTERNAL: {"gemini", "fake"},
    DataClass.CONFIDENTIAL: {"gemini", "fake"},
}

_providers: dict[str, object] = {}


def get_provider(name: str):
    if name == "omniroute":
        # ponytail: slot reserved; implemented in Phase 3 with the sanitizer + public_facts builder.
        raise LLMError("omniroute provider is not available yet")
    if name not in _providers:
        if name == "gemini":
            from .gemini import GeminiProvider
            _providers[name] = GeminiProvider()
        elif name == "fake":
            _providers[name] = FakeProvider()
        else:
            raise LLMError(f"unknown provider {name!r}")
    return _providers[name]


def complete(*, scope: CompanyScope, agent_id: int | None, run_id: int | None, task: str,
             data_class: DataClass, system: str, prompt: str, tier: str = "flash",
             json_mode: bool = False, provider: str | None = None) -> LLMResult:
    name = provider or get_settings().llm_provider
    if name not in ALLOWED[data_class]:
        raise PolicyViolation(f"{data_class} data may not be sent to provider {name!r}")
    res = get_provider(name).complete(system=system, prompt=prompt, tier=tier, json_mode=json_mode)
    scope.add_cost(agent_id=agent_id, run_id=run_id, provider=res.provider, model=res.model,
                   route=res.route, data_class=data_class, task=task,
                   tokens_in=res.tokens_in, tokens_out=res.tokens_out, cost_usd=res.cost_usd)
    return res
