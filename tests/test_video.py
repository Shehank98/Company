import json
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from agentco import db, worker
from agentco.api import auth
from agentco.api.main import app
from agentco.config import get_settings
from agentco.llm import fake
from agentco.scope import company_scope
from agentco.tools import registry
from agentco.youtube import client as yt, projects, render, script
from agentco.youtube.vendor.yt_skill import hookscore
from conftest import agent
from fake_youtube import FakeYouTube
from test_api import login

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def media(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    yt.set_client("adspot", None); yt.set_client("sidadiya", None)
    monkeypatch.undo(); get_settings.cache_clear()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "verify_google_token", lambda t: {"email": t, "email_verified": True})
    c = TestClient(app)
    login(c)
    return c


@pytest.fixture
def yfake():
    f = FakeYouTube()
    yt.set_client("adspot", f)
    return f


def run_all():
    for _ in range(60):
        with db.admin() as a:
            a.execute("UPDATE jobs SET run_at = now() WHERE status='queued'")      # skip the 15 s Veo poll delay
        if not worker.work_once():
            return


def new_project(client, **kw):
    body = {"company": "adspot", "topic": "booking a newspaper ad online", "voice_mode": "none", **kw}
    r = client.post("/api/video", json=body)
    assert r.status_code == 200, r.text
    run_all()
    return r.json()["id"]


def pdata(pid):
    with company_scope("adspot") as s:
        return projects.get(s, pid), projects.shots(s, pid)


def mp4(tmp, name="clip.mp4", secs=2):
    p = tmp / name
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=720x1280:r=30:d={secs}",
                    "-pix_fmt", "yuv420p", str(p)], check=True)
    return p


def put_asset(client, pid, beat, path, mime="video/mp4"):
    return client.post(f"/api/video/adspot/{pid}/shots/{beat}/asset", files={"file": (path.name, path.read_bytes(), mime)})


def to_review(client, tmp, *, placeholders=True):
    pid = new_project(client)
    assert client.post(f"/api/video/adspot/{pid}/approve-script").status_code == 200
    assert client.post(f"/api/video/adspot/{pid}/render", json={"allow_placeholders": placeholders}).status_code == 200
    run_all()
    return pid


# ---------------------------------------------------------------- the installed skill
def test_skill_is_installed_and_vendored_unmodified():
    assert len(hookscore.FORMULAS) == 21
    for n in ("yt-script", "yt-shorts", "yt-edit", "yt-retention", "yt-package", "yt-seo"):
        assert (ROOT / ".claude/skills" / n / "SKILL.md").is_file(), n
    assert (ROOT / ".claude/skills/LICENSE-youtube-agent-skill").is_file()
    assert "MIT" in (ROOT / "agentco/youtube/vendor/yt_skill/LICENSE").read_text()
    notice = (ROOT / "agentco/youtube/vendor/yt_skill/NOTICE.md").read_text()
    assert "Jakeschincariol/youtube-agent-skill" in notice and "UNMODIFIED" in notice
    good = hookscore.score("97% of people who start a channel quit before video 30. Here is what the other 3% do differently.")[1]
    bad = hookscore.score("Hey guys welcome back to the channel today we are going to talk about something amazing")[1]
    assert good > bad + 20


# ---------------------------------------------------------------- script agent
def test_script_agent_scores_five_hooks_and_writes_beats(client):
    pid = new_project(client)
    p, sh = pdata(pid)
    assert p["status"] == "script_review" and len(p["hook_options"]) == 5
    scores = [h["score"] for h in p["hook_options"]]
    assert scores == sorted(scores, reverse=True) and p["hook"]["line"] == p["hook_options"][0]["line"]
    assert all(h["band"] in ("STRONG", "WORKABLE", "WEAK") and "panel" in h for h in p["hook_options"])
    assert 4 <= len(sh) <= 7 and sh[0]["spoken"] == p["hook"]["line"]            # beat 1 IS the hook, verbatim
    assert all(s["on_screen"] and "9:16" in s["visual_prompt"] for s in sh)
    assert p["title"] and p["tags"] and p["claims_check"]["ok"] is True
    with company_scope("adspot") as s:
        assert {r["data_class"] for r in s.conn.execute("SELECT data_class FROM cost_events").fetchall()} == {"PUBLIC"}
        assert any(e["type"] == "video.script_ready" for e in s.list_events())
        assert s.conn.execute("SELECT COUNT(*) AS n FROM tool_calls WHERE tool='save_video_script' AND status='ok'").fetchone()["n"] == 1


