"""AgentCo API + PWA host. Every route except /auth/*, /api/health and /api/config needs the owner session."""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import db
from ..agents import command
from ..config import get_settings
from ..notify import post_message
from ..scope import scope_by_id
from ..tools import registry
from . import auth
from .ratelimit import limit

app = FastAPI(title="AgentCo", docs_url=None, redoc_url=None)
app.include_router(auth.router)
api = [Depends(auth.require_owner)]


def _companies(scope: str) -> list[dict]:
    with db.admin() as a:
        rows = a.execute("SELECT id, slug, name FROM companies ORDER BY id").fetchall()
    if scope == "all":
        return rows
    sel = [r for r in rows if r["slug"] == scope]
    if not sel:
        raise HTTPException(404, "Unknown company")
    return sel


def _each(scope: str, fn):
    """Run fn(CompanyScope) once per company, each in its own RLS-confined transaction; tag rows."""
    out = []
    for co in _companies(scope):
        with scope_by_id(co["id"], co["slug"]) as s:
            for row in fn(s):
                out.append({**row, "company": co["slug"]})
    return out


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/config")
def config():
    s = get_settings()
    return {"google_client_id": s.google_client_id, "vapid_public_key": s.vapid_public_key}


@app.get("/api/companies", dependencies=api)
def companies():
    return _companies("all")


# ---------------- chat ----------------
class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    scope: str = "all"


@app.get("/api/chat", dependencies=api)
def chat_history():
    with db.admin() as a:
        rows = a.execute("SELECT * FROM messages ORDER BY id DESC LIMIT 100").fetchall()
    return list(reversed(rows))


@app.post("/api/chat", dependencies=[*api, Depends(limit("chat", 30, 60))])
def chat(body: ChatIn):
    _companies(body.scope)  # validates scope
    post_message(body.scope, "owner", body.text)
    reply = command.handle(body.text, body.scope)
    post_message(body.scope, "command", reply)
    return {"reply": reply}


_MAGIC = {".pdf": b"%PDF", ".png": b"\x89PNG", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff", ".webp": b"RIFF",
          ".mp4": b"", ".mov": b"", ".webm": b"\x1aE\xdf\xa3"}
_VIDEO_EXT = (".mp4", ".mov", ".webm")


def _magic_ok(ext: str, data: bytes) -> bool:
    if ext in (".mp4", ".mov"):
        return data[4:8] == b"ftyp"                      # ISO base media container signature
    return data.startswith(_MAGIC[ext])


def _store_upload(file: UploadFile, subdir: str = "", allowed: tuple[str, ...] = tuple(_MAGIC)) -> tuple[str, str, int]:
    """Validate type, magic bytes and size, then store under a random name. Returns (stored, safe_name, size)."""
    s = get_settings()
    ext = Path(file.filename or "").suffix.lower()
    if ext not in allowed:
        raise HTTPException(415, "Unsupported file type")
    cap_mb = s.max_video_mb if ext in _VIDEO_EXT else s.max_upload_mb
    data = file.file.read(cap_mb * 1024 * 1024 + 1)
    if len(data) > cap_mb * 1024 * 1024:
        raise HTTPException(413, f"File larger than {cap_mb} MB")
    if not _magic_ok(ext, data):
        raise HTTPException(415, "File content does not match its type")
    folder = Path(s.upload_dir) / subdir
    folder.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid.uuid4().hex}{ext}"
    (folder / stored).write_bytes(data)
    return stored, re.sub(r"[^A-Za-z0-9._-]", "_", file.filename or "file")[:80], len(data)


