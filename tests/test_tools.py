import dataclasses

import pytest

from agentco.models import Tier
from agentco.scope import company_scope
from agentco.tools import registry
from conftest import agent


def test_department_cannot_call_another_departments_tools():
    ins = agent("sidadiya", "Sidadiya Insights")
    ops = agent("adspot", "AdSpot Ops")
    with company_scope("sidadiya") as s:
        r = registry.call(s, ins, None, "adspot_report_snapshot", {})
        assert r["status"] == "denied"
        assert registry.call(s, ins, None, "send_publication_mail", {})["status"] == "denied"
    with company_scope("adspot") as s:
        assert registry.call(s, ops, None, "sidadiya_report_snapshot", {})["status"] == "denied"
        assert registry.call(s, ops, None, "publish_post", {})["status"] == "denied"   # marketing's tool


def test_company_data_scope_enforced_even_if_dept_matches():
    # an 'operations' agent in Sidadiya may not use the AdSpot-only publication mail tool
    sop = agent("sidadiya", "Sidadiya Ops")
    with company_scope("sidadiya") as s:
        assert registry.call(s, sop, None, "send_publication_mail", {"booking_ref": "X"})["status"] == "denied"


def test_customer_desk_has_no_send_tool():
    assert not [t for t in registry.DEPT_TOOLS["customer_desk"] if "send" in t or "publish" in t]
    desk = agent("adspot", "Customer Desk")
    with company_scope("adspot") as s:
        for t in ("send_customer_mail", "send_publication_mail", "send_mail", "gmail_send"):
            assert registry.call(s, desk, None, t, {"to": "x@evil.com"})["status"] == "denied"
        assert s.list_approvals() == []


def test_high_tier_only_creates_approval_and_never_runs(monkeypatch):
    calls = []
    spy = dataclasses.replace(registry.TOOLS["send_publication_mail"], handler=lambda s, a, c: calls.append(a) or "sent")
    monkeypatch.setitem(registry.TOOLS, "send_publication_mail", spy)
    ops = agent("adspot", "AdSpot Ops")
    with company_scope("adspot") as s:
        r = registry.call(s, ops, None, "send_publication_mail", {"booking_ref": "BOOK-1", "to": "x@evil.com"})
        assert r["status"] == "pending_approval" and calls == []
        again = registry.call(s, ops, None, "send_publication_mail", {"booking_ref": "BOOK-1", "to": "x@evil.com"})
        assert again["approval_id"] == r["approval_id"]                   # idempotent
        with pytest.raises(registry.ToolDenied):
            registry.execute_approved(s, r["approval_id"])                # not approved yet
        assert calls == []
        s.decide_approval(r["approval_id"], "approved")
        assert registry.execute_approved(s, r["approval_id"])["status"] == "executed"
        assert len(calls) == 1
        with pytest.raises(registry.ToolDenied):
            registry.execute_approved(s, r["approval_id"])                # cannot run twice


def test_rejected_approval_cannot_execute():
    mk = agent("sidadiya", "Marketing")
    with company_scope("sidadiya") as s:
        r = registry.call(s, mk, None, "publish_post", {"post_id": "p1"})
        assert r["status"] == "pending_approval"
        s.decide_approval(r["approval_id"], "rejected")
        with pytest.raises(registry.ToolDenied):
            registry.execute_approved(s, r["approval_id"])
        assert s.decide_approval(r["approval_id"], "approved") is None   # cannot flip a decided approval


def test_owner_command_must_bind_booking_and_amount():
    fin = agent("adspot", "Finance")
    assert registry.TOOLS["send_customer_mail"].tier == Tier.OWNER_COMMAND
    registry.DEPT_TOOLS["finance"] = registry.DEPT_TOOLS["finance"] | {"send_customer_mail"}
    try:
        with company_scope("adspot") as s:
            assert registry.call(s, fin, None, "send_customer_mail", {"booking_ref": "B1"})["status"] == "denied"
            r = registry.call(s, fin, None, "send_customer_mail", {"booking_ref": "B1", "amount": 5000})
            ap = s.get_approval(r["approval_id"])
            assert ap["booking_ref"] == "B1" and float(ap["amount"]) == 5000
    finally:
        registry.DEPT_TOOLS["finance"] = frozenset({"save_note"})


def test_write_low_is_disabled(monkeypatch):
    t = dataclasses.replace(registry.TOOLS["save_note"], name="wl", tier=Tier.WRITE_LOW)
    monkeypatch.setitem(registry.TOOLS, "wl", t)
    monkeypatch.setitem(registry.DEPT_TOOLS, "command", registry.DEPT_TOOLS["command"] | {"wl"})
    cmd = agent("adspot", "Command")
    with company_scope("adspot") as s:
        assert registry.call(s, cmd, None, "wl", {})["status"] == "denied"


def test_every_denied_call_is_audited():
    desk = agent("adspot", "Customer Desk")
    with company_scope("adspot") as s:
        registry.call(s, desk, None, "send_mail", {"to": "x@evil.com"})
        row = s.conn.execute("SELECT status, tool FROM tool_calls").fetchone()
        assert row == {"status": "denied", "tool": "send_mail"}
