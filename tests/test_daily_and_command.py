from datetime import datetime
from zoneinfo import ZoneInfo

from agentco import db, scheduler, worker
from agentco.agents import command
from agentco.scope import company_scope

TZ = ZoneInfo("Asia/Colombo")


def test_scheduler_then_worker_produce_both_daily_reports_once():
    early = datetime(2026, 11, 16, 7, 0, tzinfo=TZ)
    assert scheduler.tick(early) == 0                       # not due yet
    late = datetime(2026, 11, 16, 8, 0, tzinfo=TZ)
    assert scheduler.tick(late) == 2                        # AdSpot Ops + Sidadiya Insights only
    assert scheduler.tick(late) == 0                        # exactly once per day
    assert worker.work_once() and worker.work_once() and not worker.work_once()
    for slug in ("adspot", "sidadiya"):
        with company_scope(slug) as s:
            note = s.latest_note("daily_report")
            assert note and note["data"]                    # snapshot kept alongside the text
            assert any(e["type"] == "report.ready" for e in s.list_events())
            assert s.conn.execute("SELECT COUNT(*) AS n FROM cost_events").fetchone()["n"] == 1
    with db.admin() as a:
        assert a.execute("SELECT COUNT(*) AS n FROM messages WHERE role='system'").fetchone()["n"] == 2


def test_adspot_report_data_never_lands_in_sidadiya():
    scheduler.tick(datetime(2026, 11, 16, 8, 0, tzinfo=TZ))
    while worker.work_once():
        pass
    with company_scope("sidadiya") as s:
        assert "bookings_by_status" not in str(s.latest_note("daily_report")["data"])


def test_paused_agent_is_not_scheduled():
    with company_scope("adspot") as s:
        ops = next(a for a in s.list_agents() if a["name"] == "AdSpot Ops")
        s.set_agent_status(ops["id"], "paused")
    assert scheduler.tick(datetime(2026, 11, 16, 8, 0, tzinfo=TZ)) == 1


def test_command_status_and_pause():
    assert "AdSpot Ops: active" in command.handle("status of adspot", "all")
    assert "Paused AdSpot Ops" in command.handle("pause AdSpot Ops", "adspot")
    with company_scope("adspot") as s:
        assert next(a for a in s.list_agents() if a["name"] == "AdSpot Ops")["status"] == "paused"


def test_payment_chat_command_changes_nothing_in_phase_1():
    reply = command.handle("payment received for BOOK-20260101-002", "adspot")
    assert "Nothing was changed" in reply
    with company_scope("adspot") as s:
        assert s.list_approvals(None) == []
        assert s.conn.execute("SELECT COUNT(*) AS n FROM tool_calls").fetchone()["n"] == 0