@app.post("/api/chat/upload", dependencies=[*api, Depends(limit("upload", 10, 60))])
def chat_upload(file: UploadFile = File(...), scope: str = Form("all"), kind: str = Form(""),
                booking_ref: str = Form("")):
    """Stores an owner upload. Contents are UNTRUSTED and are never read by any model.
    kind='receipt' = a publication receipt: attach it to a booking (given now, or named in the next chat message)."""
    if kind == "receipt":
        from .. import receipts
        stored, safe, size = _store_upload(file, "receipts", (".pdf", ".png", ".jpg", ".jpeg", ".webp"))
        post_message(scope, "owner", f"Uploaded publication receipt {safe}",
                     {"stored_as": stored, "name": safe, "size": size, "kind": "receipt"})
        if not booking_ref.strip():
            reply = "Got the receipt. Which booking is it for? Reply e.g. \"receipt for BOOK-20260101-001\"."
        else:
            try:
                r = receipts.link_latest_upload("adspot", booking_ref.strip())
                reply = (f"Receipt linked to {r['booking_ref']}. A customer mail draft is in the Mail tab "
                         + ("(auto-send is on)." if r["auto"] else "for you to review and send."))
            except (LookupError, ValueError) as e:
                reply = str(e)
        post_message(scope, "command", reply)
        return {"stored_as": stored, "reply": reply}
    stored, safe, size = _store_upload(file, "", (".pdf", ".png", ".jpg", ".jpeg", ".webp"))   # chat: no video
    data = {"size": size}
    post_message(scope, "owner", f"Uploaded {safe}", {"stored_as": stored, "name": safe, "size": size})
    reply = "Saved. Phase 2 will let me link receipts to a booking; nothing was sent or changed."
    post_message(scope, "command", reply)
    return {"stored_as": stored, "reply": reply}


# ---------------- approvals ----------------
class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject|edit)$")
    summary: str | None = Field(default=None, max_length=500)
    accept_mismatch: bool = False


@app.get("/api/approvals", dependencies=api)
def approvals(scope: str = "all", status: str = "pending"):
    return _each(scope, lambda s: s.list_approvals(status if status != "any" else None))


@app.post("/api/approvals/{slug}/{approval_id}", dependencies=[*api, Depends(limit("decide", 30, 60))])
def decide(slug: str, approval_id: int, body: DecisionIn):
    (co,) = _companies(slug)
    note = None
    with scope_by_id(co["id"], co["slug"]) as s:
        if body.decision == "edit":
            row = s.edit_approval_summary(approval_id, body.summary or "")
        else:
            cur = s.get_approval(approval_id)
            if cur and body.decision == "approve" and cur["payload"].get("mismatch") and not body.accept_mismatch:
                raise HTTPException(409, "The amount differs from the booking total. Tick 'amounts differ, confirm anyway' to continue.")
            row = s.decide_approval(approval_id, "approved" if body.decision == "approve" else "rejected", body.summary)
            if row and body.decision == "approve":
                try:
                    out = registry.execute_approved(s, approval_id, accept_mismatch=body.accept_mismatch)
                    res = out.get("result") or {}
                    note = " ".join(res.get("notes", [])) if isinstance(res, dict) else None
                    if note:
                        post_message(slug, "system", f"{row['summary']}\n" + "\n".join("- " + n for n in res["notes"]))
                except NotImplementedError:
                    note = "Approved. The executor for this action is not built yet; nothing was sent."
                except registry.ToolDenied as e:
                    raise HTTPException(409, str(e))
        if not row:
            raise HTTPException(409, "Approval not found or already decided")
    return {"ok": True, "note": note}


# ---------------- departments / tasks / activity / costs ----------------
@app.get("/api/departments", dependencies=api)
def departments(scope: str = "all"):
    def f(s):
        return [{**a, "spent_usd": s.month_spend(a["id"])} for a in s.list_agents()]
    return _each(scope, f)


class StatusIn(BaseModel):
    status: str = Field(pattern="^(active|paused)$")


class BudgetIn(BaseModel):
    monthly_budget_usd: float = Field(gt=0, le=1000)


@app.post("/api/departments/{slug}/{agent_id}/status", dependencies=api)
def set_status(slug: str, agent_id: int, body: StatusIn):
    (co,) = _companies(slug)
    with scope_by_id(co["id"], co["slug"]) as s:
        a = s.get_agent(agent_id)
        if not a:
            raise HTTPException(404, "No such agent")
        if a["status"] == "budget_exhausted":
            raise HTTPException(409, "Budget exhausted: raise the budget first")
        s.set_agent_status(agent_id, body.status)
    return {"ok": True}


@app.post("/api/departments/{slug}/{agent_id}/budget", dependencies=api)
def set_budget(slug: str, agent_id: int, body: BudgetIn):
    (co,) = _companies(slug)
    with scope_by_id(co["id"], co["slug"]) as s:
        a = s.get_agent(agent_id)
        if not a:
            raise HTTPException(404, "No such agent")
        s.set_agent_budget(agent_id, body.monthly_budget_usd)
        if a["status"] == "budget_exhausted" and s.month_spend(agent_id) < body.monthly_budget_usd:
            s.set_agent_status(agent_id, "paused")   # owner must explicitly resume
    return {"ok": True}


