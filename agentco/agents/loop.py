"""The agent loop: context (one company) -> budget check -> model -> tool via registry -> cost -> repeat."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from pydantic import ValidationError

from ..llm import router
from ..models import DataClass, Step
from ..scope import CompanyScope
from ..security.untrusted import SYSTEM_RULE, wrap_untrusted
from ..tools import registry
from . import budget

PROTOCOL = (
    'Reply with JSON only. Either {"tool": "<name>", "args": {...}} to call ONE allowed tool, '
    'or {"final": "<your answer>"} when done. Only use tools from the allowed list.'
)


@dataclass
class LoopResult:
    final: str
    tool_results: list[dict] = field(default_factory=list)


def run_loop(scope: CompanyScope, agent: dict, run_id: int, *, task: str, instructions: str,
             data_class: DataClass = DataClass.INTERNAL, untrusted_input: str | None = None,
             untrusted_source: str = "external", initial_calls: list[tuple[str, dict]] | None = None,
             max_steps: int = 4) -> LoopResult:
    allowed = sorted(registry.DEPT_TOOLS.get(agent["dept"], ()))
    system = f"{instructions}\n{SYSTEM_RULE}\n{PROTOCOL}\nAllowed tools: {allowed}"
    results: list[dict] = []
    for name, args in (initial_calls or []):
        r = registry.call(scope, agent, run_id, name, args)
        results.append({"tool": name, **r})
        if r["status"] != "ok":
            return LoopResult(f"Required tool {name} failed: {r.get('reason', r['status'])}", results)

    last = ""
    for _ in range(max_steps):
        budget.ensure(scope, agent)                      # stop BEFORE spending more
        prompt = json.dumps({"tool_results": results}, default=str)
        if untrusted_input:
            prompt += "\n" + wrap_untrusted(untrusted_input, untrusted_source)
        res = router.complete(scope=scope, agent_id=agent["id"], run_id=run_id, task=task,
                              data_class=data_class, system=system, prompt=prompt,
                              tier=agent["model_tier"], json_mode=True)
        last = res.text
        try:
            step = Step.model_validate_json(res.text)
        except (ValidationError, ValueError):
            return LoopResult(res.text, results)         # plain text answer
        if step.tool:
            r = registry.call(scope, agent, run_id, step.tool, step.args)
            results.append({"tool": step.tool, **r})
            continue
        return LoopResult(step.final or "", results)
    return LoopResult(last, results)
