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


_MAGIC = {".pdf": b"%PDF", ".png": b"\x89PNG", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff", ".webp": b"RIFF"}


def _store_upload(file: UploadFile, subdir: str = "", allowed: tuple[str, ...] = tuple(_MAGIC)) -> tuple[str, str, int]:
    """Validate type, magic bytes and size, then store under a random name. Returns (stored, safe_name, size)."""
    s = get_settings()
    ext = Path(file.filename or "").suffix.lower()
    if ext not in allowed:
        raise HTTPException(415, "Unsupported file type")
    data = file.file.read(s.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > s.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File larger than {s.max_upload_mb} MB")
    if not data.startswith(_MAGIC[ext]):
        raise HTTPException(415, "File content does not match its type")
    folder = Path(s.upload_dir) / subdir
    folder.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid.uuid4().hex}{ext}"
    (folder / stored).write_bytes(data)
    return stored, re.sub(r"[^A-Za-z0-9._-]", "_", file.filename or "file")[:80], len(data)


@app.post("/api/chat/upload", dependencies=[*api, Depends(limit("upload", 10, 60))])
def chat_upload(file: UploadFile = File(...), scope: str = Form("all")):
    """Stores an owner upload (receipt, camera photo). Contents are UNTRUSTED and not read by any model in Phase 1."""
    stored, safe, size = _store_upload(file)
    data = {"size": size}
    post_message(scope, "owner", f"Uploaded {safe}", {"stored_as": stored, "name": safe, "size": size})
    reply = "Saved. Phase 2 will let me link receipts to a booking; nothing was sent or changed."
    post_message(scope, "command", reply)
    return {"stored_as": stored, "reply": reply}


# ---------------- approvals ----------------
class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject|edit)$")
    summary: str | None = Field(default=None, max_length=500)


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
            row = s.decide_approval(approval_id, "approved" if body.decision == "approve" else "rejected", body.summary)
            if row and body.decision == "approve":
                try:
                    registry.execute_approved(s, approval_id)
                except NotImplementedError:
                    note = "Approved. The executor for this action is not built yet (Phase 2/3); nothing was sent."
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
