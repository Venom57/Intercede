"""Engine/session wiring and deny-by-default auth/authorization dependencies.

Privacy is enforced server-side in the query path (visible_request_filter), never
client-side. Every group-scoped endpoint resolves an *active* membership first.
"""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Cookie, Depends, HTTPException, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from . import models as m

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./intercede.db")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
APP_ENV = os.environ.get("APP_ENV", "dev")
if APP_ENV == "production" and SECRET_KEY == "dev-only-change-me":  # noqa: S105 — detecting the default, not storing a secret
    raise RuntimeError("SECRET_KEY must be set to a long random value in production")
SESSION_MAX_AGE = 60 * 60 * 24 * 14  # 14 days
COOKIE_NAME = "intercede_session"

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

hasher = PasswordHasher()  # argon2id by default
signer = URLSafeTimedSerializer(SECRET_KEY, salt="session")


async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


def hash_password(pw: str) -> str:
    return hasher.hash(pw)


def verify_password(pw: str, pw_hash: str) -> bool:
    try:
        return hasher.verify(pw_hash, pw)
    except VerifyMismatchError:
        return False


# verified against when the email doesn't exist, so unknown-email and
# wrong-password logins cost the same time (no account enumeration via timing)
DUMMY_HASH = PasswordHasher().hash("timing-parity-dummy")


def set_session_cookie(response: Response, user: m.User) -> None:
    # the epoch rides in the token so bumping User.session_epoch revokes
    # every session issued before the bump
    token = signer.dumps([user.id, user.session_epoch])
    response.set_cookie(
        COOKIE_NAME, token, max_age=SESSION_MAX_AGE,
        httponly=True, samesite="lax",
        secure=os.environ.get("COOKIE_SECURE", "0") == "1",  # set 1 behind TLS/tunnel
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME)


async def current_user(
    db: AsyncSession = Depends(get_db),
    intercede_session: str | None = Cookie(default=None),
) -> m.User:
    if not intercede_session:
        raise HTTPException(401, "Not signed in")
    try:
        user_id, epoch = signer.loads(intercede_session, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        raise HTTPException(401, "Session invalid or expired") from None
    user = await db.get(m.User, user_id)
    if not user or user.session_epoch != epoch:
        raise HTTPException(401, "Session invalid")
    return user


# serializes account creation so two concurrent first registrations can't both
# pass the count==0 check and both become site admin. Per-process, which is
# fine: registration is rate-limited anyway, and the race only exists during
# first boot. Multi-process deployments should create the first account before
# opening the app to traffic.
first_user_lock = asyncio.Lock()


async def bootstrap_site_admin(db: AsyncSession, user: m.User) -> None:
    """First account ever created administers the site (self-host bootstrap).

    Call before db.add(user) so the count query doesn't autoflush the new row,
    and only while holding first_user_lock through the commit.
    """
    count = (await db.execute(select(func.count()).select_from(m.User))).scalar_one()
    if count == 0:
        user.is_site_admin = True


async def cookie_user(
    db: AsyncSession = Depends(get_db),
    intercede_session: str | None = Cookie(default=None),
) -> m.User | None:
    """Best-effort resolution for logout: valid signature required, epoch ignored
    so signing out is idempotent even with an already-revoked cookie."""
    if not intercede_session:
        return None
    try:
        user_id, _epoch = signer.loads(intercede_session, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        return None
    return await db.get(m.User, user_id)


async def require_site_admin(user: m.User = Depends(current_user)) -> m.User:
    if not user.is_site_admin:
        raise HTTPException(403, "Site admin required")
    return user


async def active_membership(
    db: AsyncSession, group_id: str, user: m.User, roles: tuple[str, ...] | None = None
) -> m.GroupMembership:
    """Deny-by-default gate for every group-scoped endpoint."""
    q = select(m.GroupMembership).where(
        m.GroupMembership.group_id == group_id,
        m.GroupMembership.user_id == user.id,
        m.GroupMembership.status == "active",
    )
    ms = (await db.execute(q)).scalar_one_or_none()
    if ms is None:
        raise HTTPException(403, "Not a member of this group")
    if roles and ms.role not in roles:
        raise HTTPException(403, "Insufficient role")
    return ms


def visible_request_filter(ms: m.GroupMembership):
    """SQL predicate for which requests this membership may see.

    - leaders see everything in their group
    - authors always see their own requests (a leaders_only request must not
      vanish from the person who posted it)
    - privacy='group' visible to all active members
    - privacy='family_only' visible only if the request's subject family is
      the viewer's family (family subject: subject_id; member subject: the
      member's family_id, resolved via correlated subquery)
    - privacy='leaders_only' visible only to leaders (handled by first branch)
    """
    if ms.role == "leader":
        return m.PrayerRequest.group_id == ms.group_id

    member_family = (
        select(m.Member.family_id).where(m.Member.id == m.PrayerRequest.subject_id)
    ).scalar_subquery()

    family_only_visible = and_(
        m.PrayerRequest.privacy == "family_only",
        or_(
            and_(m.PrayerRequest.subject_type == "family",
                 m.PrayerRequest.subject_id == (ms.family_id or "")),
            and_(m.PrayerRequest.subject_type == "member",
                 member_family == (ms.family_id or "")),
        ),
    )
    return and_(
        m.PrayerRequest.group_id == ms.group_id,
        or_(m.PrayerRequest.privacy == "group",
            m.PrayerRequest.created_by == ms.user_id,
            family_only_visible),
    )


def today() -> date:
    # UTC everywhere: the "prayed today" idempotency boundary must not drift
    # with the server's local timezone (timestamps are already stored UTC)
    return datetime.now(UTC).date()