def test_model_only_sees_public_facts_and_topic(client):
    seen = []
    fake.set_handler(lambda sy, p: (seen.append(p), fake._default(sy, p))[1])
    new_project(client)
    assert any("AdSpot Media is Sri Lanka's first" in p for p in seen)
    assert not any("@" in p or "BOOK-" in p for p in seen)


def test_unsupported_claim_flags_the_script(client):
    fake.set_handler(lambda sy, p: json.dumps({"ok": False, "issues": ["Claims it takes 5 minutes, not in facts"]})
                     if "claims checker" in sy else fake._default(sy, p))
    p, _ = pdata(new_project(client))
    assert p["status"] == "script_review" and "5 minutes" in p["claims_check"]["issues"][0] and p["claims_check"]["ok"] is False


def test_too_long_script_fails_cleanly_after_one_retry(client):
    calls = []
    def h(sy, p):
        if "script writer" in sy:
            calls.append(1)
            beat = {"spoken": "word " * 40, "on_screen": "X", "visual_prompt": "Vertical 9:16 scene, no text"}
            return json.dumps({"title": "T title", "description": "d", "tags": [], "beats": [beat] * 4})
        return fake._default(sy, p)
    fake.set_handler(h)
    pid = new_project(client)
    p, _ = pdata(pid)
    assert len(calls) == 2 and p["status"] == "failed" and "at most 125 words" in p["error"]


def test_private_details_cannot_enter_a_video(client):
    for bad in ("offer for ravi@example.com", "booking BOOK-20260101-001 is live", "call 0771234567 now"):
        assert client.post("/api/video", json={"company": "adspot", "topic": bad}).status_code == 422
    fake.set_handler(lambda sy, p: json.dumps({"hooks": [{"formula": "the-statistic", "line": "Email ravi@example.com for 97% off now"}] * 3})
                     if "hook writer" in sy else fake._default(sy, p))
    p, _ = pdata(new_project(client))
    assert p["status"] == "failed" and "private details" in p["error"]


def test_paused_video_agent_blocks_requests(client):
    with company_scope("adspot") as s:
        s.set_agent_status(agent("adspot", "Video Producer")["id"], "paused")
    assert client.post("/api/video", json={"company": "adspot", "topic": "anything goes here"}).status_code == 409


def test_budget_stops_the_video_agent(client):
    with company_scope("adspot") as s:
        s.set_agent_budget(agent("adspot", "Video Producer")["id"], 0.0015)     # fake model: 0.001 per call
    p, _ = pdata(new_project(client))
    assert p["status"] == "failed" and "budget" in p["error"].lower()
    with company_scope("adspot") as s:
        assert next(a for a in s.list_agents() if a["role"] == "video")["status"] == "budget_exhausted"


