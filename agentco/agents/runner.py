"""Executes one queued job for one agent inside one company scope."""
from __future__ import annotations

from .. import db
from ..models import DataClass
from ..notify import post_message, send_push
from ..scope import CompanyScope, scope_by_id
from ..tools import registry
from . import budget
from .loop import run_loop

REPORT_INSTRUCTIONS = (
    "You are a chief-of-staff analyst writing a short daily briefing for a very busy business owner "
    "in simple English. Use ONLY the numbers in tool_results. Max 8 bullets. Start with what needs "
    "attention today. Do not invent figures."
)

ROLE_TOOL = {"ops_daily": "adspot_report_snapshot", "insights_daily": "sidadiya_report_snapshot"}


def _daily(scope: CompanyScope, agent: dict, run_id: int) -> str:
    tool = ROLE_TOOL[agent["role"]]
    out = run_loop(scope, agent, run_id, task="daily_report", instructions=REPORT_INSTRUCTIONS,
                   initial_calls=[(tool, {})], max_steps=1, data_class=DataClass.INTERNAL)
    if out.tool_results and out.tool_results[0]["status"] != "ok":
        raise RuntimeError(out.final)
    snap = out.tool_results[0]["result"]
    r = registry.call(scope, agent, run_id, "save_note", {
        "kind": "daily_report", "title": f"{agent['name']} - daily report", "body": out.final, "data": snap})
    note_id = r["result"]["note_id"]
    scope.emit("report.ready", {"note_id": note_id}, agent["dept"])
    return out.final


def db_company_name(company_id: int) -> str:
    with db.admin() as a:
        return a.execute("SELECT name FROM companies WHERE id=%s", (company_id,)).fetchone()["name"]


def run_job(job: dict) -> str:
    """Returns 'done' | 'skipped' | 'budget_stopped' | 'failed:<reason>'. Never raises on policy outcomes."""
    with db.admin() as a:
        co = a.execute("SELECT slug FROM companies WHERE id=%s", (job["company_id"],)).fetchone()
    outcome, message = "done", None
    with scope_by_id(job["company_id"], co["slug"]) as scope:
        agent = scope.get_agent(job["agent_id"]) if job["agent_id"] else None
        if agent is None:
            return "failed:no agent"
        task_id = scope.create_task(f"{job['kind']} ({agent['name']})", agent["id"], job["payload"])
        run_id = scope.start_run(agent["id"], task_id)
        if agent["status"] != "active":
            scope.finish_run(run_id, "skipped", f"agent is {agent['status']}")
            scope.update_task(task_id, "blocked")
            return "skipped"
        try:
            budget.ensure(scope, agent)
            scope.update_task(task_id, "running")
            if agent["role"] in ROLE_TOOL:
                message = _daily(scope, agent, run_id)
            elif agent["role"] == "marketing" and job["kind"] == "content_pack":
                from .. import content
                co_name = db_company_name(job["company_id"])
                message = content.build(scope, agent, run_id, int(job["payload"]["pack_id"]), co_name)
            else:
                raise RuntimeError(f"role {agent['role']!r} has no scheduled behaviour")
            scope.finish_run(run_id, "done")
            scope.update_task(task_id, "done")
        except budget.BudgetExceeded as e:
            scope.finish_run(run_id, "budget_stopped", str(e))
            scope.update_task(task_id, "blocked")
            outcome, message = "budget_stopped", f"{agent['name']} stopped: monthly budget reached ({e})."
        except Exception as e:  # keep failure info committed
            scope.finish_run(run_id, "failed", f"{type(e).__name__}: {e}"[:500])
            scope.update_task(task_id, "failed")
            if job["kind"] == "content_pack":
                scope.update_pack(int(job["payload"]["pack_id"]), status="failed", error=f"{type(e).__name__}: {e}"[:300])
            outcome, message = f"failed:{type(e).__name__}", f"{agent['name']} failed: {e}"[:500]
    if message:
        post_message(co["slug"], "system", message)
        if outcome == "done":
            send_push(f"{co['slug'].title()}: draft ready" if job["kind"] == "content_pack"
                      else f"{co['slug'].title()} report ready", message[:140])
    return outcome
