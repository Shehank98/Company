import pyotp
import pytest
from fastapi.testclient import TestClient

from agentco.api import auth
from agentco.api.main import app
from agentco.scope import company_scope
from agentco.tools import registry
from conftest import agent


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "verify_google_token", lambda t: {"email": t, "email_verified": True})
    return TestClient(app)


def login(client, email="owner@example.com"):
    r = client.post("/auth/google", json={"id_token": email})
    assert r.status_code == 200, r.text
    secret = r.json().get("secret")
    if secret is None:
        from agentco import db
        with db.admin() as a:
            secret = a.execute("SELECT totp_secret FROM owner_auth").fetchone()["totp_secret"]
    r = client.post("/auth/totp", json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text


def test_everything_needs_the_session(client):
    for path in ("/api/approvals", "/api/departments", "/api/chat", "/api/costs", "/api/companies", "/api/activity"):
        assert client.get(path).status_code == 401
    assert client.post("/api/chat", json={"text": "status"}).status_code == 401
    assert client.get("/api/health").status_code == 200


def test_only_the_allowlisted_google_account_gets_in(client):
    assert client.post("/auth/google", json={"id_token": "stranger@example.com"}).status_code == 403


def test_google_alone_is_not_enough_and_wrong_totp_fails(client):
    client.post("/auth/google", json={"id_token": "owner@example.com"})
    assert client.get("/api/approvals").status_code == 401           # no session yet
    assert client.post("/auth/totp", json={"code": "000000"}).status_code == 401
    assert client.get("/api/approvals").status_code == 401


def test_totp_step_needs_the_google_step_first(client):
    assert client.post("/auth/totp", json={"code": "123456"}).status_code == 401


def test_login_is_rate_limited(client):
    codes = [client.post("/auth/google", json={"id_token": "x@x.com"}).status_code for _ in range(10)]
    assert 429 in codes


def test_chat_and_company_views(client):
    login(client)
    assert client.get("/api/auth/me" if False else "/auth/me").json()["email"] == "owner@example.com"
    r = client.post("/api/chat", json={"text": "status", "scope": "all"})
    assert "AdSpot Media" in r.json()["reply"] and "Sidadiya" in r.json()["reply"]
    deps = client.get("/api/departments?scope=sidadiya").json()
    assert deps and {d["company"] for d in deps} == {"sidadiya"}
    assert client.get("/api/departments?scope=nope").status_code == 404


def test_approval_flow_and_double_decision(client):
    login(client)
    ops = agent("adspot", "AdSpot Ops")
    with company_scope("adspot") as s:
        ap = registry.call(s, ops, None, "send_publication_mail", {"booking_ref": "BOOK-1"})["approval_id"]
    items = client.get("/api/approvals?scope=all").json()
    assert [i["id"] for i in items] == [ap] and items[0]["company"] == "adspot"
    # the other company cannot decide it (RLS): not found / already decided
    assert client.post(f"/api/approvals/sidadiya/{ap}", json={"decision": "approve"}).status_code == 409
    r = client.post(f"/api/approvals/adspot/{ap}", json={"decision": "approve"})
    assert r.status_code == 200 and "nothing was sent" in r.json()["note"]
    assert client.post(f"/api/approvals/adspot/{ap}", json={"decision": "reject"}).status_code == 409


def test_edit_keeps_approval_pending(client):
    login(client)
    mk = agent("sidadiya", "Marketing")
    with company_scope("sidadiya") as s:
        ap = registry.call(s, mk, None, "publish_post", {"post_id": "p1"})["approval_id"]
    assert client.post(f"/api/approvals/sidadiya/{ap}", json={"decision": "edit", "summary": "Reworded"}).status_code == 200
    got = client.get("/api/approvals?scope=sidadiya").json()
    assert got[0]["summary"] == "Reworded" and got[0]["status"] == "pending"


def test_upload_validation(client):
    login(client)
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 50
    assert client.post("/api/chat/upload", files={"file": ("r.png", png, "image/png")}, data={"scope": "adspot"}).status_code == 200
    assert client.post("/api/chat/upload", files={"file": ("evil.exe", b"MZ", "application/octet-stream")}).status_code == 415
    assert client.post("/api/chat/upload", files={"file": ("fake.pdf", b"not a pdf", "application/pdf")}).status_code == 415
    big = b"%PDF" + b"0" * (10 * 1024 * 1024)
    assert client.post("/api/chat/upload", files={"file": ("big.pdf", big, "application/pdf")}).status_code == 413


def test_budget_exhausted_agent_cannot_be_resumed_without_new_budget(client):
    login(client)
    a = agent("adspot", "AdSpot Ops")
    with company_scope("adspot") as s:
        s.set_agent_status(a["id"], "budget_exhausted")
    assert client.post(f"/api/departments/adspot/{a['id']}/status", json={"status": "active"}).status_code == 409
    assert client.post(f"/api/departments/adspot/{a['id']}/budget", json={"monthly_budget_usd": 20}).status_code == 200
    deps = client.get("/api/departments?scope=adspot").json()
    assert next(d for d in deps if d["id"] == a["id"])["status"] == "paused"   # owner must resume explicitly
