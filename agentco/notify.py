"""Owner notifications: chat messages (admin table) and web push."""
from __future__ import annotations

import json

from . import db
from .config import get_settings


def post_message(scope: str, role: str, body: str, attachment: dict | None = None) -> int:
    from psycopg.types.json import Jsonb
    with db.admin() as a:
        return a.execute("INSERT INTO messages (scope, role, body, attachment) VALUES (%s,%s,%s,%s) RETURNING id",
                         (scope, role, body, Jsonb(attachment) if attachment else None)).fetchone()["id"]


def send_push(title: str, body: str) -> int:
    s = get_settings()
    if not (s.vapid_private_key and s.vapid_public_key):
        return 0
    from pywebpush import WebPushException, webpush
    sent = 0
    with db.admin() as a:
        subs = a.execute("SELECT * FROM push_subscriptions").fetchall()
        for sub in subs:
            try:
                webpush({"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                        data=json.dumps({"title": title, "body": body}), vapid_private_key=s.vapid_private_key,
                        vapid_claims={"sub": s.vapid_subject})
                sent += 1
            except WebPushException as e:
                if getattr(e.response, "status_code", None) in (404, 410):   # subscription gone
                    a.execute("DELETE FROM push_subscriptions WHERE id=%s", (sub["id"],))
            except Exception:
                pass
    return sent
