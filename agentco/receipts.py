"""Publication receipt -> mail to the CUSTOMER (recipient is the email on the booking). Draft by default;
auto-send only if the auto_send_receipts setting is true. The reply-on-publication-thread option is stored
as a setting but NOT built yet (ponytail: add when you want it)."""
from __future__ import annotations

from . import bookings, db, mailer
from .scope import company_scope


def attach_receipt(slug: str, ref: str, stored_name: str) -> dict:
    with company_scope(slug) as s:
        b = bookings.get(s, ref)
        if b is None and bookings.refresh_one(s, ref):      # mirror may lag the live app
            b = bookings.get(s, ref)
        if b is None:
            raise LookupError(f"no booking {ref}")
        if b["state"] in ("awaiting_payment", "enquiry"):
            raise ValueError("this booking is not paid yet, so it cannot have a publication receipt")
        if b["state"] == "exception":
            raise ValueError("this booking is in an exception state")
        try:
            bookings.transition(s, b["booking_ref"], "receipt_received")
        except bookings.IllegalTransition:
            pass
        desk = next((a for a in s.list_agents() if a["role"] == "customer_desk" and a["status"] == "active"), None)
        oid, created = mailer.queue_customer_receipt(s, bookings.get(s, ref), stored_name, desk, None)
        s.emit("receipt.received", {"booking_ref": b["booking_ref"]}, "operations")
        auto = s.get_setting("auto_send_receipts", "false") == "true"
    return {"outbox_id": oid, "created": created, "auto": auto, "booking_ref": b["booking_ref"]}


def link_latest_upload(slug: str, ref: str) -> dict:
    """Chat flow: the owner uploaded a receipt without saying which booking, then named it."""
    with db.admin() as a:
        row = a.execute("SELECT id, attachment FROM messages WHERE role='owner' AND attachment IS NOT NULL "
                        "AND linked_booking IS NULL AND (attachment->>'kind')='receipt' ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            raise LookupError("I have no unlinked receipt upload. Upload the receipt first.")
    res = attach_receipt(slug, ref, row["attachment"]["stored_as"])
    with db.admin() as a:
        a.execute("UPDATE messages SET linked_booking=%s WHERE id=%s", (res["booking_ref"], row["id"]))
    return res
