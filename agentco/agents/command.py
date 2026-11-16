"""Command agent (chief of staff). The model's ONLY output is a validated Intent; deterministic code
executes it. Money-affecting intents are recognised but NOT executable in Phase 1."""
from __future__ import annotations

from pydantic import ValidationError

from .. import db
from ..llm import router
from ..llm.base import LLMError
from ..models import DataClass, Intent
from ..scope import scope_by_id
from . import budget

INTENT_SYSTEM = (
    "You convert an owner's chat message into an intent. Return JSON only with keys: "
    "kind (status|show_report|list_approvals|pause_agent|resume_agent|create_post|create_ad|money_action|unknown), "
    "company (adspot|sidadiya|all|null), agent (agent name or null), booking_ref (e.g. BOOK-20260101-123 "
    "or ADM-2026-1234, or null), amount (number or null), topic (what the post/ad is about, or null). Use create_post to draft a social post and create_ad to draft an Ads Manager setup sheet. Use money_action for anything about marking "
    "paid, payments, payouts, sending mail or posting. Never invent values."
)

HELP = ("I can: show status, show today's report, list approvals, pause/resume an agent, and draft posts/ads. "
        "Try: \"status\", \"approvals\", \"pause AdSpot Ops\", \"post for AdSpot about booking classified ads online\".")


def _companies(choice: str) -> list[dict]:
    with db.admin() as a:
        rows = a.execute("SELECT id, slug, name FROM companies ORDER BY id").fetchall()
    return rows if choice == "all" else [r for r in rows if r["slug"] == choice]


def parse_intent(text: str, scope_choice: str) -> Intent:
    cos = _companies(scope_choice) or _companies("all")
    co = cos[0]  # ponytail: chat model cost is charged to the first in-scope company's Command agent
    with scope_by_id(co["id"], co["slug"]) as s:
        cmd = next(a for a in s.list_agents() if a["role"] == "command")
        try:
            budget.ensure(s, cmd)
        except budget.BudgetExceeded:
            return Intent(kind="unknown")
        try:
            res = router.complete(scope=s, agent_id=cmd["id"], run_id=None, task="intent",
                                  data_class=DataClass.CONFIDENTIAL,  # chat may name customers/bookings
                                  system=INTENT_SYSTEM, prompt=text[:2000], tier="flash", json_mode=True)
            return Intent.model_validate_json(res.text)
        except (ValidationError, ValueError, LLMError):
            return Intent(kind="unknown")


def handle(text: str, scope_choice: str) -> str:
    intent = parse_intent(text, scope_choice)
    choice = intent.company or scope_choice
    cos = _companies(choice) or _companies("all")

    if intent.kind == "money_action":
        return ("That would move money or send mail to a customer. Phase 1 cannot do that. "
                "Nothing was changed. (The booking/amount confirm card arrives in Phase 2.)")
    if intent.kind in ("create_post", "create_ad"):
        from .. import content
        if intent.company not in ("adspot", "sidadiya") and scope_choice not in ("adspot", "sidadiya"):
            return "Which company is this for, AdSpot or Sidadiya?"
        slug = intent.company if intent.company in ("adspot", "sidadiya") else scope_choice
        if not intent.topic:
            return "What should it be about? e.g. \"post for AdSpot about booking classified ads online\"."
        try:
            pid = content.request_pack(slug, "post" if intent.kind == "create_post" else "ad", intent.topic)
        except (ValueError, LookupError) as e:
            return f"I can't draft that: {e}"
        return f"Drafting {'post' if intent.kind == 'create_post' else 'ad'} #{pid} for {slug.title()}. It will appear in the Posts tab in a minute. Nothing is published anywhere."
    if intent.kind == "status":
        lines = []
        for co in cos:
            with scope_by_id(co["id"], co["slug"]) as s:
                pend = len(s.list_approvals("pending"))
                lines.append(f"**{co['name']}** - {pend} approval(s) waiting")
                for a in s.list_agents():
                    lines.append(f"- {a['name']}: {a['status']} (${s.month_spend(a['id']):.2f} / ${float(a['monthly_budget_usd']):.2f})")
        return "\n".join(lines)
    if intent.kind == "show_report":
        out = []
        for co in cos:
            with scope_by_id(co["id"], co["slug"]) as s:
                n = s.latest_note("daily_report")
            out.append(f"**{co['name']}**\n{n['body']}" if n else f"**{co['name']}**: no report yet.")
        return "\n\n".join(out)
    if intent.kind == "list_approvals":
        out = []
        for co in cos:
            with scope_by_id(co["id"], co["slug"]) as s:
                for ap in s.list_approvals("pending"):
                    out.append(f"#{ap['id']} [{co['name']}] {ap['summary']}")
        return "\n".join(out) or "No approvals waiting."
    if intent.kind in ("pause_agent", "resume_agent"):
        if not intent.agent:
            return "Which agent? e.g. \"pause AdSpot Ops\"."
        matches = []
        for co in cos:
            with scope_by_id(co["id"], co["slug"]) as s:
                for a in s.list_agents():
                    if intent.agent.lower() in a["name"].lower():
                        matches.append((co, a))
        if len(matches) != 1:
            return f"I found {len(matches)} agents matching \"{intent.agent}\". Please be more specific (name + company)."
        co, a = matches[0]
        with scope_by_id(co["id"], co["slug"]) as s:
            if intent.kind == "pause_agent":
                s.set_agent_status(a["id"], "paused")
                return f"Paused {a['name']} ({co['name']})."
            if a["status"] == "budget_exhausted":
                return f"{a['name']} hit its monthly budget. Raise the budget on the Departments page first."
            s.set_agent_status(a["id"], "active")
            return f"Resumed {a['name']} ({co['name']})."
    return HELP