@app.get("/api/tasks", dependencies=api)
def tasks(scope: str = "all"):
    return _each(scope, lambda s: s.list_tasks())


@app.get("/api/activity", dependencies=api)
def activity(scope: str = "all"):
    def f(s):
        ev = [{"kind": "event", "label": e["type"], "detail": e["payload"], "at": e["created_at"]} for e in s.list_events(50)]
        tc = s.conn.execute("SELECT tool, tier, status, reason, created_at FROM tool_calls WHERE company_id=%s "
                            "ORDER BY id DESC LIMIT 50", (s.company_id,)).fetchall()
        ev += [{"kind": "tool_call", "label": f"{t['tool']} [{t['status']}]", "detail": t["reason"], "at": t["created_at"]} for t in tc]
        return ev
    return sorted(_each(scope, f), key=lambda r: r["at"], reverse=True)[:100]


@app.get("/api/costs", dependencies=api)
def costs(scope: str = "all"):
    return _each(scope, lambda s: s.cost_summary())


# ---------------- web push ----------------
class PushSub(BaseModel):
    endpoint: str
    keys: dict


@app.post("/api/push/subscribe", dependencies=api)
def push_subscribe(sub: PushSub):
    with db.admin() as a:
        a.execute("INSERT INTO push_subscriptions (endpoint, p256dh, auth) VALUES (%s,%s,%s) "
                  "ON CONFLICT (endpoint) DO UPDATE SET p256dh=EXCLUDED.p256dh, auth=EXCLUDED.auth",
                  (sub.endpoint, sub.keys.get("p256dh", ""), sub.keys.get("auth", "")))
    return {"ok": True}


# ---------------- content packs (manual publish) ----------------
class PackIn(BaseModel):
    company: str = Field(pattern="^(adspot|sidadiya)$")
    kind: str = Field(pattern="^(post|ad)$")
    topic: str = Field(min_length=3, max_length=300)
    image_mode: str = Field(default="flow_manual", pattern="^(flow_manual|gemini_image)$")


@app.get("/api/content", dependencies=api)
def content_list(scope: str = "all"):
    return _each(scope, lambda s: s.list_packs())


@app.post("/api/content", dependencies=[*api, Depends(limit("content", 10, 60))])
def content_create(body: PackIn):
    from .. import content
    try:
        return {"id": content.request_pack(body.company, body.kind, body.topic, body.image_mode)}
    except ValueError as e:
        raise HTTPException(422, str(e))
    except LookupError as e:
        raise HTTPException(409, str(e))


_PACK_MOVES = {"approved": ("draft", "needs_review"), "rejected": ("draft", "needs_review", "approved", "failed"),
               "posted": ("approved",)}


class PackStatus(BaseModel):
    status: str = Field(pattern="^(approved|rejected|posted)$")


@app.post("/api/content/{slug}/{pack_id}/status", dependencies=api)
def content_status(slug: str, pack_id: int, body: PackStatus):
    (co,) = _companies(slug)
    with scope_by_id(co["id"], co["slug"]) as s:
        if not s.move_pack(pack_id, body.status, _PACK_MOVES[body.status]):
            raise HTTPException(409, "That change is not allowed from the pack's current status")
    return {"ok": True}


@app.post("/api/content/{slug}/{pack_id}/image", dependencies=[*api, Depends(limit("upload", 10, 60))])
def content_image_upload(slug: str, pack_id: int, file: UploadFile = File(...)):
    """For flow_manual: attach the image you made in Google Flow."""
    (co,) = _companies(slug)
    stored, _, _ = _store_upload(file, "content", (".png", ".jpg", ".jpeg", ".webp"))
    with scope_by_id(co["id"], co["slug"]) as s:
        if s.get_pack(pack_id) is None:
            raise HTTPException(404, "No such pack")
        s.update_pack(pack_id, image_file=stored)
    return {"ok": True}


