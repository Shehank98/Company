import psycopg
import pytest

from agentco import db
from agentco.scope import company_scope
from conftest import co_id


def test_company_a_cannot_read_company_b_via_repo():
    with company_scope("adspot") as s:
        s.create_task("secret adspot task", None)
        s.add_note(agent_id=None, kind="note", title="t", body="adspot only")
    with company_scope("sidadiya") as s:
        assert s.list_tasks() == []
        assert s.latest_note("note") is None


def test_rls_blocks_raw_sql_across_companies():
    with company_scope("adspot") as s:
        s.create_task("adspot task", None)
    with company_scope("sidadiya") as s:   # raw SELECT with no company filter at all
        assert s.conn.execute("SELECT * FROM tasks").fetchall() == []
        assert s.conn.execute("SELECT * FROM agents").fetchall() != []   # only its own agents
        assert {r["company_id"] for r in s.conn.execute("SELECT company_id FROM agents").fetchall()} == {s.company_id}


def test_rls_blocks_writing_into_another_company():
    other = co_id("adspot")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with company_scope("sidadiya") as s:
            s.conn.execute("INSERT INTO tasks (company_id, title) VALUES (%s, 'x')", (other,))


def test_no_scope_sees_nothing():
    with db.pool().connection() as conn:
        with conn.transaction():
            conn.execute("SET LOCAL ROLE agentco_app")
            assert conn.execute("SELECT * FROM agents").fetchall() == []


def test_audit_tables_are_append_only_for_app_role():
    with company_scope("adspot") as s:
        s.log_tool_call(run_id=None, agent_id=None, tool="t", tier="READ", args={}, status="ok")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with company_scope("adspot") as s:
            s.conn.execute("UPDATE tool_calls SET status='denied'")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with company_scope("adspot") as s:
            s.conn.execute("DELETE FROM cost_events")
