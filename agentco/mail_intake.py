"""AdSpot mailbox intake (Customer Desk). READ, LABEL and DRAFT only: nothing here can send mail.
Mail content is UNTRUSTED data: it is wrapped, classified into a strict schema, and can trigger no action.
The model's output is validated with extra='forbid'; anything else is discarded as 'unclassified'."""
from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import bookings
from .agents import budget
from .connectors import gmail
from .connectors.gmail import REF_RE
from .llm import router
from .llm.base import LLMError
from .models import DataClass
from .notify import post_message, send_push
from .scope import CompanyScope
from .security.untrusted import SYSTEM_RULE, wrap_untrusted

log = logging.getLogger("agentco.mail")
CATEGORIES = ("booking_enquiry", "payment_proof", "artwork_or_ad_copy", "publication_reply", "complaint", "spam", "other")
DRAFT_FOR = {"booking_enquiry", "complaint", "other"}


class MailTriage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["booking_enquiry", "payment_proof", "artwork_or_ad_copy", "publication_reply", "complaint", "spam", "other"]
    summary: str = Field(max_length=400)
    booking_ref: str | None = Field(default=None, max_length=60)
    amount: float | None = None


TRIAGE_SYSTEM = (
    "You are a mail triage assistant for a newspaper-ad booking company. Read the email and return JSON only with keys: "
    "category (booking_enquiry|payment_proof|artwork_or_ad_copy|publication_reply|complaint|spam|other), "
    "summary (max 2 short sentences), booking_ref (a reference like BOOK-20260101-001 or ADM-2026-1001 if present, else null), "
    "amount (a payment amount if stated, else null). " + SYSTEM_RULE)
REPLY_SYSTEM = (
    "Write a short, polite DRAFT reply (plain text, max 120 words) for a human to review. Do not promise prices, "
    "dates or refunds. Do not include links or contact details. " + SYSTEM_RULE)


def _classify(scope: CompanyScope, agent: dict, msg: dict) -> MailTriage:
    wrapped = wrap_untrusted(f"From: {msg['from_addr']}\nSubject: {msg['subject']}\n\n{msg['body']}", "gmail")
    res = router.complete(scope=scope, agent_id=agent["id"], run_id=None, task="mail_triage",
                          data_class=DataClass.CONFIDENTIAL, system=TRIAGE_SYSTEM, prompt=wrapped,
                          tier=agent["model_tier"], json_mode=True)
    try:
        return MailTriage.model_validate_json(res.text)
    except (ValidationError, ValueError):
        return MailTriage(category="other", summary="Could not classify this mail automatically. Please read it.")


def _link(scope: CompanyScope, msg: dict, t: MailTriage) -> tuple[str | None, str | None]:
    """(booking_ref, confidence). Linking is metadata only; it never triggers an action."""
    m = REF_RE.search(f"{msg['subject']} {msg['body']}") or (REF_RE.search(t.booking_ref or "") if t.booking_ref else None)
    if m:
        b = bookings.get(scope, m.group(1)) or (bookings.refresh_one(scope, m.group(1)) and bookings.get(scope, m.group(1)))
        if b:
            same = (b["customer_email"] or "").lower() == msg["from_addr"]
            return b["booking_ref"], "verified" if same else "unverified"
    b = scope.conn.execute("SELECT booking_ref FROM bookings WHERE company_id=%s AND lower(customer_email)=%s "
                           "ORDER BY created_at DESC LIMIT 1", (scope.company_id, msg["from_addr"])).fetchone()
    return (b["booking_ref"], "verified") if b else (None, None)


def poll(scope: CompanyScope, agent: dict, run_id: int) -> str:
    if not gmail.configured():
        return "Gmail is not configured; skipped mailbox."
    client = gmail.get_client()
    seen = 0
    for stub in client.list_inbox(25):
        if scope.conn.execute("SELECT 1 FROM mail_log WHERE company_id=%s AND direction='in' AND gmail_message_id=%s",
                              (scope.company_id, stub["id"])).fetchone():
            continue
        try:
            budget.ensure(scope, agent)
        except budget.BudgetExceeded:
            break                                          # remaining mail is picked up after the budget is raised
        msg = client.get_message(stub["id"])
        try:
            t = _classify(scope, agent, msg)
        except LLMError:
            t = MailTriage(category="other", summary="Model unavailable; please read this mail yourself.")
        if scope.conn.execute("SELECT 1 FROM publications WHERE company_id=%s AND lower(email)=%s AND email<>''",
                              (scope.company_id, msg["from_addr"])).fetchone():
            t.category = "publication_reply"               # decided by sender address (our table), not by the mail text
        ref, conf = _link(scope, msg, t)
        draft_id = None
        if t.category in DRAFT_FOR:
            try:
                r = router.complete(scope=scope, agent_id=agent["id"], run_id=None, task="mail_reply_draft",
                                    data_class=DataClass.CONFIDENTIAL, system=REPLY_SYSTEM, tier="flash",
                                    prompt=wrap_untrusted(f"Subject: {msg['subject']}\n\n{msg['body']}", "gmail"))
                draft_id = client.create_draft(to=msg["from_addr"], subject=f"Re: {msg['subject']}", body=r.text, thread_id=msg["thread_id"])
            except Exception as e:
                log.warning("reply draft failed: %s", type(e).__name__)
        scope.conn.execute(
            "INSERT INTO mail_log (company_id, direction, gmail_message_id, thread_id, rfc_message_id, from_addr, subject, "
            "category, summary, booking_ref, link_confidence, draft_id) VALUES (%s,'in',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT DO NOTHING", (scope.company_id, msg["id"], msg["thread_id"], msg["rfc_message_id"], msg["from_addr"],
                                       msg["subject"][:300], t.category, t.summary, ref, conf, draft_id))
        try:
            client.add_label(msg["id"], f"{get_label()}/{t.category}")
        except Exception:
            pass
        seen += 1
        if t.category != "spam":
            link = f" [{ref}{'' if conf == 'verified' else ', sender unverified'}]" if ref else ""
            post_message("adspot", "system", f"New mail ({t.category.replace('_', ' ')}) from {msg['from_addr']}{link}: {t.summary}"
                         + (" A reply draft is waiting in Gmail." if draft_id else ""))
            send_push("New AdSpot mail", f"{t.category.replace('_', ' ')}: {t.summary}"[:140])
    return f"Mailbox checked: {seen} new mail(s)."


def get_label() -> str:
    from .config import get_settings
    return get_settings().gmail_label


def run(scope: CompanyScope, agent: dict, run_id: int) -> str:
    n = bookings.sync_all(scope)
    return f"Synced {n} booking(s). " + poll(scope, agent, run_id)