@app.get("/api/content/{slug}/{pack_id}/image", dependencies=api)
def content_image(slug: str, pack_id: int):
    (co,) = _companies(slug)
    with scope_by_id(co["id"], co["slug"]) as s:
        pack = s.get_pack(pack_id)
    if not pack or not pack["image_file"]:
        raise HTTPException(404, "No image")
    f = Path(get_settings().upload_dir) / "content" / Path(pack["image_file"]).name
    if not f.is_file():
        raise HTTPException(404, "Image file missing")
    return FileResponse(f, headers={"Content-Disposition": f'attachment; filename="{slug}-{pack_id}{f.suffix}"'})


class FactIn(BaseModel):
    key: str = Field(pattern="^[a-z0-9_]{2,40}$")
    value: str = Field(min_length=1, max_length=500)


@app.get("/api/facts", dependencies=api)
def facts(scope: str = "all"):
    return _each(scope, lambda s: s.list_facts())


@app.put("/api/facts/{slug}", dependencies=api)
def fact_put(slug: str, body: FactIn):
    from ..security.public_guard import problems
    if problems(body.value):
        raise HTTPException(422, "Facts are public: no emails, phone numbers or booking references")
    (co,) = _companies(slug)
    with scope_by_id(co["id"], co["slug"]) as s:
        s.upsert_fact(body.key, body.value)
    return {"ok": True}


# ---------------- Phase 2: bookings, payments, mail, publications, payouts, settings ----------------
import csv  # noqa: E402
import io  # noqa: E402
import re as _re  # noqa: E402

from .. import bookings as bk, finance, mailer, payments, receipts  # noqa: E402
from ..connectors import gmail as gmail_conn  # noqa: E402


@app.get("/api/bookings", dependencies=api)
def bookings_list(scope: str = "all"):
    return _each(scope, lambda s: bk.list_bookings(s))


@app.post("/api/bookings/{slug}/sync", dependencies=[*api, Depends(limit("sync", 6, 60))])
def bookings_sync(slug: str):
    (co,) = _companies(slug)
    if slug != "adspot":
        raise HTTPException(404, "Only AdSpot has bookings")
    with scope_by_id(co["id"], co["slug"]) as s:
        return {"synced": bk.sync_all(s)}


class PayIn(BaseModel):
    amount: float | None = Field(default=None, gt=0)
    method: str = Field(default="bank_transfer", pattern="^(bank_transfer|helapay)$")


@app.post("/api/bookings/{slug}/{ref}/mark-paid", dependencies=[*api, Depends(limit("pay", 20, 60))])
def booking_mark_paid(slug: str, ref: str, body: PayIn):
    """Creates the confirm card (bound to this booking + amount). Nothing is recorded or sent until it is confirmed."""
    if slug != "adspot":
        raise HTTPException(404, "Only AdSpot has bookings")
    return payments.request_mark_paid(slug, ref, body.amount, body.method)


@app.post("/api/bookings/{slug}/{ref}/publication-drafts", dependencies=api)
def booking_pub_drafts(slug: str, ref: str):
    (co,) = _companies(slug)
    with scope_by_id(co["id"], co["slug"]) as s:
        b = bk.get(s, ref)
        if not b:
            raise HTTPException(404, "No such booking")
        if b["state"] in ("enquiry", "awaiting_payment"):
            raise HTTPException(409, "Mark the booking paid first")
        return mailer.queue_publication_drafts(s, b)


@app.post("/api/bookings/{slug}/{ref}/receipt", dependencies=[*api, Depends(limit("upload", 10, 60))])
def booking_receipt(slug: str, ref: str, file: UploadFile = File(...)):
    stored, safe, size = _store_upload(file, "receipts", (".pdf", ".png", ".jpg", ".jpeg", ".webp"))
    try:
        return receipts.attach_receipt(slug, ref, stored)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))


class CloseIn(BaseModel):
    to: str = Field(pattern="^(published|closed)$")


@app.post("/api/bookings/{slug}/{ref}/state", dependencies=api)
def booking_state(slug: str, ref: str, body: CloseIn):
    (co,) = _companies(slug)
    with scope_by_id(co["id"], co["slug"]) as s:
        try:
            bk.transition(s, ref, body.to)
        except (bk.IllegalTransition, LookupError) as e:
            raise HTTPException(409, str(e))
    return {"ok": True}