# ---------------------------------------------------------------- review & edit
def test_script_can_be_edited_only_while_in_review_and_stays_public_safe(client):
    pid = new_project(client)
    ok = client.put(f"/api/video/adspot/{pid}/shots/2", json={"spoken": "Short and sweet line.", "on_screen": "SWEET", "visual_prompt": "Vertical 9:16 calm desk, no text"})
    assert ok.status_code == 200 and pdata(pid)[1][1]["spoken"] == "Short and sweet line."
    assert client.put(f"/api/video/adspot/{pid}/shots/2", json={"spoken": "Mail me at a@b.com", "on_screen": "X", "visual_prompt": "Vertical 9:16 desk"}).status_code == 409
    assert client.put(f"/api/video/adspot/{pid}/meta", json={"title": "My <b>title</b>", "description": "hello", "tags": ["a,b", "#c"]}).status_code == 200
    p, _ = pdata(pid)
    assert p["title"] == "My btitle/b" and "#Shorts" in p["description"] and p["tags"] == ["ab", "c"]
    client.post(f"/api/video/adspot/{pid}/approve-script")
    assert client.put(f"/api/video/adspot/{pid}/shots/2", json={"spoken": "Too late to edit.", "on_screen": "X", "visual_prompt": "Vertical 9:16 desk"}).status_code == 409


# ---------------------------------------------------------------- voice + clips
def test_ai_voiceover_is_made_per_beat_and_costed(client):
    pid = new_project(client, voice_mode="tts")
    client.post(f"/api/video/adspot/{pid}/approve-script"); run_all()
    p, sh = pdata(pid)
    assert all(s["voice_file"] and (projects.media_dir(pid) / s["voice_file"]).is_file() for s in sh)
    with company_scope("adspot") as s:
        assert s.conn.execute("SELECT COUNT(*) AS n FROM cost_events WHERE task='tts'").fetchone()["n"] == len(sh)


def test_veo_clips_are_polled_and_requeued_until_ready(client):
    with company_scope("adspot") as s:
        s.set_agent_budget(agent("adspot", "Video Producer")["id"], 200)       # real Veo is ~$3 per 8 s clip
    pid = new_project(client, clip_mode="veo_api")
    client.post(f"/api/video/adspot/{pid}/approve-script"); run_all()
    p, sh = pdata(pid)
    assert all(s["status"] == "ready" and s["source"] == "veo" and s["asset_kind"] == "video" for s in sh)
    assert all((projects.media_dir(pid) / s["asset_file"]).is_file() for s in sh)
    with company_scope("adspot") as s:
        assert s.conn.execute("SELECT COUNT(*) AS n FROM cost_events WHERE task='veo_clip'").fetchone()["n"] == len(sh)
        assert s.conn.execute("SELECT COUNT(*) AS n FROM tasks WHERE title LIKE 'video_clip%'").fetchone()["n"] == 0   # polls stay off the Tasks board


def test_default_budget_stops_veo_midway_then_retry_resumes_without_paying_twice(client):
    pid = new_project(client, clip_mode="veo_api")                          # default $5 budget, fake Veo $3.20 per clip
    client.post(f"/api/video/adspot/{pid}/approve-script"); run_all()
    p, sh = pdata(pid)
    ready = [s for s in sh if s["status"] == "ready"]
    assert p["status"] == "assets" and 0 < len(ready) < len(sh)               # the project survives a budget stop
    assert any("budget" in (s["error"] or "").lower() for s in sh if s["status"] != "ready")
    with company_scope("adspot") as s:
        assert next(a for a in s.list_agents() if a["role"] == "video")["status"] == "budget_exhausted"
        spent_before = s.conn.execute("SELECT COUNT(*) AS n FROM cost_events WHERE task='veo_clip'").fetchone()["n"]
        s.set_agent_budget(agent("adspot", "Video Producer")["id"], 200); s.set_agent_status(agent("adspot", "Video Producer")["id"], "active")
    assert client.post(f"/api/video/adspot/{pid}/retry-assets").status_code == 200
    run_all()
    p, sh = pdata(pid)
    assert all(s["status"] == "ready" for s in sh) and p["status"] == "assets"
    with company_scope("adspot") as s:
        assert s.conn.execute("SELECT COUNT(*) AS n FROM cost_events WHERE task='veo_clip'").fetchone()["n"] == len(sh)   # one charge per clip
        assert spent_before < len(sh)


