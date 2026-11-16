"""Deterministic provider for tests and offline dev. No network."""
from __future__ import annotations

import json
import re
from typing import Callable

from .base import LLMResult

Handler = Callable[[str, str], str]   # (system, prompt) -> text

_handler: Handler | None = None
cost_per_call = 0.001


def set_handler(h: Handler | None) -> None:
    global _handler
    _handler = h


def _default(system: str, prompt: str) -> str:
    low = prompt.lower()
    if "mail triage" in system.lower():
        b = prompt.lower()
        ref = re.search(r"\b(BOOK-\d{8}-\d{3}|ADM-\d{4}-\d{4})\b", prompt, re.I)
        cat = ("spam" if "lottery" in b else "payment_proof" if ("slip" in b or "paid" in b)
               else "complaint" if "complain" in b else "booking_enquiry" if ("how much" in b or "book" in b) else "other")
        return json.dumps({"category": cat, "summary": "Customer wrote about: " + prompt.split("Subject:")[-1][:60].strip().replace("\n", " "),
                           "booking_ref": ref.group(1) if ref else None, "amount": None})
    if "greeting line" in system.lower():
        n = re.search(r"first name: (.+)", prompt).group(1).strip()
        return json.dumps({"greeting": f"Dear {n},"})
    if "draft reply" in system.lower():
        return "Thank you for your message. We will get back to you shortly."
    if "claims checker" in system.lower():
        return json.dumps({"ok": True, "issues": []})
    if "copywriter" in system.lower():
        topic = re.search(r"topic: (.+)", prompt).group(1)
        ad = "kind: ad" in prompt
        return json.dumps({
            "facebook": f"Fake Facebook post about {topic}", "instagram": f"Fake Instagram caption about {topic}",
            "x": f"Fake X post about {topic}", "linkedin": f"Fake LinkedIn post about {topic}",
            "hashtags": ["#SriLanka"], "image_brief": "A friendly flat illustration.",
            "image_prompt": f"Flat illustration about {topic}, no text",
            "ad_setup": {"objective": "Traffic", "audience": "Sri Lanka, 25-54", "headline": "Book online",
                         "primary_text": topic, "call_to_action": "Learn more", "destination_url": "https://example.com"} if ad else None})
    if "intent" in system.lower():
        m = re.search(r"\b(BOOK-\d{8}-\d{3}|ADM-\d{4}-\d{4})\b", prompt, re.I)
        company = "adspot" if "adspot" in low else "sidadiya" if "sidadiya" in low else "all"
        ref = m.group(1) if m else None
        co = "adspot" if "adspot" in low else "sidadiya" if "sidadiya" in low else None
        stripped = re.sub(r"\b(?:BOOK-\d{8}-\d{3}|ADM-\d{4}-\d{4})\b", "", prompt, flags=re.I)
        am = re.search(r"(?:rs\.?|lkr)\s*([\d,]+(?:\.\d+)?)", stripped, re.I)
        amount = float(am.group(1).replace(",", "")) if am else None
        if "payment received" in low or re.search(r"mark .*paid", low):
            return json.dumps({"kind": "mark_paid", "company": co, "booking_ref": ref, "amount": amount})
        if low.startswith("send"):
            return json.dumps({"kind": "send_draft", "company": co, "booking_ref": ref})
        if "receipt" in low and ref:
            return json.dumps({"kind": "link_receipt", "company": co, "booking_ref": ref})
        if "payout" in low:
            return json.dumps({"kind": "money_action", "company": co})
        tm = re.search(r"\b(?:about|on)\s+(.+)$", prompt, re.I)
        if ("post" in low or "ad " in low or low.startswith("ad")) and "paid" not in low and "approval" not in low:
            kind = "create_ad" if re.search(r"\bad\b", low) and "post" not in low else "create_post"
            return json.dumps({"kind": kind, "company": "adspot" if "adspot" in low else "sidadiya" if "sidadiya" in low else None,
                               "topic": tm.group(1).strip() if tm else None})
        if "paid" in low or "payment received" in low:
            kind = "money_action"
        elif "pause" in low:
            kind = "pause_agent"
        elif "resume" in low:
            kind = "resume_agent"
        elif "approval" in low:
            kind = "list_approvals"
        elif "report" in low or "briefing" in low:
            kind = "show_report"
        elif "status" in low or "how" in low:
            kind = "status"
        else:
            kind = "unknown"
        am = re.search(r"(?:pause|resume)\s+(.+)$", prompt, re.I)
        agent = am.group(1).strip() if am else None
        return json.dumps({"kind": kind, "company": company, "agent": agent,
                           "booking_ref": m.group(1) if m else None})
    return "Daily report (fake model). " + prompt[:400]


class FakeProvider:
    name = "fake"

    def complete(self, *, system: str, prompt: str, tier: str, json_mode: bool) -> LLMResult:
        text = (_handler or _default)(system, prompt)
        tin, tout = max(1, len(prompt) // 4), max(1, len(text) // 4)
        return LLMResult(text, self.name, f"fake-{tier}", "direct", tin, tout, cost_per_call)
