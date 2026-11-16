"""Video project state + the registry handler for the agent's save_video_script tool."""
from __future__ import annotations

from pathlib import Path

from psycopg.types.json import Jsonb

from .. import db
from ..config import get_settings
from ..orchestrator import queue
from ..scope import CompanyScope, company_scope
from ..security.public_guard import assert_public_safe
from . import client, tts, veo
from .render import MIN_BEAT, WPS


def media_dir(pid: int) -> Path:
    return Path(get_settings().upload_dir) / "video" / str(int(pid))


def safe_file(pid: int, name: str | None) -> Path | None:
    return (media_dir(pid) / Path(name).name) if name else None


def _enqueue(company_id: int, agent_id: int, kind: str, payload: dict, key: str | None = None, delay: int = 0) -> None:
    with db.admin() as a:
        queue.enqueue(a, company_id=company_id, agent_id=agent_id, kind=kind, payload=payload, dedupe_key=key, delay_seconds=delay)


def request_project(slug: str, topic: str, voice_mode: str = "tts", clip_mode: str = "flow_manual") -> int:
    topic = (topic or "").strip()
    if not 3 <= len(topic) <= 300:
        raise ValueError("topic must be 3-300 characters")
    if voice_mode not in ("tts", "none") or clip_mode not in ("flow_manual", "veo_api"):
        raise ValueError("bad voice or clip mode")
    assert_public_safe(topic, "topic")
    if voice_mode == "tts" and not tts.available():
        raise ValueError("AI voiceover is not set up (GEMINI_TTS_MODEL). Choose 'no voiceover' or add the model id")
    if clip_mode == "veo_api" and not veo.available():
        raise ValueError("Veo is not set up (VEO_MODEL). Use Google Flow by hand, or add the model id")
    with company_scope(slug) as s:
        ag = next((a for a in s.list_agents() if a["role"] == "video"), None)
        if ag is None or ag["status"] != "active":
            raise LookupError("The Video Producer agent is paused. Resume it on the Departments tab.")
        pid = s.conn.execute("INSERT INTO video_projects (company_id, topic, voice_mode, clip_mode, created_by) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                             (s.company_id, topic, voice_mode, clip_mode, ag["id"])).fetchone()["id"]
        cid, aid = s.company_id, ag["id"]
    _enqueue(cid, aid, "video_script", {"project_id": pid})
    return pid


def save_script(scope: CompanyScope, args: dict, ctx: dict) -> dict:
    """Registry DRAFT tool. Only fills a project that is still drafting; always lands in script_review."""
    pid = int(args["project_id"])
    p = scope.conn.execute("SELECT status FROM video_projects WHERE id=%s AND company_id=%s", (pid, scope.company_id)).fetchone()
    if p is None or p["status"] not in ("drafting_script", "failed"):
        raise ValueError("this project is not waiting for a script")
    scope.conn.execute(
        "UPDATE video_projects SET status='script_review', hook=%s, hook_options=%s, title=%s, description=%s, tags=%s, "
        "claims_check=%s, error=NULL, run_id=%s, updated_at=now() WHERE id=%s AND company_id=%s",
        (Jsonb(args["hook"]), Jsonb(args["hook_options"]), str(args["title"])[:100], str(args.get("description", ""))[:1500],
         Jsonb(list(args.get("tags", []))[:12]), Jsonb(args["claims_check"]), ctx.get("run_id"), pid, scope.company_id))
    for i, b in enumerate(args["beats"], start=1):
        est = max(MIN_BEAT, len(str(b["spoken"]).split()) / WPS + 0.4)
        scope.conn.execute(
            "INSERT INTO video_shots (company_id, project_id, beat_no, spoken, on_screen, visual_prompt, duration_s) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (project_id, beat_no) DO UPDATE SET spoken=EXCLUDED.spoken, "
            "on_screen=EXCLUDED.on_screen, visual_prompt=EXCLUDED.visual_prompt, duration_s=EXCLUDED.duration_s",
            (scope.company_id, pid, i, b["spoken"], b.get("on_screen", ""), b["visual_prompt"], round(est, 2)))
    return {"project_id": pid, "beats": len(args["beats"])}


def get(scope: CompanyScope, pid: int) -> dict | None:
    return scope.conn.execute("SELECT * FROM video_projects WHERE id=%s AND company_id=%s", (pid, scope.company_id)).fetchone()


def shots(scope: CompanyScope, pid: int) -> list[dict]:
    return scope.conn.execute("SELECT * FROM video_shots WHERE project_id=%s AND company_id=%s ORDER BY beat_no",
                              (pid, scope.company_id)).fetchall()


def list_projects(scope: CompanyScope, limit: int = 30) -> list[dict]:
    out = []
    for p in scope.conn.execute("SELECT * FROM video_projects WHERE company_id=%s ORDER BY id DESC LIMIT %s", (scope.company_id, limit)).fetchall():
        up = scope.conn.execute("SELECT status, video_id, url, privacy, error FROM youtube_uploads WHERE project_id=%s AND company_id=%s",
                                (p["id"], scope.company_id)).fetchone()
        out.append({**p, "shots": shots(scope, p["id"]), "upload": up})
    return out


def _need(scope: CompanyScope, pid: int, allowed: tuple[str, ...]) -> dict:
    p = get(scope, pid)
    if p is None:
        raise LookupError("no such video project")
    if p["status"] not in allowed:
        raise ValueError(f"not allowed while the project is '{p['status']}'")
    return p


def update_shot(scope: CompanyScope, pid: int, beat: int, spoken: str, on_screen: str, visual_prompt: str) -> None:
    _need(scope, pid, ("script_review",))
    for what, t in (("spoken line", spoken), ("on-screen text", on_screen), ("visual prompt", visual_prompt)):
        assert_public_safe(t, what)
    n = scope.conn.execute("UPDATE video_shots SET spoken=%s, on_screen=%s, visual_prompt=%s, duration_s=%s WHERE project_id=%s AND beat_no=%s AND company_id=%s",
                           (spoken[:300], on_screen[:90], visual_prompt[:600], round(max(MIN_BEAT, len(spoken.split()) / WPS + 0.4), 2),
                            pid, beat, scope.company_id)).rowcount
    if not n:
        raise LookupError("no such beat")


def update_meta(scope: CompanyScope, pid: int, title: str, description: str, tags: list[str]) -> None:
    _need(scope, pid, ("script_review", "assets", "review"))
    m = client.clean_meta(title, description, tags)
    assert_public_safe(m["title"] + " " + m["description"], "title/description")
    scope.conn.execute("UPDATE video_projects SET title=%s, description=%s, tags=%s, updated_at=now() WHERE id=%s AND company_id=%s",
                       (m["title"], m["description"], Jsonb(m["tags"]), pid, scope.company_id))


def approve_script(slug: str, pid: int) -> dict:
    with company_scope(slug) as s:
        p = _need(s, pid, ("script_review",))
        total = sum(len(x["spoken"].split()) for x in shots(s, pid))
        if total > 130:
            raise ValueError(f"the script has {total} spoken words; keep it under about 125 so it fits in a Short")
        s.conn.execute("UPDATE video_projects SET status='assets', updated_at=now() WHERE id=%s AND company_id=%s", (pid, s.company_id))
        cid, aid, beats = s.company_id, p["created_by"], [x["beat_no"] for x in shots(s, pid)]
        voice, clip = p["voice_mode"], p["clip_mode"]
    if voice == "tts":
        _enqueue(cid, aid, "video_voice", {"project_id": pid}, key=f"voice:{pid}")
    if clip == "veo_api":
        for b in beats:
            _enqueue(cid, aid, "video_clip", {"project_id": pid, "beat_no": b, "attempt": 0, "nonce": 0}, key=f"clip:{pid}:{b}:0:0")
    return {"voice": voice, "clip": clip, "beats": len(beats)}


def retry_assets(slug: str, pid: int) -> dict:
    """After a budget stop or a failed clip: queue only what is still missing. A Veo clip already started is polled, not paid twice."""
    import time
    nonce = int(time.time())
    with company_scope(slug) as s:
        p = _need(s, pid, ("assets",))
        sh = shots(s, pid)
        cid, aid = s.company_id, p["created_by"]
        need_voice = p["voice_mode"] == "tts" and any(not x["voice_file"] for x in sh)
        need_clips = [x["beat_no"] for x in sh if p["clip_mode"] == "veo_api" and not x["asset_file"]]
        s.conn.execute("UPDATE video_shots SET error=NULL, status=CASE WHEN status='failed' THEN 'pending' ELSE status END "
                       "WHERE project_id=%s AND company_id=%s AND asset_file IS NULL", (pid, s.company_id))
    if need_voice:
        _enqueue(cid, aid, "video_voice", {"project_id": pid}, key=f"voice:{pid}:r{nonce}")
    for b in need_clips:
        _enqueue(cid, aid, "video_clip", {"project_id": pid, "beat_no": b, "attempt": 0, "nonce": nonce}, key=f"clip:{pid}:{b}:{nonce}:0")
    return {"voice": need_voice, "clips": need_clips}


def attach_asset(scope: CompanyScope, pid: int, beat: int, stored: str, kind: str) -> None:
    _need(scope, pid, ("assets", "review"))
    n = scope.conn.execute("UPDATE video_shots SET asset_file=%s, asset_kind=%s, source=%s, status='ready', error=NULL "
                           "WHERE project_id=%s AND beat_no=%s AND company_id=%s",
                           (stored, kind, "flow_upload" if kind == "video" else "owner_image", pid, beat, scope.company_id)).rowcount
    if not n:
        raise LookupError("no such beat")
    scope.conn.execute("UPDATE video_projects SET status='assets', render_file=NULL, preview_file=NULL, updated_at=now() "
                       "WHERE id=%s AND company_id=%s", (pid, scope.company_id))     # a changed picture invalidates the old render


def request_render(slug: str, pid: int, allow_placeholders: bool) -> None:
    with company_scope(slug) as s:
        p = _need(s, pid, ("assets", "review", "failed"))
        sh = shots(s, pid)
        if not sh:
            raise ValueError("this project has no script yet")
        if p["voice_mode"] == "tts" and any(not x["voice_file"] for x in sh):
            raise ValueError("the voiceover is still being made; try again in a minute")
        if not allow_placeholders and any(not x["asset_file"] for x in sh):
            missing = [str(x["beat_no"]) for x in sh if not x["asset_file"]]
            raise ValueError(f"beats still without a picture: {', '.join(missing)} (add them, or allow placeholder cards)")
        s.conn.execute("UPDATE video_projects SET status='rendering', error=NULL, updated_at=now() WHERE id=%s AND company_id=%s", (pid, s.company_id))
        cid, aid = s.company_id, p["created_by"]
    _enqueue(cid, aid, "video_render", {"project_id": pid, "allow_placeholders": bool(allow_placeholders)})


def reject(scope: CompanyScope, pid: int) -> None:
    _need(scope, pid, ("drafting_script", "script_review", "assets", "review", "failed"))
    scope.conn.execute("UPDATE video_projects SET status='rejected', updated_at=now() WHERE id=%s AND company_id=%s", (pid, scope.company_id))
