"""YouTube Data API upload (OAuth refresh token per channel, in Railway env only; never in model context).

SAFETY: privacy is hard-coded to PRIVATE. AgentCo never makes a video public; the owner does that in YouTube Studio.
(Also: videos uploaded through an API project that has not passed Google's audit are forced private anyway.)
Quota: videos.insert costs 1600 units of the default 10,000/day, so keep youtube_max_uploads_per_day <= 6."""
from __future__ import annotations

import os
import re
from pathlib import Path

import httpx

from ..config import get_settings

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
PRIVACY = "private"          # constant on purpose: there is no parameter to change it


class YouTubeDisabled(RuntimeError):
    pass


class MetaError(ValueError):
    pass


_clients: dict[str, object] = {}


def set_client(slug: str, c) -> None:
    if c is None:
        _clients.pop(slug, None)
    else:
        _clients[slug] = c


def _token(slug: str) -> str:
    return getattr(get_settings(), f"youtube_refresh_token_{slug}", "")


def configured(slug: str) -> bool:
    s = get_settings()
    return slug in _clients or bool(s.youtube_client_id and s.youtube_client_secret and _token(slug))


def get_client(slug: str):
    if slug not in _clients:
        if not configured(slug):
            raise YouTubeDisabled(f"YouTube is not configured for {slug}")
        _clients[slug] = RestYouTube(_token(slug))
    return _clients[slug]


def clean_meta(title: str, description: str, tags: list[str]) -> dict:
    """Validate/normalise what we send. YouTube rejects angle brackets; titles max 100 chars; tags ~500 chars total."""
    t = re.sub(r"[<>]", "", (title or "")).strip()
    if not t:
        raise MetaError("the video needs a title")
    d = re.sub(r"[<>]", "", (description or "")).strip()[:4800]
    if "#shorts" not in (t + d).lower():
        d = (d + "\n\n#Shorts").strip()
    out, total = [], 0
    for tag in tags or []:
        tag = re.sub(r"[<>,#]", "", str(tag)).strip()[:30]
        if tag and total + len(tag) + 1 <= 450 and len(out) < 12:
            out.append(tag)
            total += len(tag) + 1
    return {"title": t[:100], "description": d, "tags": out}


class RestYouTube:
    def __init__(self, refresh_token: str) -> None:
        self.refresh_token = refresh_token
        self._access: str | None = None

    def _auth(self) -> dict:
        if not self._access:
            s = get_settings()
            r = httpx.post(TOKEN_URL, data={"client_id": s.youtube_client_id, "client_secret": s.youtube_client_secret,
                                            "refresh_token": self.refresh_token, "grant_type": "refresh_token"}, timeout=20)
            r.raise_for_status()
            self._access = r.json()["access_token"]
        return {"Authorization": f"Bearer {self._access}"}

    def upload(self, *, path: str, title: str, description: str, tags: list[str], synthetic: bool) -> dict:
        size = os.path.getsize(path)
        body = {"snippet": {"title": title, "description": description, "tags": tags, "categoryId": "22"},
                "status": {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False,
                           # ponytail: field name per YouTube's 2024 AI-disclosure option; verify against the live API
                           "containsSyntheticMedia": bool(synthetic)}}
        init = httpx.post(UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"}, json=body, timeout=30,
                          headers={**self._auth(), "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"})
        init.raise_for_status()
        # ponytail: single-request upload (fine for Shorts, <100 MB); add chunked resume if files ever get large
        up = httpx.put(init.headers["Location"], content=Path(path).read_bytes(), timeout=600,
                       headers={"Content-Type": "video/mp4", "Content-Length": str(size)})
        up.raise_for_status()
        vid = up.json()["id"]
        return {"id": vid, "url": f"https://youtube.com/shorts/{vid}"}
