"""Tiny in-memory sliding-window limiter. ponytail: per-process; move to Postgres if api is scaled out."""
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

_hits: dict[tuple[str, str], deque] = defaultdict(deque)


def limit(name: str, max_calls: int, per_seconds: int):
    def dep(request: Request) -> None:
        ip = request.client.host if request.client else "?"
        q = _hits[(name, ip)]
        now = time.monotonic()
        while q and now - q[0] > per_seconds:
            q.popleft()
        if len(q) >= max_calls:
            raise HTTPException(429, "Too many requests")
        q.append(now)
    return dep


def reset() -> None:
    _hits.clear()
