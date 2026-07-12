"""Security middleware and helpers: rate limiting, headers, audit logging.

The rate limiter is a per-process sliding window keyed on (client IP, bucket).
It protects credential endpoints against brute force and the public join
endpoint against scripted abuse. For multi-process deployments put a shared
limiter (e.g. a reverse-proxy limit or Redis) in front as well — this is the
in-app backstop, not the only line of defense.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m

# window size / max attempts are env-tunable so tests can tighten them
RATE_WINDOW_SECONDS = int(os.environ.get("RATE_WINDOW_SECONDS", "60"))
RATE_MAX_ATTEMPTS = int(os.environ.get("RATE_MAX_ATTEMPTS", "10"))

_attempts: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def client_ip(request: Request) -> str:
    # trust the first hop only; a fronting proxy should set X-Forwarded-For
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request, bucket: str) -> None:
    """Raise 429 when (ip, bucket) exceeds RATE_MAX_ATTEMPTS per window."""
    now = time.monotonic()
    key = (client_ip(request), bucket)
    q = _attempts[key]
    while q and now - q[0] > RATE_WINDOW_SECONDS:
        q.popleft()
    if len(q) >= RATE_MAX_ATTEMPTS:
        raise HTTPException(429, "Too many attempts — try again shortly",
                            headers={"Retry-After": str(RATE_WINDOW_SECONDS)})
    q.append(now)


def reset_rate_limits() -> None:
    """Test hook."""
    _attempts.clear()


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    # API responses are JSON/SVG; a restrictive CSP defangs any reflected markup
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
}


async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    return response


AUDIT_ACTIONS = (
    "auth.register", "auth.login", "auth.login_failed",
    "join.requested", "join.approved", "join.denied",
    "invite.rotated", "request.status_changed", "request.deleted",
    "family.deleted", "member.deleted",
)


def audit(db: AsyncSession, action: str, *, group_id: str | None = None,
          actor_id: str | None = None, target_id: str | None = None,
          detail: str | None = None) -> None:
    """Queue an audit row on the session; caller's commit persists it."""
    db.add(m.AuditLog(action=action, group_id=group_id, actor_id=actor_id,
                      target_id=target_id, detail=detail))
