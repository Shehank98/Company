import json

import pytest

from agentco import db, worker
from agentco.agents import budget
from agentco.agents.loop import run_loop
from agentco.llm import fake
from agentco.orchestrator import queue
from agentco.scope import company_scope
from conftest import agent, co_id


def _set_budget(slug, name, usd):
    a = agent(slug, name)
    with company_scope(slug) as s:
        s.set_agent_budget(a["id"], usd)
    return a


def _enqueue(slug, a, key):
    with db.admin() as c:
        queue.enqueue(c, company_id=co_id(slug), agent_id=a["id"], kind="daily_report", dedupe_key=key)


def test_budget_exhaustion_stops_the_agent():
    a = _set_budget("adspot", "AdSpot Ops", 0.001)     # fake model costs 0.001 per call
    _enqueue("adspot", a, "k1")
    assert worker.work_once()                          # first run fits the budget and spends it
    _enqueue("adspot", a, "k2")
    assert worker.work_once()                          # second run must be stopped
    with company_scope("adspot") as s:
        assert s.get_agent(a["id"])["status"] == "budget_exhausted"
        assert s.conn.execute("SELECT COUNT(*) AS n FROM cost_events").fetchone()["n"] == 1   # no new spend
        assert s.conn.execute("SELECT status FROM runs ORDER BY id DESC LIMIT 1").fetchone()["status"] == "budget_stopped"
        assert any(e["type"] == "agent.budget_exhausted" for e in s.list_events())
    _enqueue("adspot", a, "k3")
    worker.work_once()                                 # exhausted agent stays stopped
    with company_scope("adspot") as s:
        assert s.conn.execute("SELECT COUNT(*) AS n FROM cost_events").fetchone()["n"] == 1


def test_loop_stops_mid_run_when_budget_hit():
    a = _set_budget("adspot", "AdSpot Ops", 0.0025)
    fake.set_handler(lambda system, prompt: json.dumps({"tool": "save_note", "args": {"title": "x", "body": "y"}}))
    with company_scope("adspot") as s:
        run_id = s.start_run(a["id"])
        with pytest.raises(budget.BudgetExceeded):
            run_loop(s, a, run_id, task="t", instructions="i", max_steps=50)
        n = s.conn.execute("SELECT COUNT(*) AS n FROM cost_events").fetchone()["n"]
        assert n == 3            # 0.003 >= 0.0025, never 50 calls
        assert s.get_agent(a["id"])["status"] == "budget_exhausted"
