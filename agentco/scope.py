"""CompanyScope: the only way agent code touches tenant data.

Every method filters on company_id explicitly AND runs inside an RLS-confined transaction
(db.scoped), so a bug in either layer alone cannot leak another company's rows.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

import psycopg
from psycopg.types.json import Jsonb

from . import db
from .models import EVENT_TYPES


@dataclass
class CompanyScope:
    company_id: int
    slug: str
    conn: psycopg.Connection

    # ---- agents ----
    def get_agent(self, agent_id: int) -> dict | None:
        return self.conn.execute(
            "SELECT * FROM agents WHERE id=%s AND company_id=%s", (agent_id, self.company_id)
        ).fetchone()

    def list_agents(self) -> list[dict]:
        return self.conn.execute(
            "SELECT * FROM agents WHERE company_id=%s ORDER BY id", (self.company_id,)
        ).fetchall()

    def set_agent_status(self, agent_id: int, status: str) -> None:
        self.conn.execute(
            "UPDATE agents SET status=%s WHERE id=%s AND company_id=%s",
            (status, agent_id, self.company_id),
        )

    def set_agent_budget(self, agent_id: int, usd: float) -> None:
        self.conn.execute(
            "UPDATE agents SET monthly_budget_usd=%s WHERE id=%s AND company_id=%s",
            (usd, agent_id, self.company_id),
        )

    # ---- runs / tasks ----
    def start_run(self, agent_id: int, task_id: int | None = None) -> int:
        return self.conn.execute(
            "INSERT INTO runs (company_id, agent_id, task_id) VALUES (%s,%s,%s) RETURNING id",
            (self.company_id, agent_id, task_id),
        ).fetchone()["id"]

    def finish_run(self, run_id: int, status: str, error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE runs SET status=%s, error=%s, finished_at=now() WHERE id=%s AND company_id=%s",
            (status, error, run_id, self.company_id),
        )

    def create_task(self, title: str, agent_id: int | None, payload: dict | None = None) -> int:
        return self.conn.execute(
            "INSERT INTO tasks (company_id, agent_id, title, payload) VALUES (%s,%s,%s,%s) RETURNING id",
            (self.company_id, agent_id, title, Jsonb(payload or {})),
        ).fetchone()["id"]

    def update_task(self, task_id: int, status: str) -> None:
        self.conn.execute(
            "UPDATE tasks SET status=%s, updated_at=now() WHERE id=%s AND company_id=%s",
            (status, task_id, self.company_id),
        )

    def list_tasks(self, limit: int = 100) -> list[dict]:
        return self.conn.execute(
            "SELECT t.*, a.name AS agent_name FROM tasks t LEFT JOIN agents a ON a.id=t.agent_id "
            "WHERE t.company_id=%s ORDER BY t.id DESC LIMIT %s", (self.company_id, limit)
        ).fetchall()

    # ---- events (typed, validated) ----
    def emit(self, type_: str, payload: dict, source_dept: str | None = None) -> int:
        model = EVENT_TYPES.get(type_)
        if model is None:
            raise ValueError(f"unknown event type {type_!r}")
        clean = model(**payload).model_dump()
        return self.conn.execute(
            "INSERT INTO events (company_id, type, source_dept, payload) VALUES (%s,%s,%s,%s) RETURNING id",
            (self.company_id, type_, source_dept, Jsonb(clean)),
        ).fetchone()["id"]

    def list_events(self, limit: int = 100) -> list[dict]:
        return self.conn.execute(
            "SELECT * FROM events WHERE company_id=%s ORDER BY id DESC LIMIT %s", (self.company_id, limit)
        ).fetchall()

    # ---- tool-call audit ----
    def log_tool_call(self, *, run_id, agent_id, tool, tier, args, status, result=None, reason=None) -> int:
        return self.conn.execute(
            "INSERT INTO tool_calls (company_id, run_id, agent_id, tool, tier, args, result, status, reason) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (self.company_id, run_id, agent_id, tool, tier, Jsonb(_jsonable(args)),
             Jsonb(_jsonable(result)) if result is not None else None, status, reason),
        ).fetchone()["id"]

    # ---- approvals ----
    def create_approval(self, *, tool, tier, summary, payload, agent_id, run_id,
                        booking_ref=None, amount=None) -> tuple[int, bool]:
        """Idempotent: same tool+payload => same approval row. Returns (id, created)."""
        key = hashlib.sha256(json.dumps([tool, payload, booking_ref, amount], sort_keys=True, default=str).encode()).hexdigest()
        row = self.conn.execute(
            "INSERT INTO approvals (company_id, tool, tier, summary, payload, booking_ref, amount, "
            "idempotency_key, requested_by, run_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (company_id, idempotency_key) DO NOTHING RETURNING id",
            (self.company_id, tool, tier, summary, Jsonb(_jsonable(payload)), booking_ref, amount, key, agent_id, run_id),
        ).fetchone()
        if row:
            return row["id"], True
        existing = self.conn.execute(
            "SELECT id FROM approvals WHERE company_id=%s AND idempotency_key=%s", (self.company_id, key)
        ).fetchone()
        return existing["id"], False

    def list_approvals(self, status: str | None = "pending") -> list[dict]:
        if status:
            return self.conn.execute(
                "SELECT * FROM approvals WHERE company_id=%s AND status=%s ORDER BY id DESC",
                (self.company_id, status)).fetchall()
        return self.conn.execute(
            "SELECT * FROM approvals WHERE company_id=%s ORDER BY id DESC LIMIT 100", (self.company_id,)).fetchall()

    def get_approval(self, approval_id: int) -> dict | None:
        return self.conn.execute(
            "SELECT * FROM approvals WHERE id=%s AND company_id=%s", (approval_id, self.company_id)).fetchone()

    def decide_approval(self, approval_id: int, status: str, summary: str | None = None) -> dict | None:
        """Only a still-pending approval can be decided (no re-deciding)."""
        return self.conn.execute(
            "UPDATE approvals SET status=%s, decided_at=now(), summary=COALESCE(%s, summary) "
            "WHERE id=%s AND company_id=%s AND status='pending' RETURNING *",
            (status, summary, approval_id, self.company_id),
        ).fetchone()

    def edit_approval_summary(self, approval_id: int, summary: str) -> dict | None:
        return self.conn.execute(
            "UPDATE approvals SET summary=%s WHERE id=%s AND company_id=%s AND status='pending' RETURNING *",
            (summary[:500], approval_id, self.company_id)).fetchone()

    # ---- cost ----
    def add_cost(self, *, agent_id, run_id, provider, model, route, data_class, task,
                 tokens_in, tokens_out, cost_usd) -> None:
        self.conn.execute(
            "INSERT INTO cost_events (company_id, agent_id, run_id, provider, model, route, data_class, "
            "task, tokens_in, tokens_out, cost_usd) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (self.company_id, agent_id, run_id, provider, model, route, str(data_class), task,
             tokens_in, tokens_out, cost_usd),
        )

    def month_spend(self, agent_id: int) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) AS s FROM cost_events WHERE company_id=%s AND agent_id=%s "
            "AND created_at >= date_trunc('month', now())", (self.company_id, agent_id)).fetchone()
        return float(row["s"])

    def cost_summary(self) -> list[dict]:
        return self.conn.execute(
            "SELECT a.name AS agent, c.provider, c.route, c.data_class, SUM(c.tokens_in) AS tokens_in, "
            "SUM(c.tokens_out) AS tokens_out, SUM(c.cost_usd) AS cost_usd "
            "FROM cost_events c LEFT JOIN agents a ON a.id=c.agent_id "
            "WHERE c.company_id=%s AND c.created_at >= date_trunc('month', now()) "
            "GROUP BY 1,2,3,4 ORDER BY cost_usd DESC", (self.company_id,)).fetchall()

    # ---- content packs (manual-publish) ----
    _PACK_COLS = {"status", "copy", "hashtags", "image_brief", "image_prompt", "aspect_ratios",
                  "image_file", "ad_setup", "claims_check", "error", "image_mode", "run_id"}

    def create_pack(self, *, kind: str, topic: str, image_mode: str, agent_id: int | None) -> int:
        return self.conn.execute(
            "INSERT INTO content_packs (company_id, kind, topic, image_mode, created_by) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING id", (self.company_id, kind, topic, image_mode, agent_id)
        ).fetchone()["id"]

    def get_pack(self, pack_id: int) -> dict | None:
        return self.conn.execute(
            "SELECT * FROM content_packs WHERE id=%s AND company_id=%s", (pack_id, self.company_id)).fetchone()

    def list_packs(self, limit: int = 50) -> list[dict]:
        return self.conn.execute(
            "SELECT * FROM content_packs WHERE company_id=%s ORDER BY id DESC LIMIT %s",
            (self.company_id, limit)).fetchall()

    def update_pack(self, pack_id: int, **fields) -> None:
        bad = set(fields) - self._PACK_COLS
        if bad:
            raise ValueError(f"cannot update {bad}")
        sets = ", ".join(f"{k}=%s" for k in fields)
        vals = [Jsonb(_jsonable(v)) if isinstance(v, (dict, list)) else v for v in fields.values()]
        self.conn.execute(f"UPDATE content_packs SET {sets}, updated_at=now() WHERE id=%s AND company_id=%s",
                          (*vals, pack_id, self.company_id))

    def move_pack(self, pack_id: int, new: str, allowed_from: tuple[str, ...]) -> dict | None:
        return self.conn.execute(
            "UPDATE content_packs SET status=%s, updated_at=now() WHERE id=%s AND company_id=%s "
            "AND status = ANY(%s) RETURNING *", (new, pack_id, self.company_id, list(allowed_from))).fetchone()

    # ---- public facts (the only source for marketing copy) ----
    def list_facts(self) -> list[dict]:
        return self.conn.execute(
            "SELECT key, value FROM public_facts WHERE company_id=%s ORDER BY key", (self.company_id,)).fetchall()

    def upsert_fact(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO public_facts (company_id, key, value) VALUES (%s,%s,%s) "
            "ON CONFLICT (company_id, key) DO UPDATE SET value=EXCLUDED.value",
            (self.company_id, key, value))

    # ---- notes ----
    def add_note(self, *, agent_id, kind, title, body, data=None) -> int:
        return self.conn.execute(
            "INSERT INTO notes (company_id, agent_id, kind, title, body, data) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (self.company_id, agent_id, kind, title, body, Jsonb(_jsonable(data or {}))),
        ).fetchone()["id"]

    def latest_note(self, kind: str) -> dict | None:
        return self.conn.execute(
            "SELECT * FROM notes WHERE company_id=%s AND kind=%s ORDER BY id DESC LIMIT 1",
            (self.company_id, kind)).fetchone()


def _jsonable(v: Any) -> Any:
    return json.loads(json.dumps(v, default=str))


@contextmanager
def company_scope(slug: str) -> Iterator[CompanyScope]:
    """Open a scope by company slug (looks the id up with the admin connection first)."""
    with db.admin() as a:
        row = a.execute("SELECT id FROM companies WHERE slug=%s", (slug,)).fetchone()
    if not row:
        raise LookupError(f"unknown company {slug!r}")
    with scope_by_id(row["id"], slug) as s:
        yield s


@contextmanager
def scope_by_id(company_id: int, slug: str) -> Iterator[CompanyScope]:
    with db.scoped(company_id) as conn:
        yield CompanyScope(company_id, slug, conn)
