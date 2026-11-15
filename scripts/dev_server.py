"""DEV ONLY - never deploy. Runs the API against a throwaway DB with the fake LLM, fixture app DBs
and the owner login bypassed, so the PWA can be exercised locally."""
import os
import sys
from pathlib import Path

os.environ.update({
    "DATABASE_URL": "postgresql://agentco:agentco@localhost:5432/agentco_dev",
    "ADSPOT_RO_DATABASE_URL": "postgresql://agentco:agentco@localhost:5432/fake_adspot",
    "SIDADIYA_RO_DATABASE_URL": "postgresql://agentco:agentco@localhost:5432/fake_sidadiya",
    "LLM_PROVIDER": "fake", "COOKIE_SECURE": "false", "OWNER_EMAIL": "owner@example.com",
})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg
import uvicorn

from agentco import db, scheduler, seed, worker
from agentco.agents.runner import run_job  # noqa: F401
from agentco.api import auth
from agentco.api.main import app
from agentco.scope import company_scope
from agentco.tools import registry

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
db.migrate(); seed.seed()
scheduler.tick()
while worker.work_once():
    pass
with company_scope("adspot") as s:
    ops = next(a for a in s.list_agents() if a["name"] == "AdSpot Ops")
    registry.call(s, ops, None, "send_publication_mail", {"booking_ref": "BOOK-20260101-002"})
app.dependency_overrides[auth.require_owner] = lambda: "owner@example.com"
uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