@app.get("/api/mail", dependencies=api)
def mail_feed(scope: str = "adspot"):
    inbox = _each(scope, lambda s: s.conn.execute(
        "SELECT id, from_addr, subject, category, summary, booking_ref, link_confidence, thread_id, draft_id, created_at "
        "FROM mail_log WHERE company_id=%s AND direction='in' ORDER BY id DESC LIMIT 40", (s.company_id,)).fetchall())
    return {"gmail_configured": gmail_conn.configured(), "inbox": inbox, "outbox": _each(scope, lambda s: mailer.list_outbox(s)),
            "undo_window_seconds": get_settings().undo_window_seconds}


class MailEdit(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20000)


def _mail_op(slug: str, fn):
    (co,) = _companies(slug)
    try:
        with scope_by_id(co["id"], co["slug"]) as s:
            return fn(s, co)
    except mailer.MailError as e:
        raise HTTPException(409, str(e))


@app.put("/api/mail/{slug}/{mail_id}", dependencies=api)
def mail_edit(slug: str, mail_id: int, body: MailEdit):
    _mail_op(slug, lambda s, co: mailer.update_draft(s, mail_id, body.subject, body.body))
    return {"ok": True}


@app.post("/api/mail/{slug}/{mail_id}/undo", dependencies=api)
def mail_undo(slug: str, mail_id: int):
    _mail_op(slug, lambda s, co: mailer.undo(s, mail_id))
    return {"ok": True}


@app.post("/api/mail/{slug}/{mail_id}/reschedule", dependencies=api)
def mail_reschedule(slug: str, mail_id: int):
    """Put a cancelled/failed CUSTOMER mail back in the queue (new undo window)."""
    def f(s, co):
        r = s.conn.execute("UPDATE mail_outbox SET status='scheduled', error=NULL, send_after=%s WHERE id=%s AND company_id=%s "
                           "AND kind IN ('customer_payment','customer_receipt') AND status IN ('cancelled','failed') RETURNING id",
                           (mailer._undo_deadline(), mail_id, s.company_id)).fetchone()
        if not r:
            raise mailer.MailError("only a cancelled or failed customer mail can be rescheduled")
    _mail_op(slug, f)
    return {"ok": True}


@app.post("/api/mail/{slug}/{mail_id}/send", dependencies=[*api, Depends(limit("send", 20, 60))])
def mail_send(slug: str, mail_id: int):
    """The owner pressing Send on a draft is the explicit command. Recipient rules are enforced in mailer."""
    (co,) = _companies(slug)
    if not gmail_conn.configured():
        raise HTTPException(409, "Gmail is not configured on the server yet")
    r = mailer.send_outbox(co["id"], co["slug"], mail_id, "owner-button")
    if r["status"] != "sent":
        raise HTTPException(409, f"Not sent: {r.get('reason', r['status'])}")
    return r


class PubIn(BaseModel):
    group_ref: str = Field(pattern="^[A-Za-z0-9_-]{1,60}$")
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(default="", max_length=200)
    active: bool = True


@app.get("/api/publications", dependencies=api)
def publications_list(scope: str = "adspot"):
    """Saved publications plus any AdSpot publication groups that still need an address (listed with an empty email)."""
    def f(s):
        have = s.conn.execute("SELECT group_ref, name, email, active FROM publications WHERE company_id=%s ORDER BY name",
                              (s.company_id,)).fetchall()
        known = {h["group_ref"] for h in have}
        extra = []
        if s.slug == "adspot":
            try:
                from ..connectors import adspot as _ad
                seen = {}
                for v in _ad.newspaper_groups().values():
                    if v["group_ref"] and v["group_ref"] not in known:
                        seen[v["group_ref"]] = {"group_ref": v["group_ref"], "name": v["group_name"] or v["group_ref"], "email": "", "active": True}
                extra = list(seen.values())
            except Exception:
                pass
        return have + extra
    return _each(scope, f)


@app.put("/api/publications/{slug}", dependencies=api)
def publications_put(slug: str, body: PubIn):
    """The ONLY way a publication address enters the system: typed here by the owner, never taken from mail content."""
    if body.email and not _re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", body.email):
        raise HTTPException(422, "That does not look like an email address")
    (co,) = _companies(slug)
    with scope_by_id(co["id"], co["slug"]) as s:
        s.conn.execute("INSERT INTO publications (company_id, group_ref, name, email, active) VALUES (%s,%s,%s,%s,%s) "
                       "ON CONFLICT (company_id, group_ref) DO UPDATE SET name=EXCLUDED.name, email=EXCLUDED.email, active=EXCLUDED.active",
                       (co["id"], body.group_ref, body.name, body.email.strip(), body.active))
    return {"ok": True}


