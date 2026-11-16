"""Tool registry: the ONLY path from an agent to any side effect.

Checks, in order, every call (denials are audited too):
 1. tool exists
 2. tool is in the agent's DEPARTMENT allowlist (code-defined, not data-driven)
 3. tool is allowed for this company (data scope)
 4. tier rule:  READ/DRAFT run; WRITE_LOW disabled; HIGH/OWNER_COMMAND only create an approval.
Handlers for HIGH tools are never invoked from `call()`. They run only from `execute_approved()`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..connectors import adspot, sidadiya
from ..models import Tier
from ..scope import CompanyScope


class ToolDenied(Exception):
    pass


@dataclass(frozen=True)
class Tool:
    name: str
    tier: Tier
    handler: Callable[[CompanyScope, dict, dict], Any]   # (scope, args, ctx) -> result
    companies: frozenset[str] | None = None              # None = any company
    describe: Callable[[dict], str] | None = None        # human summary for approval cards
    needs_booking_and_amount: bool = False               # OWNER_COMMAND binding


def _not_built(phase: str):
    def h(scope, args, ctx):
        raise NotImplementedError(f"not built until {phase}")
    return h


def _save_note(scope: CompanyScope, args: dict, ctx: dict):
    return {"note_id": scope.add_note(agent_id=ctx.get("agent_id"), kind=str(args.get("kind", "note"))[:40],
                                      title=str(args.get("title", ""))[:200], body=str(args.get("body", ""))[:20000],
                                      data=args.get("data") or {})}


def _save_pack(scope: CompanyScope, args: dict, ctx: dict):
    """DRAFT only: stores text/image refs on an existing pack. Nothing is ever published by AgentCo."""
    fields = {k: v for k, v in args.items() if k in {"status", "copy", "hashtags", "image_brief", "image_prompt",
                                                     "aspect_ratios", "image_file", "ad_setup", "claims_check", "error"}}
    if fields.get("status") not in (None, "draft", "needs_review", "failed"):
        raise ValueError("agents may only produce drafts")
    scope.update_pack(int(args["pack_id"]), run_id=ctx.get("run_id"), **fields)
    return {"pack_id": int(args["pack_id"])}


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("adspot_report_snapshot", Tier.READ, lambda s, a, c: adspot.snapshot(), frozenset({"adspot"})),
    Tool("sidadiya_report_snapshot", Tier.READ, lambda s, a, c: sidadiya.snapshot(), frozenset({"sidadiya"})),
    Tool("save_note", Tier.DRAFT, _save_note),
    # Phase 2 stubs (mail read/draft). Registered so allowlists can be tested now.
    Tool("read_mail", Tier.READ, _not_built("Phase 2"), frozenset({"adspot"})),
    Tool("create_mail_draft", Tier.DRAFT, _not_built("Phase 2"), frozenset({"adspot"})),
    # HIGH-risk stubs: creating an approval is all that can happen until the executors exist.
    Tool("send_publication_mail", Tier.HIGH, _not_built("Phase 2"), frozenset({"adspot"}),
         describe=lambda a: f"Send mail to publication for booking {a.get('booking_ref', '?')}"),
    Tool("save_content_pack", Tier.DRAFT, _save_pack),
    Tool("send_customer_mail", Tier.OWNER_COMMAND, _not_built("Phase 2"), frozenset({"adspot"}),
         describe=lambda a: f"Send payment confirmation for {a.get('booking_ref', '?')}", needs_booking_and_amount=True),
]}

# Department allowlists. NOTE: customer_desk has no send tool of any kind.
DEPT_TOOLS: dict[str, frozenset[str]] = {
    "command": frozenset({"save_note"}),
    "operations": frozenset({"adspot_report_snapshot", "save_note", "send_publication_mail"}),
    "insights": frozenset({"sidadiya_report_snapshot", "save_note"}),
    "customer_desk": frozenset({"read_mail", "create_mail_draft", "save_note"}),
    "marketing": frozenset({"save_note", "save_content_pack"}),
    "finance": frozenset({"save_note"}),
    "engineering": frozenset({"save_note"}),
}


def call(scope: CompanyScope, agent: dict, run_id: int | None, name: str, args: dict) -> dict:
    """Execute (or gate) one tool call. Returns {"status": ..., ...}. Never raises for policy denials."""
    args = args if isinstance(args, dict) else {}

    def deny(reason: str, tier: str | None = None) -> dict:
        scope.log_tool_call(run_id=run_id, agent_id=agent["id"], tool=name, tier=tier, args=args,
                            status="denied", reason=reason)
        return {"status": "denied", "reason": reason}

    tool = TOOLS.get(name)
    if tool is None:
        return deny("unknown tool")
    if name not in DEPT_TOOLS.get(agent["dept"], frozenset()):
        return deny(f"tool not in {agent['dept']} allowlist", tool.tier)
    if tool.companies is not None and scope.slug not in tool.companies:
        return deny(f"tool not available to company {scope.slug}", tool.tier)

    ctx = {"agent_id": agent["id"], "run_id": run_id}
    if tool.tier in (Tier.READ, Tier.DRAFT):
        try:
            result = tool.handler(scope, args, ctx)
        except Exception as e:
            scope.log_tool_call(run_id=run_id, agent_id=agent["id"], tool=name, tier=tool.tier, args=args,
                                status="error", reason=f"{type(e).__name__}: {e}"[:300])
            return {"status": "error", "reason": type(e).__name__}
        scope.log_tool_call(run_id=run_id, agent_id=agent["id"], tool=name, tier=tool.tier, args=args,
                            status="ok", result=result if isinstance(result, dict) else {"value": result})
        return {"status": "ok", "result": result}

    if tool.tier == Tier.WRITE_LOW:
        return deny("WRITE_LOW tools are disabled until the agent has a clean track record", tool.tier)

    # HIGH / OWNER_COMMAND: never execute here, only request approval.
    booking_ref = args.get("booking_ref")
    amount = args.get("amount")
    if tool.needs_booking_and_amount and (not booking_ref or amount is None):
        return deny("OWNER_COMMAND tools must be bound to a booking_ref and an amount", tool.tier)
    summary = tool.describe(args) if tool.describe else f"Run {name}"
    approval_id, created = scope.create_approval(
        tool=name, tier=tool.tier, summary=summary, payload=args, agent_id=agent["id"], run_id=run_id,
        booking_ref=str(booking_ref) if booking_ref else None,
        amount=float(amount) if amount is not None else None)
    scope.log_tool_call(run_id=run_id, agent_id=agent["id"], tool=name, tier=tool.tier, args=args,
                        status="pending_approval", result={"approval_id": approval_id})
    if created:
        scope.emit("approval.requested", {"approval_id": approval_id, "tool": name}, agent["dept"])
    return {"status": "pending_approval", "approval_id": approval_id}


def execute_approved(scope: CompanyScope, approval_id: int) -> dict:
    """Run a HIGH/OWNER_COMMAND handler. Refuses unless the approval row is 'approved'."""
    ap = scope.get_approval(approval_id)
    if ap is None:
        raise ToolDenied("no such approval")
    if ap["status"] != "approved":
        raise ToolDenied(f"approval is {ap['status']}, not approved")
    tool = TOOLS[ap["tool"]]
    if tool.companies is not None and scope.slug not in tool.companies:
        raise ToolDenied("tool not available to this company")
    result = tool.handler(scope, ap["payload"], {"approval_id": approval_id})
    scope.conn.execute("UPDATE approvals SET status='executed' WHERE id=%s AND company_id=%s",
                       (approval_id, scope.company_id))
    return {"status": "executed", "result": result}