def test_flow_assets_are_validated_before_they_are_accepted(client, media):
    pid = new_project(client)
    client.post(f"/api/video/adspot/{pid}/approve-script")
    good = mp4(media)
    assert put_asset(client, pid, 1, good).status_code == 200
    assert pdata(pid)[1][0]["source"] == "flow_upload" and pdata(pid)[1][0]["status"] == "ready"
    assert client.post(f"/api/video/adspot/{pid}/shots/2/asset", files={"file": ("a.mp4", b"#EXTM3U\nhttp://127.0.0.1/x.ts", "video/mp4")}).status_code == 415
    assert client.post(f"/api/video/adspot/{pid}/shots/2/asset", files={"file": ("a.exe", b"MZ", "application/octet-stream")}).status_code == 415
    fakevid = b"\x00\x00\x00\x18ftypmp42" + b"junk" * 50                            # right signature, not a real video
    assert client.post(f"/api/video/adspot/{pid}/shots/2/asset", files={"file": ("a.mp4", fakevid, "video/mp4")}).status_code == 422
    assert put_asset(client, pid, 99, good).status_code == 404


# ---------------------------------------------------------------- render
def test_render_refuses_missing_pictures_unless_placeholders_allowed(client):
    pid = new_project(client)
    client.post(f"/api/video/adspot/{pid}/approve-script")
    r = client.post(f"/api/video/adspot/{pid}/render", json={"allow_placeholders": False})
    assert r.status_code == 409 and "without a picture" in r.json()["detail"]
    assert client.get(f"/api/video/adspot/{pid}/file").status_code == 404


def test_real_render_makes_a_vertical_short_with_captions(client, media):
    pid = new_project(client, voice_mode="tts")
    client.post(f"/api/video/adspot/{pid}/approve-script"); run_all()                  # voiceover
    assert put_asset(client, pid, 1, mp4(media)).status_code == 200
    img = media / "pic.png"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=teal:s=800x800:d=1", "-frames:v", "1", str(img)], check=True)
    assert put_asset(client, pid, 2, img, "image/png").status_code == 200
    assert client.post(f"/api/video/adspot/{pid}/render", json={"allow_placeholders": True}).status_code == 200
    run_all()
    p, _ = pdata(pid)
    assert p["status"] == "review" and 5 < float(p["duration_s"]) < 59.5, p["error"]
    info = render.probe(projects.media_dir(pid) / "render" / "final.mp4")
    assert (info["w"], info["h"]) == (1080, 1920)
    assert (projects.media_dir(pid) / "render" / "preview.jpg").is_file()
    f = client.get(f"/api/video/adspot/{pid}/file")
    assert f.status_code == 200 and f.headers["content-type"] == "video/mp4" and f.content[4:8] == b"ftyp"
    assert client.get(f"/api/video/adspot/{pid}/preview.jpg").status_code == 200
    # changing a picture invalidates the render
    assert put_asset(client, pid, 1, mp4(media, "other.mp4")).status_code == 200
    assert pdata(pid)[0]["status"] == "assets" and pdata(pid)[0]["render_file"] is None
    assert client.get(f"/api/video/adspot/{pid}/file").status_code == 404


def test_a_video_over_a_minute_is_refused():
    beats = [{"beat_no": i, "spoken": "word " * 50, "on_screen": "", "asset": None, "asset_kind": None, "voice": None} for i in range(1, 6)]
    with pytest.raises(render.RenderError, match="under 60s"):
        render.render(Path("/tmp/agentco-too-long"), beats, True)


def test_every_ffmpeg_input_blocks_network_protocols(client, media, monkeypatch):
    seen = []
    orig = render._run
    monkeypatch.setattr(render, "_run", lambda cmd, timeout=240: (seen.append(cmd), orig(cmd, timeout))[1])
    to_review(client, media)
    inputs = [c for c in seen if "-i" in c and "lavfi" not in c[c.index("-i") - 1]]
    assert inputs and all("-protocol_whitelist" in c and "file,pipe" in c for c in inputs)


