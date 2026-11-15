"""Scheduler (Railway cron, every 5 min): enqueue each due agent's daily run exactly once per day."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from . import db
from .config import get_settings
from .orchestrator import queue
from .scope import scope_by_id


def tick(now: datetime | None = None) -> int:
    tz = ZoneInfo(get_settings().timezone)
    now = (now or datetime.now(tz)).astimezone(tz)
    hhmm, day = now.strftime("%H:%M"), now.date().isoformat()
    with db.admin() as a:
        companies = a.execute("SELECT id, slug FROM companies").fetchall()
    n = 0
    for co in companies:
        with scope_by_id(co["id"], co["slug"]) as s:
            due = [ag for ag in s.list_agents()
                   if ag["status"] == "active" and ag["schedule_hhmm"] and ag["schedule_hhmm"] <= hhmm]
        with db.admin() as a:
            for ag in due:
                if queue.enqueue(a, company_id=co["id"], agent_id=ag["id"], kind="daily_report",
                                 dedupe_key=f"daily:{ag['id']}:{day}"):
                    n += 1
    return n


if __name__ == "__main__":
    print("enqueued", tick())