@app.get("/api/payouts", dependencies=api)
def payouts(scope: str = "adspot"):
    return _each(scope, lambda s: finance.batches(s))


@app.get("/api/payouts/{slug}/{batch_id}.csv", dependencies=api)
def payout_csv(slug: str, batch_id: int):
    from fastapi.responses import PlainTextResponse
    (co,) = _companies(slug)
    with scope_by_id(co["id"], co["slug"]) as s:
        b = next((x for x in finance.batches(s) if x["id"] == batch_id), None)
    if not b:
        raise HTTPException(404, "No such batch")
    return PlainTextResponse(finance.to_csv(b), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="payout-{slug}-{batch_id}.csv"'})


@app.post("/api/payouts/{slug}/{batch_id}/mark-paid", dependencies=api)
def payout_paid(slug: str, batch_id: int):
    """Records that YOU paid the publications by bank transfer. AgentCo moves no money."""
    (co,) = _companies(slug)
    with scope_by_id(co["id"], co["slug"]) as s:
        try:
            return {"recorded": finance.mark_batch_paid_manually(s, batch_id, "owner")}
        except ValueError as e:
            raise HTTPException(409, str(e))


_SETTINGS = {"auto_send_receipts", "send_payment_confirmation", "receipt_also_reply_on_publication_thread"}


class SettingIn(BaseModel):
    value: str = Field(pattern="^(true|false)$")


@app.get("/api/settings/{slug}", dependencies=api)
def settings_get(slug: str):
    (co,) = _companies(slug)
    with scope_by_id(co["id"], co["slug"]) as s:
        return {k: s.get_setting(k, "false") for k in sorted(_SETTINGS)}


@app.put("/api/settings/{slug}/{key}", dependencies=api)
def settings_put(slug: str, key: str, body: SettingIn):
    if key not in _SETTINGS:
        raise HTTPException(404, "Unknown setting")
    (co,) = _companies(slug)
    with scope_by_id(co["id"], co["slug"]) as s:
        s.set_setting(key, body.value)
    return {"ok": True}


# ---------------- YouTube Shorts ----------------
from ..youtube import client as yt_client, projects as vp, tts as yt_tts, uploader as yt_uploader, veo as yt_veo  # noqa: E402


def _video_op(fn, *, not_found="No such video project"):
    try:
        return fn()
    except LookupError as e:
        raise HTTPException(404, str(e) or not_found)
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.get("/api/video", dependencies=api)
def video_list(scope: str = "all"):
    st = get_settings()
    return {"projects": _each(scope, lambda s: vp.list_projects(s)),
            "capabilities": {"tts": yt_tts.available(), "veo": yt_veo.available(), "max_uploads_per_day": st.youtube_max_uploads_per_day,
                             "youtube": {c: yt_client.configured(c) for c in ("adspot", "sidadiya")}}}


class VideoIn(BaseModel):
    company: str = Field(pattern="^(adspot|sidadiya)$")
    topic: str = Field(min_length=3, max_length=300)
    voice_mode: str = Field(default="tts", pattern="^(tts|none)$")
    clip_mode: str = Field(default="flow_manual", pattern="^(flow_manual|veo_api)$")


@app.post("/api/video", dependencies=[*api, Depends(limit("video", 10, 60))])
def video_create(body: VideoIn):
    try:
        return {"id": vp.request_project(body.company, body.topic, body.voice_mode, body.clip_mode)}
    except ValueError as e:
        raise HTTPException(422, str(e))
    except LookupError as e:
        raise HTTPException(409, str(e))


class ShotIn(BaseModel):
    spoken: str = Field(min_length=3, max_length=300)
    on_screen: str = Field(default="", max_length=90)
    visual_prompt: str = Field(min_length=5, max_length=600)


class MetaIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4800)
    tags: list[str] = Field(default_factory=list, max_length=20)


def _vscope(slug: str, fn):
    (co,) = _companies(slug)
    with scope_by_id(co["id"], co["slug"]) as s:
        return _video_op(lambda: fn(s))


