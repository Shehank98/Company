"""AgentCo's view of AdSpot bookings: read-only sync + the workflow state machine.

AdSpot has four independent status tracks (see docs/systems-map.md). The source can only move our state
FORWARD; AgentCo-only steps (receipt_received, customer_receipt_sent, closed) are never overwritten by a sync."""
from __future__ import annotations

import re

from psycopg.types.json import Jsonb

from .connectors import adspot
from .connectors.gmail import REF_RE
from .scope import CompanyScope

ORDER = ["enquiry", "awaiting_payment", "paid", "sent_to_publication", "receipt_received",
         "customer_receipt_sent", "published", "closed"]


class IllegalTransition(Exception):
    pass


def _idx(state: str) -> int:
    return ORDER.index(state) if state in ORDER else -1


def derive_source_state(row: dict) -> tuple[str, str | None]:
    """Minimum state implied by the live app's columns. Returns (state, exception_reason)."""
    if (row.get("status") or "").lower() == "cancelled":
        return "exception", "cancelled in the booking system"
    if row.get("published_at"):
        return "published", None
    if row.get("sent_to_publication_at"):
        return "sent_to_publication", None
    if (row.get("payment_status") or "").lower() == "completed":
        return "paid", None
    return "awaiting_payment", None


def upsert_from_source(scope: CompanyScope, row: dict) -> str:
    ref = row["booking_id"]
    scope.conn.execute(
        "INSERT INTO bookings (company_id, booking_ref, quotation_ref, customer_name, customer_email, total_amount, "
        "items, source_status, source_payment) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (company_id, booking_ref) DO UPDATE SET quotation_ref=EXCLUDED.quotation_ref, "
        "customer_name=EXCLUDED.customer_name, customer_email=EXCLUDED.customer_email, "
        "total_amount=EXCLUDED.total_amount, items=EXCLUDED.items, source_status=EXCLUDED.source_status, "
        "source_payment=EXCLUDED.source_payment, source_synced_at=now()",
        (scope.company_id, ref, row.get("quotation_number"), row.get("customer_name"),
         (row.get("customer_email") or "").strip() or None, row.get("total_amount") or 0,
         Jsonb(row.get("items") or []), row.get("status"), row.get("payment_status")))
    target, reason = derive_source_state(row)
    cur = scope.conn.execute("SELECT state FROM bookings WHERE company_id=%s AND booking_ref=%s",
                             (scope.company_id, ref)).fetchone()["state"]
    if target == "exception":
        if cur != "exception":
            scope.conn.execute("UPDATE bookings SET state='exception', exception_reason=%s WHERE company_id=%s AND booking_ref=%s",
                               (reason, scope.company_id, ref))
    elif cur == "exception" and target != "exception" and reason is None:
        pass  # a human resolves exceptions; the sync does not silently un-exception
    elif _idx(target) > _idx(cur) and cur != "exception":
        scope.conn.execute("UPDATE bookings SET state=%s WHERE company_id=%s AND booking_ref=%s",
                           (target, scope.company_id, ref))
    return ref


def sync_all(scope: CompanyScope) -> int:
    n = 0
    for row in adspot.fetch_bookings():
        upsert_from_source(scope, row)
        n += 1
    return n


def refresh_one(scope: CompanyScope, ref: str) -> bool:
    row = adspot.fetch_booking(ref)
    if row:
        upsert_from_source(scope, row)
    return bool(row)


def get(scope: CompanyScope, ref: str) -> dict | None:
    return scope.conn.execute(
        "SELECT * FROM bookings WHERE company_id=%s AND (upper(booking_ref)=upper(%s) OR upper(quotation_ref)=upper(%s))",
        (scope.company_id, ref, ref)).fetchone()


def list_bookings(scope: CompanyScope, limit: int = 100) -> list[dict]:
    return scope.conn.execute(
        "SELECT b.*, EXISTS(SELECT 1 FROM ledger_entries l WHERE l.company_id=b.company_id AND l.booking_ref=b.booking_ref "
        "AND l.kind='payment_in') AS payment_recorded FROM bookings b WHERE b.company_id=%s "
        "ORDER BY b.created_at DESC, b.id DESC LIMIT %s", (scope.company_id, limit)).fetchall()


def resolve(scope: CompanyScope, text: str) -> tuple[list[dict], bool]:
    """Find the booking a chat message means. Returns (candidates, exact). Exact only for a full reference."""
    m = REF_RE.search(text or "")
    if m:
        ref = m.group(1).upper()
        b = get(scope, ref)
        if b is None and refresh_one(scope, ref):
            b = get(scope, ref)
        return ([b] if b else []), bool(b)
    digits = re.findall(r"\d{3,}", text or "")
    if digits:
        suf = digits[-1]
        q = ("SELECT * FROM bookings WHERE company_id=%s AND (booking_ref LIKE %s OR quotation_ref LIKE %s) "
             "ORDER BY created_at DESC LIMIT 5")
        rows = scope.conn.execute(q, (scope.company_id, f"%{suf}", f"%{suf}")).fetchall()
        if not rows:                      # the mirror can lag the live app by up to a poll: sync once and retry
            sync_all(scope)
            rows = scope.conn.execute(q, (scope.company_id, f"%{suf}", f"%{suf}")).fetchall()
        return rows, False
    return [], False


def transition(scope: CompanyScope, ref: str, to: str, reason: str | None = None) -> None:
    b = get(scope, ref)
    if b is None:
        raise LookupError(f"no booking {ref}")
    cur = b["state"]
    if to == "exception":
        pass
    elif cur == "exception":
        raise IllegalTransition("resolve the exception first")
    elif to not in ORDER or _idx(to) <= _idx(cur):
        raise IllegalTransition(f"{cur} -> {to} is not a forward step")
    scope.conn.execute("UPDATE bookings SET state=%s, exception_reason=%s WHERE company_id=%s AND booking_ref=%s",
                       (to, reason if to == "exception" else None, scope.company_id, b["booking_ref"]))


def payout_amount(items: list[dict]) -> dict[str, float]:
    """What the publication is owed per newspaper: the ad price itself (details.adTotal, else price), not our
    commission/VAT/service charge. ASSUMPTION to confirm with the owner."""
    out: dict[str, float] = {}
    for it in items or []:
        amt = (it.get("details") or {}).get("adTotal", it.get("price", 0)) or 0
        out[it.get("newspaperId") or "unknown"] = out.get(it.get("newspaperId") or "unknown", 0.0) + float(amt)
    return out
