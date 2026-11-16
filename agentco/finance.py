"""Payout batches: PREPARE ONLY. AgentCo never moves money. The owner pays publications by bank transfer
and then records it here."""
from __future__ import annotations

import csv
import io

from . import bookings
from .connectors import adspot
from .scope import CompanyScope


def _open_batch(scope: CompanyScope) -> int:
    r = scope.conn.execute("SELECT id FROM payout_batches WHERE company_id=%s AND status='open' ORDER BY id DESC LIMIT 1",
                           (scope.company_id,)).fetchone()
    if r:
        return r["id"]
    return scope.conn.execute("INSERT INTO payout_batches (company_id) VALUES (%s) RETURNING id", (scope.company_id,)).fetchone()["id"]


def add_to_batch(scope: CompanyScope, b: dict) -> int:
    """Idempotent: one item per (booking, publication group)."""
    groups = adspot.newspaper_groups()
    owed: dict[str, float] = {}
    for np_id, amt in bookings.payout_amount(b["items"]).items():
        g = groups.get(np_id, {}).get("group_ref") or "unmapped"
        owed[g] = owed.get(g, 0.0) + amt
    batch = _open_batch(scope)
    n = 0
    for gref, amt in owed.items():
        n += scope.conn.execute(
            "INSERT INTO payout_items (company_id, batch_id, booking_ref, group_ref, amount) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (company_id, booking_ref, group_ref) DO NOTHING",
            (scope.company_id, batch, b["booking_ref"], gref, round(amt, 2))).rowcount
    return n


def batches(scope: CompanyScope) -> list[dict]:
    out = []
    for bt in scope.conn.execute("SELECT * FROM payout_batches WHERE company_id=%s ORDER BY id DESC LIMIT 10", (scope.company_id,)).fetchall():
        items = scope.conn.execute(
            "SELECT i.group_ref, COALESCE(p.name, i.group_ref) AS publication, i.booking_ref, i.amount FROM payout_items i "
            "LEFT JOIN publications p ON p.company_id=i.company_id AND p.group_ref=i.group_ref "
            "WHERE i.company_id=%s AND i.batch_id=%s ORDER BY publication, i.booking_ref", (scope.company_id, bt["id"])).fetchall()
        totals: dict[str, float] = {}
        for i in items:
            totals[i["publication"]] = totals.get(i["publication"], 0.0) + float(i["amount"])
        out.append({**bt, "items": items, "totals": totals, "grand_total": sum(totals.values())})
    return out


def to_csv(batch: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["publication", "booking_ref", "amount_lkr"])
    for i in batch["items"]:
        w.writerow([i["publication"], i["booking_ref"], f"{float(i['amount']):.2f}"])
    for p, t in batch["totals"].items():
        w.writerow([f"TOTAL {p}", "", f"{t:.2f}"])
    return buf.getvalue()


def mark_batch_paid_manually(scope: CompanyScope, batch_id: int, actor: str) -> int:
    """Records that the OWNER paid the batch by hand. Writes payout_out ledger entries; moves nothing."""
    bt = scope.conn.execute("UPDATE payout_batches SET status='paid_manually', paid_at=now() "
                            "WHERE id=%s AND company_id=%s AND status='open' RETURNING id", (batch_id, scope.company_id)).fetchone()
    if not bt:
        raise ValueError("batch is not open")
    n = 0
    for i in scope.conn.execute("SELECT * FROM payout_items WHERE company_id=%s AND batch_id=%s", (scope.company_id, batch_id)).fetchall():
        n += scope.conn.execute(
            "INSERT INTO ledger_entries (company_id, kind, booking_ref, amount, method, marked_by, idempotency_key, note) "
            "VALUES (%s,'payout_out',%s,%s,'bank_transfer_manual',%s,%s,%s) ON CONFLICT DO NOTHING",
            (scope.company_id, i["booking_ref"], i["amount"], actor, f"payout:{batch_id}:{i['booking_ref']}:{i['group_ref']}",
             f"publication {i['group_ref']}")).rowcount
    return n
