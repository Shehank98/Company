"""Hard monthly budget per agent. At/over budget the agent is set to 'budget_exhausted' and stops."""
from __future__ import annotations

from ..scope import CompanyScope


class BudgetExceeded(Exception):
    pass


def ensure(scope: CompanyScope, agent: dict) -> None:
    fresh = scope.get_agent(agent["id"])
    if fresh["status"] == "budget_exhausted":
        raise BudgetExceeded("agent budget exhausted")
    spent = scope.month_spend(agent["id"])
    budget = float(fresh["monthly_budget_usd"])
    if spent >= budget:
        scope.set_agent_status(agent["id"], "budget_exhausted")
        scope.emit("agent.budget_exhausted",
                   {"agent_id": agent["id"], "spent_usd": round(spent, 4), "budget_usd": budget}, agent["dept"])
        raise BudgetExceeded(f"spent ${spent:.4f} of ${budget:.2f}")