# ---------------------------------------------------------------- YouTube upload
def test_upload_is_private_once_and_needs_a_rendered_video(client, media, yfake):
    pid = new_project(client)
    assert client.post(f"/api/video/adspot/{pid}/upload").status_code == 409                   # not rendered yet
    pid = to_review(client, media)
    client.put(f"/api/video/adspot/{pid}/meta", json={"title": "Book ads <fast>", "description": "Try it", "tags": ["ads"]})
    r = client.post(f"/api/video/adspot/{pid}/upload")
    assert r.status_code == 200 and r.json()["url"].startswith("https://youtube.com/shorts/")
    up = yfake.uploads[0]
    assert up["title"] == "Book ads fast" and "#Shorts" in up["description"] and up["synthetic"] is True and up["path"].endswith("final.mp4")
    assert pdata(pid)[0]["status"] == "uploaded"
    again = client.post(f"/api/video/adspot/{pid}/upload")
    assert again.status_code == 409 and len(yfake.uploads) == 1                               # at most once
    with db.admin() as a:
        assert "PRIVATE" in a.execute("SELECT body FROM messages WHERE body LIKE 'Short #%' ORDER BY id DESC LIMIT 1").fetchone()["body"]


def test_failed_upload_is_not_retried_silently_but_can_be_retried_by_the_owner(client, media, yfake):
    pid = to_review(client, media)
    yfake.fail = True
    assert client.post(f"/api/video/adspot/{pid}/upload").status_code == 409
    with company_scope("adspot") as s:
        assert s.conn.execute("SELECT status FROM youtube_uploads").fetchone()["status"] == "failed"
    assert yfake.uploads == []
    yfake.fail = False
    assert client.post(f"/api/video/adspot/{pid}/upload").status_code == 200 and len(yfake.uploads) == 1


def test_youtube_not_connected_gives_a_clear_message(client, media):
    pid = to_review(client, media)
    r = client.post(f"/api/video/adspot/{pid}/upload")
    assert r.status_code == 409 and "not connected" in r.json()["detail"]


def test_daily_upload_cap(client, media, yfake, monkeypatch):
    monkeypatch.setenv("YOUTUBE_MAX_UPLOADS_PER_DAY", "1")
    get_settings.cache_clear()
    a, b = to_review(client, media), to_review(client, media)
    assert client.post(f"/api/video/adspot/{a}/upload").status_code == 200
    r = client.post(f"/api/video/adspot/{b}/upload")
    assert r.status_code == 409 and "daily upload cap" in r.json()["detail"] and len(yfake.uploads) == 1


def test_agent_requested_upload_needs_approval(client, media, yfake):
    pid = to_review(client, media)
    vid = agent("adspot", "Video Producer")
    with company_scope("adspot") as s:
        r = registry.call(s, vid, None, "youtube_upload", {"project_id": pid})
    assert r["status"] == "pending_approval" and yfake.uploads == []
    assert client.post(f"/api/approvals/adspot/{r['approval_id']}", json={"decision": "approve"}).status_code == 200
    assert len(yfake.uploads) == 1


def test_rest_client_hardcodes_private_and_not_for_kids(media, monkeypatch):
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "id"); monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "sec")
    get_settings.cache_clear()
    posted = {}
    class R:
        def __init__(s, j=None, h=None): s._j, s.headers = j or {}, h or {}
        def raise_for_status(s): pass
        def json(s): return s._j
    def post(url, **kw):
        if "oauth2" in url: return R({"access_token": "tok"})
        posted.update(kw); return R(h={"Location": "https://upload.example/session"})
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "put", lambda url, **kw: R({"id": "abc123"}))
    f = media / "v.mp4"; f.write_bytes(b"x" * 10)
    out = yt.RestYouTube("refresh").upload(path=str(f), title="T", description="D #Shorts", tags=["a"], synthetic=True)
    assert out == {"id": "abc123", "url": "https://youtube.com/shorts/abc123"}
    st = posted["json"]["status"]
    assert st["privacyStatus"] == "private" and st["selfDeclaredMadeForKids"] is False and st["containsSyntheticMedia"] is True
    assert posted["headers"]["Authorization"] == "Bearer tok"


