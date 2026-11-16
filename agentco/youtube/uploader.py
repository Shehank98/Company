"""Upload a rendered Short to YouTube as PRIVATE. At most once per project: claim -> send -> record, never auto-retried.
Called by the owner's Upload button, or by execute_approved() for an agent-requested (HIGH) upload."""
from __future__ import annotations

import logging

from .. import db
from ..config import get_settings
from ..notify import post_message
from ..scope import scope_by_id
from . import client, projects

log = logging.getLogger("agentco.youtube")


def upload_project(company_id: int, slug: str, pid: int, actor: str) -> dict:
    s_ = get_settings()
    if not client.configured(slug):
        return {"status": "failed", "reason": f"YouTube is not connected for {slug} yet (see docs/youtube.md)"}
    with scope_by_id(company_id, slug) as s:                                # tx 1: checks + claim (committed before the network call)
        p = projects.get(s, pid)
        if p is None:
            return {"status": "failed", "reason": "no such project"}
        if p["status"] != "review" or not p["render_file"]:
            return {"status": "failed", "reason": f"the video is '{p['status']}', render and review it first"}
        today = s.conn.execute("SELECT COUNT(*) AS n FROM youtube_uploads WHERE company_id=%s AND status IN ('claimed','done') "
                               "AND created_at >= date_trunc('day', now() AT TIME ZONE %s) AT TIME ZONE %s",
                               (company_id, s_.timezone, s_.timezone)).fetchone()["n"]
        if today >= s_.youtube_max_uploads_per_day:
            return {"status": "failed", "reason": f"daily upload cap of {s_.youtube_max_uploads_per_day} reached (YouTube API quota)"}
        try:
            meta = client.clean_meta(p["title"], p["description"], p["tags"])
        except client.MetaError as e:
            return {"status": "failed", "reason": str(e)}
        claim = s.conn.execute(
            "INSERT INTO youtube_uploads (company_id, project_id, status) VALUES (%s,%s,'claimed') "
            "ON CONFLICT (project_id) DO UPDATE SET status='claimed', error=NULL, updated_at=now() "
            "WHERE youtube_uploads.status='failed' RETURNING id", (company_id, pid)).fetchone()
        if not claim:
            cur = s.conn.execute("SELECT status, url FROM youtube_uploads WHERE project_id=%s", (pid,)).fetchone()
            return {"status": "skipped", "reason": f"already {cur['status']}" + (f": {cur['url']}" if cur and cur["url"] else "")}
        path = projects.safe_file(pid, p["render_file"])
    try:
        out = client.get_client(slug).upload(path=str(path), title=meta["title"], description=meta["description"],
                                             tags=meta["tags"], synthetic=s_.youtube_synthetic_disclosure)
    except Exception as e:                                                  # tx 2: record failure; the owner decides on a retry
        with scope_by_id(company_id, slug) as s:
            s.conn.execute("UPDATE youtube_uploads SET status='failed', error=%s, updated_at=now() WHERE project_id=%s",
                           (f"{type(e).__name__}"[:200], pid))
        log.warning("youtube upload failed: %s", type(e).__name__)
        return {"status": "failed", "reason": f"YouTube refused or failed: {type(e).__name__}"}
    with scope_by_id(company_id, slug) as s:
        s.conn.execute("UPDATE youtube_uploads SET status='done', video_id=%s, url=%s, updated_at=now() WHERE project_id=%s",
                       (out["id"], out["url"], pid))
        s.conn.execute("UPDATE video_projects SET status='uploaded', updated_at=now() WHERE id=%s AND company_id=%s", (pid, company_id))
        s.emit("video.uploaded", {"project_id": pid, "video_id": out["id"]}, "video")
    post_message(slug, "system", f"Short #{pid} is on YouTube as PRIVATE: {out['url']}\nOpen YouTube Studio > Content and set it to Public when you are happy. AgentCo never publishes it for you.")
    return {"status": "uploaded", "video_id": out["id"], "url": out["url"], "by": actor}
