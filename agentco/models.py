"""Typed contracts: data classes, tool tiers, events, chat intents."""
from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class DataClass(StrEnum):
    PUBLIC = "PUBLIC"                # brand voice, public product info, offers, post topics
    INTERNAL = "INTERNAL"            # revenue, margins, publication rates
    CONFIDENTIAL = "CONFIDENTIAL"    # customer PII, receipts, mail content, payment data


class Tier(StrEnum):
    READ = "READ"                    # auto
    DRAFT = "DRAFT"                  # auto, saved as draft
    WRITE_LOW = "WRITE_LOW"          # disabled until an agent has a clean track record
    HIGH = "HIGH"                    # always needs an approval
    OWNER_COMMAND = "OWNER_COMMAND"  # owner's explicit chat command, bound to booking + amount


# ---- Orchestrator events: departments never call each other, they exchange these. ----
class _Ev(BaseModel):
    model_config = {"extra": "forbid"}


class ReportReady(_Ev):
    note_id: int
    kind: str = "daily_report"


class ApprovalRequested(_Ev):
    approval_id: int
    tool: str


class AgentBudgetExhausted(_Ev):
    agent_id: int
    spent_usd: float
    budget_usd: float


class PaymentReceived(_Ev):            # Phase 2 producers
    booking_ref: str
    amount: float


class BookingReadyForPublication(_Ev):  # Phase 2
    booking_ref: str


class ReceiptReceived(_Ev):            # Phase 2
    booking_ref: str


class ContentDrafted(_Ev):
    pack_id: int


class VideoScriptReady(_Ev):
    project_id: int


class VideoRendered(_Ev):
    project_id: int


class VideoUploaded(_Ev):
    project_id: int
    video_id: str


EVENT_TYPES: dict[str, type[_Ev]] = {
    "report.ready": ReportReady,
    "approval.requested": ApprovalRequested,
    "agent.budget_exhausted": AgentBudgetExhausted,
    "payment.received": PaymentReceived,
    "booking.ready_for_publication": BookingReadyForPublication,
    "receipt.received": ReceiptReceived,
    "content.drafted": ContentDrafted,
    "video.script_ready": VideoScriptReady,
    "video.rendered": VideoRendered,
    "video.uploaded": VideoUploaded,
}


# ---- Command agent: the LLM may only emit one of these validated intents. ----
class Intent(BaseModel):
    kind: Literal[
        "status", "show_report", "list_approvals", "pause_agent", "resume_agent",
        "money_action", "create_post", "create_ad", "create_short", "mark_paid", "send_draft", "link_receipt", "unknown",
    ] = "unknown"
    company: Literal["adspot", "sidadiya", "all"] | None = None
    agent: str | None = Field(default=None, max_length=80)
    booking_ref: str | None = Field(default=None, max_length=60)
    amount: float | None = None
    topic: str | None = Field(default=None, max_length=300)


class Step(BaseModel):
    """One model turn inside the agent loop."""
    tool: str | None = None
    args: dict = Field(default_factory=dict)
    final: str | None = None