@app.put("/api/video/{slug}/{pid}/shots/{beat}", dependencies=api)
def video_shot(slug: str, pid: int, beat: int, body: ShotIn):
    _vscope(slug, lambda s: vp.update_shot(s, pid, beat, body.spoken, body.on_screen, body.visual_prompt))
    return {"ok": True}


@app.put("/api/video/{slug}/{pid}/meta", dependencies=api)
def video_meta(slug: str, pid: int, body: MetaIn):
    _vscope(slug, lambda s: vp.update_meta(s, pid, body.title, body.description, body.tags))
    return {"ok": True}


@app.post("/api/video/{slug}/{pid}/approve-script", dependencies=api)
def video_approve(slug: str, pid: int):
    _companies(slug)
    return _video_op(lambda: vp.approve_script(slug, pid))


@app.post("/api/video/{slug}/{pid}/shots/{beat}/asset", dependencies=[*api, Depends(limit("upload", 20, 60))])
def video_asset(slug: str, pid: int, beat: int, file: UploadFile = File(...)):
    """Attach the clip/image you made in Google Flow (or anywhere) to a beat."""
    (co,) = _companies(slug)
    ext = Path(file.filename or "").suffix.lower()
    stored, _, _ = _store_upload(file, f"video/{int(pid)}", (".mp4", ".mov", ".webm", ".png", ".jpg", ".jpeg", ".webp"))
    from ..youtube import render as yt_render
    path = vp.media_dir(pid) / stored
    kind = "video" if ext in _VIDEO_EXT else "image"
    try:
        yt_render.check_asset(path, kind)                                  # decode check before it is accepted
    except yt_render.RenderError as e:
        path.unlink(missing_ok=True)
        raise HTTPException(422, str(e))
    with scope_by_id(co["id"], co["slug"]) as s:
        _video_op(lambda: vp.attach_asset(s, pid, beat, stored, kind))
    return {"ok": True}


@app.post("/api/video/{slug}/{pid}/retry-assets", dependencies=[*api, Depends(limit("video", 10, 60))])
def video_retry(slug: str, pid: int):
    """Resume voiceover/clip generation after raising the budget or after a failed clip."""
    _companies(slug)
    return _video_op(lambda: vp.retry_assets(slug, pid))


class RenderIn(BaseModel):
    allow_placeholders: bool = False


@app.post("/api/video/{slug}/{pid}/render", dependencies=[*api, Depends(limit("render", 10, 60))])
def video_render(slug: str, pid: int, body: RenderIn):
    _companies(slug)
    _video_op(lambda: vp.request_render(slug, pid, body.allow_placeholders))
    return {"ok": True}


@app.post("/api/video/{slug}/{pid}/reject", dependencies=api)
def video_reject(slug: str, pid: int):
    _vscope(slug, lambda s: vp.reject(s, pid))
    return {"ok": True}


@app.get("/api/video/{slug}/{pid}/file", dependencies=api)
def video_file(slug: str, pid: int):
    return _video_media(slug, pid, "final.mp4", "video/mp4")


@app.get("/api/video/{slug}/{pid}/preview.jpg", dependencies=api)
def video_preview(slug: str, pid: int):
    return _video_media(slug, pid, "preview.jpg", "image/jpeg")


def _video_media(slug: str, pid: int, name: str, mime: str):
    (co,) = _companies(slug)
    with scope_by_id(co["id"], co["slug"]) as s:
        p = vp.get(s, pid)
    f = vp.media_dir(pid) / "render" / name                                   # fixed names: nothing user-supplied in the path
    if not p or not p["render_file"] or not f.is_file():
        raise HTTPException(404, "Not rendered yet")
    return FileResponse(f, media_type=mime)


@app.post("/api/video/{slug}/{pid}/upload", dependencies=[*api, Depends(limit("yt_upload", 6, 60))])
def video_upload(slug: str, pid: int):
    """The owner's explicit Upload button. Always PRIVATE; you publish it in YouTube Studio."""
    (co,) = _companies(slug)
    r = yt_uploader.upload_project(co["id"], co["slug"], pid, "owner-button")
    if r["status"] != "uploaded":
        raise HTTPException(409, r.get("reason", r["status"]))
    return r


# ---------------- PWA static files ----------------
_dist = Path(__file__).resolve().parents[2] / "web" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = (_dist / path).resolve()
        if path and f.is_file() and _dist in f.parents:
            return FileResponse(f)
        return FileResponse(_dist / "index.html")
