import json

import pytest
from fastapi.testclient import TestClient

from agentco import content, worker
from agentco.api import auth
from agentco.api.main import app
from agentco.llm import fake
from agentco.scope import company_scope
from agentco.security import public_guard
from agentco.tools import registry
from conftest import agent
from test_api import login  # noqa: F401  (shared helper)


def run_all():
    while worker.work_once():
        pass


def test_post_pack_is_drafted_from_public_facts_only():
    seen = []
    fake.set_handler(lambda system, prompt: (seen.append((system, prompt)), fake._default(system, prompt))[1])
    pid = content.request_pack("adspot", "post", "booking classified ads online")
    run_all()
    with company_scope("adspot") as s:
        p = s.get_pack(pid)
        assert p["status"] == "draft" and p["copy"]["facebook"].startswith("Fake Facebook")
        assert p["image_prompt"] and p["aspect_ratios"]["instagram"].startswith("4:5")
        classes = {r["data_class"] for r in s.conn.execute("SELECT data_class FROM cost_events").fetchall()}
        assert classes == {"PUBLIC"}                      # never INTERNAL/CONFIDENTIAL for public copy
        assert any(e["type"] == "content.drafted" for e in s.list_events())
    copy_prompt = seen[0][1]
    assert "AdSpot Media is Sri Lanka's first" in copy_prompt and "booking_id" not in copy_prompt


def test_ad_pack_has_an_ads_manager_setup_sheet():
    pid = content.request_pack("sidadiya", "ad", "start your 14-day shop trial")
    run_all()
    with company_scope("sidadiya") as s:
        sheet = s.get_pack(pid)["ad_setup"]
        assert sheet["objective"] and sheet["headline"] and sheet["call_to_action"]


def test_unsupported_claims_flag_the_pack_for_review():
    def h(system, prompt):
        if "claims checker" in system:
            return json.dumps({"ok": False, "issues": ["Claims 50% discount, not in facts"]})
        return fake._default(system, prompt)
    fake.set_handler(h)
    pid = content.request_pack("adspot", "post", "big sale")
    run_all()
    with company_scope("adspot") as s:
        p = s.get_pack(pid)
        assert p["status"] == "needs_review" and "50% discount" in p["claims_check"]["issues"][0]


def test_private_details_cannot_enter_public_content():
    for bad in ("offer for ravi@example.com customers", "booking BOOK-20260101-002 is live", "call 0771234567"):
        with pytest.raises(ValueError):
            content.request_pack("adspot", "post", bad)
    assert public_guard.problems("hello team")  == []
    # even if the model leaks one into its draft, the pack is flagged
    fake.set_handler(lambda s, p: fake._default(s, p).replace("Fake Facebook", "mail ravi@example.com"))
    pid = content.request_pack("adspot", "post", "a normal topic")
    run_all()
    with company_scope("adspot") as s:
        assert s.get_pack(pid)["status"] == "needs_review"


def test_agent_can_only_save_drafts_and_marketing_has_no_send_tools():
    mk = agent("adspot", "Marketing")
    with company_scope("adspot") as s:
        pid = s.create_pack(kind="post", topic="t", image_mode="flow_manual", agent_id=mk["id"])
        assert registry.call(s, mk, None, "save_content_pack", {"pack_id": pid, "status": "posted"})["status"] == "error"
        for t in ("send_publication_mail", "send_customer_mail", "publish_post"):
            assert registry.call(s, mk, None, t, {})["status"] == "denied"
        assert s.get_pack(pid)["status"] == "drafting"


def test_packs_are_isolated_between_companies():
    pid = content.request_pack("adspot", "post", "something public")
    run_all()
    with company_scope("sidadiya") as s:
        assert s.get_pack(pid) is None and s.list_packs() == []
        assert s.conn.execute("SELECT * FROM content_packs").fetchall() == []


def test_paused_marketing_agent_blocks_requests():
    with company_scope("adspot") as s:
        s.set_agent_status(agent("adspot", "Marketing")["id"], "paused")
    with pytest.raises(LookupError):
        content.request_pack("adspot", "post", "anything at all")


def test_gemini_image_mode_stores_a_downloadable_image(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    from agentco.config import get_settings
    get_settings.cache_clear()
    try:
        pid = content.request_pack("adspot", "post", "classified ads online", "gemini_image")
        run_all()
        with company_scope("adspot") as s:
            p = s.get_pack(pid)
            assert p["image_file"] and (tmp_path / "content" / p["image_file"]).is_file()
            assert s.conn.execute("SELECT COUNT(*) AS n FROM cost_events WHERE task='content_image'").fetchone()["n"] == 1
    finally:
        monkeypatch.undo(); get_settings.cache_clear()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "verify_google_token", lambda t: {"email": t, "email_verified": True})
    return TestClient(app)


def test_content_api_flow(client):
    assert client.get("/api/content").status_code == 401
    login(client)
    r = client.post("/api/content", json={"company": "adspot", "kind": "post", "topic": "classified ads online"})
    assert r.status_code == 200
    assert client.post("/api/content", json={"company": "adspot", "kind": "post", "topic": "mail me a@b.com"}).status_code == 422
    run_all()
    packs = client.get("/api/content?scope=adspot").json()
    pid = packs[0]["id"]
    assert client.post(f"/api/content/adspot/{pid}/status", json={"status": "posted"}).status_code == 409   # must approve first
    assert client.post(f"/api/content/adspot/{pid}/status", json={"status": "approved"}).status_code == 200
    assert client.post(f"/api/content/adspot/{pid}/status", json={"status": "posted"}).status_code == 200
    assert client.post(f"/api/content/sidadiya/{pid}/status", json={"status": "rejected"}).status_code == 409   # other company
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 40
    assert client.post(f"/api/content/adspot/{pid}/image", files={"file": ("a.png", png, "image/png")}).status_code == 200
    assert client.get(f"/api/content/adspot/{pid}/image").status_code == 200
    assert client.post(f"/api/content/adspot/{pid}/image", files={"file": ("a.pdf", b"%PDF-1", "application/pdf")}).status_code == 415


def test_facts_editor_rejects_private_data_and_chat_creates_drafts(client):
    login(client)
    assert client.put("/api/facts/adspot", json={"key": "offer", "value": "Email us at x@y.com"}).status_code == 422
    assert client.put("/api/facts/adspot", json={"key": "offer", "value": "Book online any time."}).status_code == 200
    assert any(f["key"] == "offer" for f in client.get("/api/facts?scope=adspot").json())
    r = client.post("/api/chat", json={"text": "make a post for adspot about booking obituary ads", "scope": "adspot"})
    assert "Drafting post" in r.json()["reply"]
    run_all()
    assert client.get("/api/content?scope=adspot").json()[0]["status"] == "draft"
