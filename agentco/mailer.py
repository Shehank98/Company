"""Outbound mail. The ONLY code that calls Gmail send.

Rules enforced here (fail closed):
  * customer mails go only to the email on the booking record
  * publication mails go only to an address in the publications table
  * every mail is idempotent (unique key) and at-most-once (claim -> send -> record, never auto-retried)
  * customer mails wait out a 30 s undo window; publication mails send only on an explicit owner Send
  * every send is written to mail_log with Message-ID, thread id and booking ref"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from urllib.parse import urlparse

import httpx
from psycopg.types.json import Jsonb

from . import bookings, db, templates
from .config import get_settings
from .connectors import adspot, adspot_writer, gmail
from .scope import CompanyScope, scope_by_id

log = logging.getLogger("agentco.mailer")
ARTWORK_HOSTS = {"firebasestorage.googleapis.com", "storage.googleapis.com"}
MAX_ATTACH = 10 * 1024 * 1024


class MailError(Exception):
    pass


# ---------------- creating outbox rows ----------------
def _thread_for(scope: CompanyScope, ref: str) -> tuple[str | None, str | None]:
    r = scope.conn.execute("SELECT thread_id, rfc_message_id FROM mail_log WHERE company_id=%s AND booking_ref=%s "
                           "AND direction='in' AND thread_id IS NOT NULL ORDER BY id DESC LIMIT 1",
                           (scope.company_id, ref)).fetchone()
    return (r["thread_id"], r["rfc_message_id"]) if r else (None, None)


def _insert(scope: CompanyScope, *, kind, ref, to, subject, body, key, status, attachments=(), thread=(None, None),
            send_after=None) -> tuple[int, bool]:
    row = scope.conn.execute(
        "INSERT INTO mail_outbox (company_id, kind, booking_ref, to_addr, subject, body, attachments, thread_id, in_reply_to, "
        "status, send_after, idempotency_key) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (company_id, idempotency_key) DO NOTHING RETURNING id",
        (scope.company_id, kind, ref, to, subject, body, Jsonb(list(attachments)), thread[0], thread[1],
         status, send_after, key)).fetchone()
    if row:
        return row["id"], True
    return scope.conn.execute("SELECT id FROM mail_outbox WHERE company_id=%s AND idempotency_key=%s",
                              (scope.company_id, key)).fetchone()["id"], False


def _undo_deadline() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=get_settings().undo_window_seconds)


def queue_customer_payment(scope: CompanyScope, b: dict, amount, agent: dict | None, run_id: int | None) -> tuple[int, bool]:
    if not b.get("customer_email"):
        raise MailError("the booking has no customer email")
    subject, body = templates.payment_mail(b, amount, templates.greeting(scope, agent, run_id, b.get("customer_name")))
    return _insert(scope, kind="customer_payment", ref=b["booking_ref"], to=b["customer_email"], subject=subject, body=body,
                   key=f"cust_payment:{b['booking_ref']}", status="scheduled", send_after=_undo_deadline(),
                   thread=_thread_for(scope, b["booking_ref"]))


def queue_customer_receipt(scope: CompanyScope, b: dict, upload_name: str, agent: dict | None, run_id: int | None) -> tuple[int, bool]:
    if not b.get("customer_email"):
        raise MailError("the booking has no customer email")
    subject, body = templates.receipt_mail(b, templates.greeting(scope, agent, run_id, b.get("customer_name")))
    auto = scope.get_setting("auto_send_receipts", "false") == "true"      # default false
    return _insert(scope, kind="customer_receipt", ref=b["booking_ref"], to=b["customer_email"], subject=subject, body=body,
                   key=f"cust_receipt:{b['booking_ref']}", status="scheduled" if auto else "draft",
                   send_after=_undo_deadline() if auto else None,
                   attachments=[{"name": upload_name, "source": "upload", "ref": upload_name}],
                   thread=_thread_for(scope, b["booking_ref"]))


def queue_publication_drafts(scope: CompanyScope, b: dict) -> dict:
    """One DRAFT per publication group. Addresses come only from the publications table."""
    groups = adspot.newspaper_groups()
    by_group: dict[str, list[dict]] = {}
    for it in b["items"] or []:
        g = groups.get(it.get("newspaperId"), {}).get("group_ref")
        by_group.setdefault(g or "unmapped", []).append(it)
    made, problems = [], []
    for gref, items in by_group.items():
        pub = scope.conn.execute("SELECT * FROM publications WHERE company_id=%s AND group_ref=%s AND active",
                                 (scope.company_id, gref)).fetchone()
        gname = (pub or {}).get("name") or next((v["group_name"] for v in groups.values() if v["group_ref"] == gref), gref)
        if not pub or not pub["email"]:
            problems.append(f"No email saved for publication group '{gname}'. Add it under Mail > Publications.")
            continue
        subject, body = templates.publication_mail(b, gname, items)
        att = [{"name": f"artwork-{i + 1}", "source": "url", "ref": u}
               for i, u in enumerate(dict.fromkeys(it["adFileUrl"] for it in items if it.get("adFileUrl")))]
        oid, created = _insert(scope, kind="publication", ref=b["booking_ref"], to=pub["email"], subject=subject, body=body,
                               key=f"pub:{b['booking_ref']}:{gref}", status="draft", attachments=att)
        made.append({"id": oid, "group": gname, "created": created})
    return {"drafts": made, "problems": problems}


# ---------------- editing / undo ----------------
def update_draft(scope: CompanyScope, outbox_id: int, subject: str, body: str) -> dict:
    row = scope.conn.execute(
        "UPDATE mail_outbox SET subject=%s, body=%s, edited=TRUE WHERE id=%s AND company_id=%s AND status='draft' RETURNING *",
        (subject[:300], body[:20000], outbox_id, scope.company_id)).fetchone()
    if not row:
        raise MailError("only an unsent draft can be edited")
    return row


def undo(scope: CompanyScope, outbox_id: int) -> None:
    row = scope.conn.execute(
        "UPDATE mail_outbox SET status='cancelled' WHERE id=%s AND company_id=%s AND status='scheduled' "
        "AND (send_after IS NULL OR send_after > now()) RETURNING id", (outbox_id, scope.company_id)).fetchone()
    if not row:
        raise MailError("too late to undo: it is already being sent or was sent")


def list_outbox(scope: CompanyScope, limit: int = 60) -> list[dict]:
    return scope.conn.execute("SELECT * FROM mail_outbox WHERE company_id=%s ORDER BY id DESC LIMIT %s",
                              (scope.company_id, limit)).fetchall()


# ---------------- sending ----------------
def _recipient_problem(scope: CompanyScope, ob: dict) -> str | None:
    to = (ob["to_addr"] or "").strip().lower()
    if ob["kind"] in ("customer_payment", "customer_receipt"):
        b = bookings.get(scope, ob["booking_ref"])
        if not b or (b["customer_email"] or "").strip().lower() != to:
            return "recipient is not the email on the booking record"
        return None
    if ob["kind"] == "publication":
        ok = scope.conn.execute("SELECT 1 FROM publications WHERE company_id=%s AND active AND lower(email)=%s",
                                (scope.company_id, to)).fetchone()
        return None if ok else "recipient is not in the publications table"
    return "unknown mail kind"


def _load_attachments(att: list[dict]) -> list[tuple[str, bytes, str]]:
    out = []
    for a in att or []:
        if a["source"] == "upload":
            f = Path(get_settings().upload_dir) / "receipts" / Path(a["ref"]).name
            if not f.is_file():
                raise MailError(f"attachment {a['name']} is missing")
            data = f.read_bytes()
            mime = {".pdf": "application/pdf", ".png": "image/png", ".webp": "image/webp"}.get(f.suffix.lower(), "image/jpeg")
            out.append((f"receipt{f.suffix}", data, mime))
        elif a["source"] == "url":
            u = urlparse(a["ref"])
            if u.scheme != "https" or u.hostname not in ARTWORK_HOSTS:     # SSRF guard: Firebase Storage only
                raise MailError("artwork link is not a Firebase Storage URL")
            r = httpx.get(a["ref"], timeout=30, follow_redirects=False)
            if r.status_code != 200 or len(r.content) > MAX_ATTACH:
                raise MailError("could not fetch the artwork file")
            ext = Path(u.path).suffix[:5] or ".bin"
            out.append((f"{a['name']}{ext}", r.content, r.headers.get("content-type", "application/octet-stream")))
    return out


def send_outbox(company_id: int, slug: str, outbox_id: int, actor: str) -> dict:
    """At-most-once send. Returns {"status": sent|skipped|failed, ...}. Never auto-retried."""
    with scope_by_id(company_id, slug) as s:                       # tx 1: claim (committed before any network call)
        ob = s.conn.execute("UPDATE mail_outbox SET status='sending' WHERE id=%s AND company_id=%s "
                            "AND status IN ('draft','scheduled') RETURNING *", (outbox_id, company_id)).fetchone()
        if not ob:
            cur = s.conn.execute("SELECT status FROM mail_outbox WHERE id=%s AND company_id=%s", (outbox_id, company_id)).fetchone()
            return {"status": "skipped", "reason": f"mail is {cur['status'] if cur else 'missing'}"}
        problem = _recipient_problem(s, ob)
        if problem:
            s.conn.execute("UPDATE mail_outbox SET status='failed', error=%s WHERE id=%s", (problem, outbox_id))
            return {"status": "failed", "reason": problem}
    try:
        atts = _load_attachments(ob["attachments"])
        sent = gmail.get_client().send(to=ob["to_addr"], subject=ob["subject"], body=ob["body"], attachments=atts,
                                       thread_id=ob["thread_id"], in_reply_to=ob["in_reply_to"])
    except Exception as e:                                          # tx 2: record failure; owner decides on a retry
        with scope_by_id(company_id, slug) as s:
            s.conn.execute("UPDATE mail_outbox SET status='failed', error=%s WHERE id=%s",
                           (f"{type(e).__name__}: {e}"[:300], outbox_id))
        return {"status": "failed", "reason": f"{type(e).__name__}"}
    with scope_by_id(company_id, slug) as s:
        s.conn.execute("UPDATE mail_outbox SET status='sent', sent_at=now(), gmail_message_id=%s WHERE id=%s",
                       (sent["id"], outbox_id))
        s.conn.execute("INSERT INTO mail_log (company_id, direction, gmail_message_id, thread_id, rfc_message_id, to_addr, "
                       "subject, category, booking_ref, outbox_id) VALUES (%s,'out',%s,%s,%s,%s,%s,%s,%s,%s) "
                       "ON CONFLICT DO NOTHING", (company_id, sent["id"], sent.get("threadId"), sent.get("rfc_message_id"),
                                                  ob["to_addr"], ob["subject"], ob["kind"], ob["booking_ref"], outbox_id))
        try:
            if ob["kind"] == "publication":
                bookings.transition(s, ob["booking_ref"], "sent_to_publication")
            elif ob["kind"] == "customer_receipt":
                bookings.transition(s, ob["booking_ref"], "customer_receipt_sent")
        except bookings.IllegalTransition:
            pass                                                    # e.g. already further along: not an error
    if ob["kind"] == "publication":
        try:
            adspot_writer.mark_sent_to_publication(ob["booking_ref"])
        except adspot_writer.WriterDisabled:
            pass
        except Exception:
            log.exception("could not record sent_to_publication in AdSpot")
    return {"status": "sent", "gmail_message_id": sent["id"], "by": actor}


def send_due() -> int:
    """Worker hook: send customer mails whose undo window has passed."""
    if not gmail.configured():
        return 0
    with db.admin() as a:
        cos = a.execute("SELECT id, slug FROM companies").fetchall()
    n = 0
    for co in cos:
        with scope_by_id(co["id"], co["slug"]) as s:
            ids = [r["id"] for r in s.conn.execute(
                "SELECT id FROM mail_outbox WHERE company_id=%s AND status='scheduled' AND send_after <= now() "
                "ORDER BY id LIMIT 20", (co["id"],)).fetchall()]
        for i in ids:
            if send_outbox(co["id"], co["slug"], i, "undo-window-elapsed")["status"] == "sent":
                n += 1
    return n
