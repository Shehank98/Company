"""Gmail connector (REST via httpx). Scopes needed on the OAuth refresh token:
  gmail.modify  (read, label, create drafts)   gmail.send
The refresh token / client secret live in Railway env only and never enter model context.
Tests inject a fake client with set_client()."""
from __future__ import annotations

import base64
import re
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Protocol

import httpx

from ..config import get_settings

API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class GmailDisabled(RuntimeError):
    pass


class GmailClient(Protocol):
    def list_inbox(self, max_results: int = 25) -> list[dict]: ...           # [{id, threadId}]
    def get_message(self, msg_id: str) -> dict: ...                          # parsed, see _parse
    def create_draft(self, *, to: str, subject: str, body: str, thread_id: str | None) -> str: ...
    def send(self, *, to: str, subject: str, body: str, attachments: list[tuple[str, bytes, str]],
             thread_id: str | None, in_reply_to: str | None) -> dict: ...    # {id, threadId, rfc_message_id}
    def add_label(self, msg_id: str, label: str) -> None: ...


_client: GmailClient | None = None


def set_client(c: GmailClient | None) -> None:
    global _client
    _client = c


def configured() -> bool:
    s = get_settings()
    return _client is not None or bool(s.gmail_client_id and s.gmail_client_secret and s.gmail_refresh_token)


def get_client() -> GmailClient:
    global _client
    if _client is None:
        if not configured():
            raise GmailDisabled("Gmail is not configured")
        _client = RestGmail()
    return _client


def build_mime(*, to: str, subject: str, body: str, attachments=(), in_reply_to: str | None = None,
               sender: str | None = None) -> EmailMessage:
    m = EmailMessage()
    m["To"], m["Subject"] = to, subject
    if sender:
        m["From"] = sender
    if in_reply_to:
        m["In-Reply-To"] = m["References"] = in_reply_to
    m.set_content(body)
    for name, data, mime in attachments:
        main, _, sub = (mime or "application/octet-stream").partition("/")
        m.add_attachment(data, maintype=main, subtype=sub or "octet-stream", filename=name)
    return m


def _parse(raw: dict) -> dict:
    hdr = {h["name"].lower(): h["value"] for h in raw.get("payload", {}).get("headers", [])}
    text, atts = "", []

    def walk(part):
        nonlocal text
        if part.get("filename"):
            atts.append({"name": part["filename"], "mime": part.get("mimeType"), "size": part.get("body", {}).get("size", 0)})
        elif part.get("mimeType") == "text/plain" and part.get("body", {}).get("data") and not text:
            text = base64.urlsafe_b64decode(part["body"]["data"] + "==").decode("utf-8", "replace")
        for p in part.get("parts", []) or []:
            walk(p)
    walk(raw.get("payload", {}))
    return {"id": raw["id"], "thread_id": raw.get("threadId"), "rfc_message_id": hdr.get("message-id"),
            "from_addr": parseaddr(hdr.get("from", ""))[1].lower(), "subject": hdr.get("subject", ""),
            "body": text[:20000], "attachments": atts, "labels": raw.get("labelIds", [])}


class RestGmail:
    def __init__(self) -> None:
        self._token: str | None = None

    def _auth(self) -> dict:
        if not self._token:
            s = get_settings()
            r = httpx.post(TOKEN_URL, data={"client_id": s.gmail_client_id, "client_secret": s.gmail_client_secret,
                                            "refresh_token": s.gmail_refresh_token, "grant_type": "refresh_token"}, timeout=20)
            r.raise_for_status()
            self._token = r.json()["access_token"]
        return {"Authorization": f"Bearer {self._token}"}

    def _req(self, method: str, path: str, **kw):
        r = httpx.request(method, API + path, headers=self._auth(), timeout=30, **kw)
        if r.status_code == 401:       # token expired: refresh once
            self._token = None
            r = httpx.request(method, API + path, headers=self._auth(), timeout=30, **kw)
        r.raise_for_status()
        return r.json() if r.content else {}

    def list_inbox(self, max_results: int = 25) -> list[dict]:
        return self._req("GET", "/messages", params={"labelIds": "INBOX", "maxResults": max_results,
                                                     "q": "newer_than:3d -from:me"}).get("messages", [])

    def get_message(self, msg_id: str) -> dict:
        return _parse(self._req("GET", f"/messages/{msg_id}", params={"format": "full"}))

    def create_draft(self, *, to, subject, body, thread_id):
        raw = base64.urlsafe_b64encode(build_mime(to=to, subject=subject, body=body).as_bytes()).decode()
        msg = {"raw": raw, **({"threadId": thread_id} if thread_id else {})}
        return self._req("POST", "/drafts", json={"message": msg})["id"]

    def send(self, *, to, subject, body, attachments, thread_id, in_reply_to):
        raw = base64.urlsafe_b64encode(build_mime(to=to, subject=subject, body=body, attachments=attachments,
                                                  in_reply_to=in_reply_to).as_bytes()).decode()
        out = self._req("POST", "/messages/send", json={"raw": raw, **({"threadId": thread_id} if thread_id else {})})
        meta = self._req("GET", f"/messages/{out['id']}", params={"format": "metadata", "metadataHeaders": "Message-ID"})
        mid = next((h["value"] for h in meta.get("payload", {}).get("headers", []) if h["name"].lower() == "message-id"), None)
        return {"id": out["id"], "threadId": out.get("threadId"), "rfc_message_id": mid}

    def add_label(self, msg_id: str, label: str) -> None:
        lid = self._label_id(label)
        self._req("POST", f"/messages/{msg_id}/modify", json={"addLabelIds": [lid]})

    def _label_id(self, name: str) -> str:
        for l in self._req("GET", "/labels").get("labels", []):
            if l["name"].lower() == name.lower():
                return l["id"]
        return self._req("POST", "/labels", json={"name": name})["id"]


REF_RE = re.compile(r"\b(BOOK-\d{8}-\d{3}|ADM-\d{4}-\d{4})\b", re.I)
