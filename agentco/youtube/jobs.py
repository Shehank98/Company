"""Background jobs for the Video Producer: script, voiceover, Veo clips (poll + requeue), render."""
from __future__ import annotations

from .. import db
from ..agents import budget
from ..config import get_settings
from ..llm.base import LLMError
from ..models import DataClass
from ..orchestrator import queue
from ..scope import CompanyScope
from . import projects, render, script, tts, veo

MAX_POLLS = 60        # x15 s = 15 minutes per clip


def _cost(scope, agent, run_id, provider, model, task, usd):
    scope.add_cost(agent_id=agent["id"], run_id=run_id, provider=provider, model=model, route="video", data_class=DataClass.PUBLIC,
                   task=task, tokens_in=0, tokens_out=0, cost_usd=usd)


def _fail(scope, pid, msg):
    scope.conn.execute("UPDATE video_projects SET status='failed', error=%s, updated_at=now() WHERE id=%s AND company_id=%s",
                       (msg[:300], pid, scope.company_id))


def budget_stopped(scope: CompanyScope, job: dict) -> None:
    """Called whenever the budget stops a video job (by the runner's pre-check or mid-job). The project is kept."""
    pid, kind = int(job["payload"]["project_id"]), job["kind"]
    msg = "The Video Producer's monthly budget is used up. Raise it on the Departments tab"
    if kind in ("video_script", "video_render"):
        _fail(scope, pid, msg + ".")
    else:                           # voice/clips: keep the project; raise the budget, then press Retry
        scope.conn.execute("UPDATE video_shots SET error=%s WHERE project_id=%s AND company_id=%s AND asset_file IS NULL",
                           (msg + ", then press Retry.", pid, scope.company_id))


def run(scope: CompanyScope, agent: dict, run_id: int, job: dict, company_name: str) -> str | None:
    pid = int(job["payload"]["project_id"])
    kind = job["kind"]
    try:
        if kind == "video_script":
            return script.build(scope, agent, run_id, pid, company_name)
        if kind == "video_voice":
            return voice(scope, agent, run_id, pid)
        if kind == "video_clip":
            return clip(scope, agent, run_id, job, pid)
        if kind == "video_render":
            return do_render(scope, pid, bool(job["payload"].get("allow_placeholders")), company_name)
        raise ValueError(f"unknown video job {kind}")
    except budget.BudgetExceeded:
        budget_stopped(scope, job)
        raise
    except Exception as e:
        if kind in ("video_script", "video_render"):
            _fail(scope, pid, f"{e}")
        raise


def voice(scope, agent, run_id, pid) -> None:
    s = get_settings()
    for sh in projects.shots(scope, pid):
        if sh["voice_file"]:
            continue
        budget.ensure(scope, agent)
        out = projects.media_dir(pid) / f"voice_{sh['beat_no']}.wav"
        try:
            r = tts.synthesize(sh["spoken"], out)
        except LLMError as e:
            scope.conn.execute("UPDATE video_shots SET error=%s WHERE id=%s AND company_id=%s", (str(e)[:200], sh["id"], scope.company_id))
            raise
        _cost(scope, agent, run_id, r.provider, r.model, "tts", r.cost_usd)
        scope.conn.execute("UPDATE video_shots SET voice_file=%s, duration_s=%s, error=NULL WHERE id=%s AND company_id=%s",
                           (out.name, round(r.seconds + render.VOICE_TAIL, 2), sh["id"], scope.company_id))
    return None


def clip(scope, agent, run_id, job, pid) -> None:
    beat, attempt, nonce = int(job["payload"]["beat_no"]), int(job["payload"].get("attempt", 0)), int(job["payload"].get("nonce", 0))
    sh = scope.conn.execute("SELECT * FROM video_shots WHERE project_id=%s AND beat_no=%s AND company_id=%s", (pid, beat, scope.company_id)).fetchone()
    if sh is None or sh["status"] == "ready":
        return None

    def again():
        if attempt >= MAX_POLLS:
            scope.conn.execute("UPDATE video_shots SET status='failed', error='Veo took too long' WHERE id=%s AND company_id=%s", (sh["id"], scope.company_id))
            return
        with db.admin() as a:
            queue.enqueue(a, company_id=scope.company_id, agent_id=agent["id"], kind="video_clip",
                          payload={"project_id": pid, "beat_no": beat, "attempt": attempt + 1, "nonce": nonce},
                          dedupe_key=f"clip:{pid}:{beat}:{nonce}:{attempt + 1}", delay_seconds=15)
    try:
        if not sh["op_name"]:
            budget.ensure(scope, agent)
            op = veo.start(sh["visual_prompt"])
            scope.conn.execute("UPDATE video_shots SET op_name=%s, status='generating' WHERE id=%s AND company_id=%s", (op, sh["id"], scope.company_id))
            again()
            return None
        out = projects.media_dir(pid) / f"clip_{beat}.mp4"
        if veo.poll(sh["op_name"], out) == "pending":
            again()
            return None
    except LLMError as e:
        scope.conn.execute("UPDATE video_shots SET status='failed', error=%s WHERE id=%s AND company_id=%s", (str(e)[:200], sh["id"], scope.company_id))
        return None
    _cost(scope, agent, run_id, "gemini", get_settings().veo_model or "fake-veo", "veo_clip", veo.CLIP_SECONDS * get_settings().veo_price_per_second_usd)
    scope.conn.execute("UPDATE video_shots SET asset_file=%s, asset_kind='video', source='veo', status='ready', error=NULL WHERE id=%s AND company_id=%s",
                       (out.name, sh["id"], scope.company_id))
    return None


def do_render(scope, pid, allow_placeholders, company_name) -> str:
    items = []
    for sh in projects.shots(scope, pid):
        items.append({"beat_no": sh["beat_no"], "spoken": sh["spoken"], "on_screen": sh["on_screen"],
                      "asset": projects.safe_file(pid, sh["asset_file"]), "asset_kind": sh["asset_kind"],
                      "voice": projects.safe_file(pid, sh["voice_file"])})
    res = render.render(projects.media_dir(pid) / "render", items, allow_placeholders)
    scope.conn.execute("UPDATE video_projects SET status='review', render_file='render/final.mp4', preview_file='render/preview.jpg', "
                       "duration_s=%s, error=NULL, updated_at=now() WHERE id=%s AND company_id=%s", (round(res.duration, 2), pid, scope.company_id))
    scope.emit("video.rendered", {"project_id": pid}, "video")
    return f"{company_name}: Short #{pid} is rendered ({res.duration:.0f}s). Watch it in Content > Shorts, then upload."
