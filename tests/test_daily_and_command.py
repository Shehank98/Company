from datetime import datetime
from zoneinfo import ZoneInfo

from agentco import db, scheduler, worker
from agentco.agents import command
from agentco.scope import company_scope

TZ = ZoneInfo("Asia/Colombo")


def test_scheduler_then_worker_produce_both_daily_reports_once():
    early = datetime(2026, 11, 16, 7, 0, tzinfo=TZ)
    assert scheduler.tick(early) == 1                       # only the 2-minute mail poll is due
    late = datetime(2026, 11, 16, 8, 0, tzinfo=TZ)
    assert scheduler.tick(late) == 3                        # 2 daily reports + the AdSpot mail/bookings poll
    assert scheduler.tick(late) == 0                        # exactly once per day / per 2-minute bucket
    for _ in range(4):                                      # 2 reports + 2 mail polls (07:00 and 08:00 buckets)
        assert worker.work_once()
    assert not worker.work_once()
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
    assert scheduler.tick(datetime(2026, 11, 16, 8, 0, tzinfo=TZ)) == 2   # Sidadiya report + mail poll


def test_command_status_and_pause():
    assert "AdSpot Ops: active" in command.handle("status of adspot", "all")
    assert "Paused AdSpot Ops" in command.handle("pause AdSpot Ops", "adspot")
    with company_scope("adspot") as s:
        assert next(a for a in s.list_agents() if a["name"] == "AdSpot Ops")["status"] == "paused"


def test_payment_chat_command_only_creates_a_confirm_card():
    reply = command.handle("payment received for BOOK-20260101-001", "adspot")
    assert "Confirm card ready" in reply and "Nothing happens until you confirm" in reply
    with company_scope("adspot") as s:
        assert len(s.list_approvals()) == 1 and s.list_approvals()[0]["tier"] == "OWNER_COMMAND"
        assert s.conn.execute("SELECT COUNT(*) AS n FROM ledger_entries").fetchone()["n"] == 0
        assert s.conn.execute("SELECT COUNT(*) AS n FROM mail_outbox").fetchone()["n"] == 0
