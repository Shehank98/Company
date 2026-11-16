"""Postgres job queue: FOR UPDATE SKIP LOCKED, no extra broker."""
from __future__ import annotations

from psycopg.types.json import Jsonb


def enqueue(conn, *, company_id: int, agent_id: int | None, kind: str, payload: dict | None = None,
            dedupe_key: str | None = None, delay_seconds: int = 0) -> int | None:
    row = conn.execute(
        "INSERT INTO jobs (company_id, agent_id, kind, payload, dedupe_key, run_at) "
        "VALUES (%s,%s,%s,%s,%s, now() + make_interval(secs => %s)) "
        "ON CONFLICT (dedupe_key) DO NOTHING RETURNING id",
        (company_id, agent_id, kind, Jsonb(payload or {}), dedupe_key, delay_seconds)).fetchone()
    return row["id"] if row else None


def claim_one(conn) -> dict | None:
    with conn.transaction():
        row = conn.execute(
            "SELECT id FROM jobs WHERE status='queued' AND run_at <= now() "
            "ORDER BY run_at, id FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
        if not row:
            return None
        return conn.execute(
            "UPDATE jobs SET status='running', locked_at=now(), attempts=attempts+1 WHERE id=%s RETURNING *",
            (row["id"],)).fetchone()


def finish(conn, job_id: int, outcome: str) -> None:
    ok = outcome in ("done", "skipped", "budget_stopped")
    conn.execute("UPDATE jobs SET status=%s, last_error=%s WHERE id=%s",
                 ("done" if ok else "failed", None if ok else outcome[:300], job_id))


def requeue_stuck(conn, minutes: int = 15, max_attempts: int = 3) -> int:
    return conn.execute(
        "UPDATE jobs SET status='queued' WHERE status='running' AND attempts < %s "
        "AND locked_at < now() - make_interval(mins => %s)", (max_attempts, minutes)).rowcount