def test_meta_cleaning():
    m = yt.clean_meta("  <Hi>  " + "x" * 200, "desc", ["ok", "bad,tag", "#hash", "z" * 80] + [f"t{i}" for i in range(30)])
    assert "<" not in m["title"] and len(m["title"]) <= 100 and m["description"].endswith("#Shorts")
    assert "badtag" in m["tags"] and "hash" in m["tags"] and all(len(t) <= 30 for t in m["tags"]) and len(m["tags"]) <= 12
    with pytest.raises(yt.MetaError):
        yt.clean_meta("<>", "d", [])


# ---------------------------------------------------------------- tools, isolation, auth
def test_only_the_video_department_has_video_tools():
    for dept, tools in registry.DEPT_TOOLS.items():
        has = {"save_video_script", "youtube_upload"} & tools
        assert (dept == "video") == bool(has), dept
    vid = agent("adspot", "Video Producer")
    with company_scope("adspot") as s:
        for t in ("send_publication_mail", "send_customer_mail", "mark_booking_paid", "save_content_pack"):
            assert registry.call(s, vid, None, t, {"booking_ref": "B", "amount": 1})["status"] == "denied"
    mk = agent("adspot", "Marketing")
    with company_scope("adspot") as s:
        assert registry.call(s, mk, None, "youtube_upload", {"project_id": 1})["status"] == "denied"


def test_video_data_is_isolated_between_companies(client, media):
    pid = to_review(client, media)
    with company_scope("sidadiya") as s:
        for t in ("video_projects", "video_shots", "youtube_uploads"):
            assert s.conn.execute(f"SELECT * FROM {t}").fetchall() == [], t
        assert projects.get(s, pid) is None
    assert client.post(f"/api/video/sidadiya/{pid}/render", json={"allow_placeholders": True}).status_code == 404
    assert client.get(f"/api/video/sidadiya/{pid}/file").status_code == 404
    assert client.post(f"/api/video/sidadiya/{pid}/upload").status_code == 409


def test_both_channels_work_independently(client, media):
    r = client.post("/api/video", json={"company": "sidadiya", "topic": "start a shop in 14 days", "voice_mode": "none"})
    assert r.status_code == 200
    run_all()
    with company_scope("sidadiya") as s:
        assert projects.get(s, r.json()["id"])["status"] == "script_review"
    caps = client.get("/api/video?scope=all").json()["capabilities"]
    assert set(caps["youtube"]) == {"adspot", "sidadiya"}


def test_video_endpoints_need_the_owner_session():
    anon = TestClient(app)
    for method, path in (("get", "/api/video"), ("post", "/api/video"), ("put", "/api/video/adspot/1/meta"),
                         ("post", "/api/video/adspot/1/approve-script"), ("post", "/api/video/adspot/1/render"),
                         ("post", "/api/video/adspot/1/upload"), ("get", "/api/video/adspot/1/file"),
                         ("post", "/api/video/adspot/1/shots/1/asset"), ("post", "/api/video/adspot/1/reject"),
                         ("post", "/api/video/adspot/1/retry-assets")):
        assert getattr(anon, method)(path).status_code == 401, path


def test_chat_can_start_a_short_and_stops_for_missing_details(client):
    r = client.post("/api/chat", json={"text": "make a short for adspot about booking classified ads online", "scope": "adspot"}).json()["reply"]
    assert "Writing the script for Short" in r and "private" in r
    run_all()
    with company_scope("adspot") as s:
        assert projects.list_projects(s)[0]["status"] == "script_review"
    assert "Which company" in client.post("/api/chat", json={"text": "make a short about booking ads", "scope": "all"}).json()["reply"]
    assert "What should the Short be about" in client.post("/api/chat", json={"text": "make a short for adspot", "scope": "adspot"}).json()["reply"]
