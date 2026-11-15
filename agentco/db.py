"""Postgres access. Two kinds of connection:

* scoped(company_id): one transaction, SET LOCAL ROLE agentco_app + app.company_id, so row-level
  security confines every query to that single company (even on a superuser connection).
* admin(): unscoped owner connection. Only for system tables (companies, jobs, owner_auth,
  messages, push_subscriptions) and the scheduler/worker plumbing.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import get_settings

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            get_settings().database_url, min_size=1, max_size=10,
            kwargs={"row_factory": dict_row}, open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def admin() -> Iterator[psycopg.Connection]:
    with pool().connection() as conn:
        yield conn


@contextmanager
def scoped(company_id: int) -> Iterator[psycopg.Connection]:
    """Transaction confined to one company by RLS. Commits on success, rolls back on error."""
    if not isinstance(company_id, int) or isinstance(company_id, bool):
        raise TypeError("company_id must be an int")
    with pool().connection() as conn:
        with conn.transaction():
            conn.execute("SET LOCAL ROLE agentco_app")
            conn.execute("SELECT set_config('app.company_id', %s, true)", (str(company_id),))
            yield conn


def migrate(conn: psycopg.Connection | None = None) -> list[str]:
    """Apply migrations/*.sql in order, once each."""
    folder = Path(__file__).resolve().parent.parent / "migrations"
    applied: list[str] = []
    own = conn is None
    c = conn or psycopg.connect(get_settings().database_url, row_factory=dict_row, autocommit=True)
    try:
        c.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now())")
        c.execute("SELECT pg_advisory_lock(727001)")
        done = {r["name"] for r in c.execute("SELECT name FROM schema_migrations").fetchall()}
        for f in sorted(folder.glob("*.sql")):
            if f.name in done:
                continue
            with c.transaction():
                c.execute(f.read_text())
                c.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,))
            applied.append(f.name)
        c.execute("SELECT pg_advisory_unlock(727001)")
    finally:
        if own:
            c.close()
    return applied


if __name__ == "__main__":
    print("applied:", migrate())
