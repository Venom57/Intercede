"""Account registration, login, logout, and the current-user probe."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models as m
from ..deps import (
    DUMMY_HASH,
    bootstrap_site_admin,
    clear_session_cookie,
    cookie_user,
    current_user,
    first_user_lock,
    get_db,
    hash_password,
    set_session_cookie,
    verify_password,
)
from ..schemas import LoginIn, RegisterIn
from ..security import audit, rate_limit

router = APIRouter()


@router.post("/api/auth/register", status_code=201)
async def register(body: RegisterIn, request: Request, response: Response,
                   db: AsyncSession = Depends(get_db)):
    rate_limit(request, "register")
    user = m.User(email=body.email.lower(), password_hash=hash_password(body.password),
                  display_name=body.display_name)
    async with first_user_lock:
        await bootstrap_site_admin(db, user)
        db.add(user)
        try:
            await db.flush()
            audit(db, "auth.register", actor_id=user.id)
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(409, "An account with that email already exists") from None
    set_session_cookie(response, user)
    return {"id": user.id, "display_name": user.display_name}


@router.post("/api/auth/login")
async def login(body: LoginIn, request: Request, response: Response,
                db: AsyncSession = Depends(get_db)):
    rate_limit(request, "login")
    user = (await db.execute(select(m.User).where(m.User.email == body.email.lower()))).scalar_one_or_none()
    ok = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
    if not user or not ok:
        audit(db, "auth.login_failed", actor_id=user.id if user else None,
              detail=body.email.lower()[:255])
        await db.commit()
        raise HTTPException(401, "Email or password is incorrect")
    audit(db, "auth.login", actor_id=user.id)
    await db.commit()
    set_session_cookie(response, user)
    return {"id": user.id, "display_name": user.display_name}


@router.post("/api/auth/logout")
async def logout(response: Response, user: m.User | None = Depends(cookie_user),
                 db: AsyncSession = Depends(get_db)):
    # bumping the epoch revokes every outstanding session for this user
    # (sign-out signs out all devices), not just the cookie we clear here
    if user:
        user.session_epoch += 1
        await db.commit()
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/api/me")
async def me(user: m.User = Depends(current_user)):
    return {"id": user.id, "email": user.email, "display_name": user.display_name,
            "is_site_admin": user.is_site_admin}
