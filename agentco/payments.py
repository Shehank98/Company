"""Owner-command payment flow. 'Payment received for X' creates a CONFIRM CARD bound to the exact booking and
amount; only the owner's confirmation runs it. Idempotent: confirming twice records and mails once."""
from __future__ import annotations

from . import bookings, finance, mailer
from .connectors import adspot_writer
from .scope import CompanyScope, company_scope
from .tools import registry

TOL = 0.005


def _desk_agent(scope: CompanyScope) -> dict | None:
    return next((a for a in scope.list_agents() if a["role"] == "customer_desk" and a["status"] == "active"), None)


def request_mark_paid(slug: str, text: str, amount: float | None = None, method: str = "bank_transfer") -> dict:
    """Returns {"ok", "reply", "approval_id"?}. Creates a confirm card; changes nothing else."""
    with company_scope(slug) as s:
        cands, exact = bookings.resolve(s, text)
        if not cands:
            return {"ok": False, "reply": "I can't find that booking. Say the full reference, e.g. BOOK-20260101-001 or ADM-2026-1001."}
        if len(cands) > 1:
            lines = "\n".join(f"- {c['booking_ref']} / {c['quotation_ref']} - {c['customer_name']} - Rs. {float(c['total_amount']):,.2f}" for c in cands)
            return {"ok": False, "reply": f"Which booking? I found several:\n{lines}\nReply with the full reference."}
        b = cands[0]
        if b["state"] == "exception":
            return {"ok": False, "reply": f"{b['booking_ref']} is in an exception state ({b['exception_reason']}). Resolve it first."}
        if s.conn.execute("SELECT 1 FROM ledger_entries WHERE company_id=%s AND booking_ref=%s AND kind='payment_in'",
                          (s.company_id, b["booking_ref"])).fetchone():
            return {"ok": True, "reply": f"{b['booking_ref']} already has its payment recorded. Nothing was sent again."}
        total = float(b["total_amount"])
        pay = float(amount) if amount is not None else total
        mismatch = abs(pay - total) > TOL
        payload = {"booking_ref": b["booking_ref"], "amount": pay, "method": method, "expected_total": total,
                   "mismatch": mismatch, "matched_exactly": exact, "customer": b["customer_name"]}
        warn = (f"\nAMOUNT DIFFERS: booking total is Rs. {total:,.2f} but you said Rs. {pay:,.2f}. "
                "You must tick 'amounts differ, confirm anyway' to continue.") if mismatch else ""
        summary = f"Mark {b['booking_ref']} ({b['customer_name']}) paid: Rs. {pay:,.2f} by {method.replace('_', ' ')}"
        aid = registry.owner_command(s, "mark_booking_paid", payload, summary, b["booking_ref"], pay)
        return {"ok": True, "approval_id": aid, "mismatch": mismatch,
                "reply": f"Confirm card ready in Approvals: {summary}.{warn}\nNothing happens until you confirm."}


def execute_mark_paid(scope: CompanyScope, args: dict, ctx: dict) -> dict:
    ref, amount = args["booking_ref"], float(args["amount"])
    b = bookings.get(scope, ref)
    if b is None:
        raise registry.ToolDenied("booking not found")
    if b["state"] == "exception":
        raise registry.ToolDenied("booking is in an exception state")
    if abs(float(b["total_amount"]) - float(args["expected_total"])) > TOL:
        raise registry.ToolDenied("the booking total changed since the confirm card was made; ask again")
    if args.get("mismatch") and not ctx.get("accept_mismatch"):
        raise registry.ToolDenied("amount differs from the booking total: explicit confirmation required")

    inserted = scope.conn.execute(
        "INSERT INTO ledger_entries (company_id, kind, booking_ref, amount, method, marked_by, idempotency_key) "
        "VALUES (%s,'payment_in',%s,%s,%s,'owner',%s) ON CONFLICT (company_id, idempotency_key) DO NOTHING RETURNING id",
        (scope.company_id, ref, amount, args.get("method", "bank_transfer"), f"pay_in:{ref}")).fetchone()
    if not inserted:
        return {"status": "already_recorded", "notes": ["Payment was already recorded; no second mail was queued."]}

    notes: list[str] = []
    scope.conn.execute("UPDATE bookings SET paid_at=now() WHERE company_id=%s AND booking_ref=%s", (scope.company_id, ref))
    try:
        bookings.transition(scope, ref, "paid")
    except bookings.IllegalTransition:
        pass                                    # source already showed paid / further along
    try:
        if not adspot_writer.mark_paid(ref, f"agentco-{ctx.get('approval_id', '')}"):
            notes.append("AdSpot already showed this booking as paid.")
    except adspot_writer.WriterDisabled:
        notes.append("AdSpot's own booking record was NOT updated (no write role configured). Mark it paid in the admin panel too.")
    except Exception:
        notes.append("Could not update AdSpot's booking record; please mark it paid in the admin panel.")

    b = bookings.get(scope, ref)
    desk = _desk_agent(scope)
    mail_id, created = mailer.queue_customer_payment(scope, b, amount, desk, None)
    notes.append(f"Payment-confirmation mail to the customer is queued (undo within {mailer.get_settings().undo_window_seconds}s in Mail)."
                 if created else "A confirmation mail already existed; none added.")
    pub = mailer.queue_publication_drafts(scope, b)
    for d in pub["drafts"]:
        notes.append(f"Draft to {d['group']} is ready in Mail for you to edit and send (id {d['id']}).")
    notes += pub["problems"]
    finance.add_to_batch(scope, b)
    notes.append("Added to the pending payout batch (you pay publications manually).")
    scope.emit("payment.received", {"booking_ref": ref, "amount": amount}, "finance")
    if pub["drafts"]:
        scope.emit("booking.ready_for_publication", {"booking_ref": ref}, "operations")
    return {"status": "recorded", "mail_id": mail_id, "drafts": pub["drafts"], "notes": notes}
