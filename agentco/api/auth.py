"""Single-owner auth: Google ID token (allowlisted email) -> TOTP code -> short-lived signed session cookie."""
from __future__ import annotations

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from .. import db
from ..config import get_settings
from .ratelimit import limit

router = APIRouter(prefix="/auth", tags=["auth"])
PRE, SESSION = "ac_pre", "ac_session"


def _ser(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt=salt)


def verify_google_token(token: str) -> dict:
    """Overridable in tests. Verifies signature, audience and expiry with Google's certs."""
    from google.auth.transport import requests as grequests
    from google.oauth2 import id_token
    return id_token.verify_oauth2_token(token, grequests.Request(), get_settings().google_client_id)


def _set(resp: Response, name: str, value: str, max_age: int) -> None:
    resp.set_cookie(name, value, max_age=max_age, httponly=True, samesite="strict",
                    secure=get_settings().cookie_secure, path="/")


def require_owner(request: Request) -> str:
    s = get_settings()
    raw = request.cookies.get(SESSION)
    if not raw or not s.owner_email:
        raise HTTPException(401, "Not signed in")
    try:
        data = _ser("session").loads(raw, max_age=s.session_ttl_min * 60)
    except (BadSignature, SignatureExpired):
        raise HTTPException(401, "Session expired")
    if data.get("email", "").lower() != s.owner_email.lower():
        raise HTTPException(401, "Not the owner")
    return data["email"]


class GoogleBody(BaseModel):
    id_token: str


class TotpBody(BaseModel):
    code: str


@router.post("/google", dependencies=[Depends(limit("login", 8, 60))])
def google_login(body: GoogleBody, response: Response):
    s = get_settings()
    try:
        info = verify_google_token(body.id_token)
    except Exception:
        raise HTTPException(401, "Invalid Google token")
    email = (info.get("email") or "").lower()
    if not s.owner_email or email != s.owner_email.lower() or not info.get("email_verified"):
        raise HTTPException(403, "This account is not allowed")
    with db.admin() as a:
        row = a.execute("SELECT * FROM owner_auth WHERE id=1").fetchone()
        if row is None:
            secret = pyotp.random_base32()
            a.execute("INSERT INTO owner_auth (id, totp_secret) VALUES (1,%s)", (secret,))
            row = {"totp_secret": secret, "totp_confirmed": False}
    _set(response, PRE, _ser("pre").dumps({"email": email}), 300)
    out = {"step": "totp"}
    if not row["totp_confirmed"]:
        out = {"step": "totp_setup", "secret": row["totp_secret"],
               "uri": pyotp.TOTP(row["totp_secret"]).provisioning_uri(name=email, issuer_name="AgentCo")}
    return out


@router.post("/totp", dependencies=[Depends(limit("totp", 6, 60))])
def totp_login(body: TotpBody, request: Request, response: Response):
    s = get_settings()
    try:
        pre = _ser("pre").loads(request.cookies.get(PRE, ""), max_age=300)
    except (BadSignature, SignatureExpired):
        raise HTTPException(401, "Start again with Google sign-in")
    with db.admin() as a:
        row = a.execute("SELECT * FROM owner_auth WHERE id=1").fetchone()
        if not row or not pyotp.TOTP(row["totp_secret"]).verify(body.code.strip(), valid_window=1):
            raise HTTPException(401, "Wrong code")
        if not row["totp_confirmed"]:
            a.execute("UPDATE owner_auth SET totp_confirmed=true WHERE id=1")
    response.delete_cookie(PRE, path="/")
    _set(response, SESSION, _ser("session").dumps({"email": pre["email"]}), s.session_ttl_min * 60)
    return {"ok": True}


@router.get("/me")
def me(email: str = Depends(require_owner)):
    return {"email": email}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION, path="/")
    return {"ok": True}
