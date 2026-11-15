import os
from pathlib import Path

os.environ.update({
    "DATABASE_URL": "postgresql://agentco:agentco@localhost:5432/agentco_test",
    "ADSPOT_RO_DATABASE_URL": "postgresql://agentco:agentco@localhost:5432/fake_adspot",
    "SIDADIYA_RO_DATABASE_URL": "postgresql://agentco:agentco@localhost:5432/fake_sidadiya",
    "LLM_PROVIDER": "fake", "COOKIE_SECURE": "false", "OWNER_EMAIL": "owner@example.com",
    "SESSION_SECRET": "test-secret", "GOOGLE_CLIENT_ID": "test-client", "UPLOAD_DIR": "/tmp/agentco-test-uploads",
})

import psycopg
import pytest

from agentco import db, seed
from agentco.llm import fake

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session", autouse=True)
def _database():
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    db.migrate()
    for name, f in (("fake_adspot", "adspot.sql"), ("fake_sidadiya", "sidadiya.sql")):
        with psycopg.connect(f"postgresql://agentco:agentco@localhost:5432/{name}", autocommit=True) as c:
            c.execute((FIX / f).read_text())
    yield
    db.close_pool()


@pytest.fixture(autouse=True)
def clean():
    from agentco.api import ratelimit
    ratelimit.reset()
    fake.set_handler(None)
    with db.admin() as a:
        a.execute("TRUNCATE jobs, messages, push_subscriptions, owner_auth, cost_events, tool_calls, approvals, "
                  "events, notes, runs, tasks, public_facts, agents, companies RESTART IDENTITY CASCADE")
    seed.seed()
    yield


def agent(slug: str, name: str) -> dict:
    from agentco.scope import company_scope
    with company_scope(slug) as s:
        return next(a for a in s.list_agents() if a["name"] == name)


def co_id(slug: str) -> int:
    with db.admin() as a:
        return a.execute("SELECT id FROM companies WHERE slug=%s", (slug,)).fetchone()["id"]
