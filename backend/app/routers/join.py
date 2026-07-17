"""QR-poster join flow: invite info, join, approval queue, rotation, poster SVG."""
from __future__ import annotations

import io
import os
import uuid

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models as m
from ..deps import (
    active_membership,
    bootstrap_site_admin,
    current_user,
    first_user_lock,
    get_db,
    hash_password,
    set_session_cookie,
)
from ..schemas import ApproveIn, JoinIn
from ..security import audit, rate_limit

router = APIRouter()


async def _materialize_membership(db: AsyncSession, ms: m.GroupMembership, user: m.User) -> None:
    """Create the deferred Family/Member rows once a join is (auto-)approved."""
    if ms.pending_family_name:
        fam = m.Family(group_id=ms.group_id, family_name=ms.pending_family_name)
        db.add(fam)
        await db.flush()
        ms.family_id = fam.id
        ms.pending_family_name = None
    db.add(m.Member(family_id=ms.family_id, display_name=user.display_name, user_id=user.id))


@router.get("/api/join/{code}")
async def join_info(code: str, db: AsyncSession = Depends(get_db)):
    group = (await db.execute(select(m.Group).where(m.Group.invite_code == code))).scalar_one_or_none()
    if not group:
        raise HTTPException(404, "Invite not found or expired")
    fams = (await db.execute(select(m.Family).where(
        m.Family.group_id == group.id, m.Family.deleted_at.is_(None))
        .order_by(m.Family.family_name))).scalars().all()
    return {"group_name": group.name, "approval_required": group.approval_required,
            "families": [{"id": f.id, "family_name": f.family_name} for f in fams]}


@router.post("/api/join/{code}", status_code=201)
async def join(code: str, body: JoinIn, request: Request, response: Response,
               db: AsyncSession = Depends(get_db)):
    rate_limit(request, "join")
    group = (await db.execute(select(m.Group).where(m.Group.invite_code == code))).scalar_one_or_none()
    if not group:
        raise HTTPException(404, "Invite not found or expired")
    if bool(body.family_id) == bool(body.new_family_name):
        raise HTTPException(422, "Choose an existing family or name a new one (not both)")

    # single transaction: user + membership (+ family/member rows only when no
    # approval gate — a pending join must leave no trace on the wall until the
    # leader approves, and a denied one nothing to clean up)
    user = m.User(email=body.email.lower(), password_hash=hash_password(body.password),
                  display_name=body.display_name)
    async with first_user_lock:
        await bootstrap_site_admin(db, user)
        db.add(user)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(409, "An account with that email already exists — sign in instead") from None

        if body.family_id:
            fam = await db.get(m.Family, body.family_id)
            if not fam or fam.group_id != group.id or fam.deleted_at:
                raise HTTPException(422, "That family is not part of this group")

        status = "pending" if group.approval_required else "active"
        ms = m.GroupMembership(user_id=user.id, group_id=group.id, role="member", status=status,
                               family_id=body.family_id,
                               pending_family_name=body.new_family_name)
        db.add(ms)
        if status == "active":
            await _materialize_membership(db, ms, user)
        audit(db, "join.requested", group_id=group.id, actor_id=user.id, detail=status)
        await db.commit()
    set_session_cookie(response, user)
    return {"status": status, "group_name": group.name,
            "message": "Your leader will approve you shortly" if status == "pending" else "Welcome!"}


@router.get("/api/groups/{gid}/join-queue")
async def join_queue(gid: str, user: m.User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await active_membership(db, gid, user, roles=("leader",))
    q = (select(m.GroupMembership, m.User.display_name, m.User.email, m.Family.family_name)
         .join(m.User, m.User.id == m.GroupMembership.user_id)
         .outerjoin(m.Family, m.Family.id == m.GroupMembership.family_id)
         .where(m.GroupMembership.group_id == gid, m.GroupMembership.status == "pending"))
    rows = (await db.execute(q)).all()
    return [{"membership_id": ms.id, "display_name": name, "email": email,
             "family": f"starting “{ms.pending_family_name}”" if ms.pending_family_name
                       else (f"joining {family_name}" if family_name else None)}
            for ms, name, email, family_name in rows]


@router.patch("/api/join-queue/{membership_id}")
async def approve(membership_id: str, body: ApproveIn,
                  user: m.User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    ms = await db.get(m.GroupMembership, membership_id)
    if not ms or ms.status != "pending":
        raise HTTPException(404, "Pending membership not found")
    await active_membership(db, ms.group_id, user, roles=("leader",))
    if body.approve:
        if ms.family_id and not ms.pending_family_name:
            fam = await db.get(m.Family, ms.family_id)
            if not fam or fam.deleted_at:
                raise HTTPException(409, "The family they chose no longer exists — deny so they can rejoin")
        joiner = await db.get(m.User, ms.user_id)
        await _materialize_membership(db, ms, joiner)
        ms.status = "active"
        audit(db, "join.approved", group_id=ms.group_id, actor_id=user.id, target_id=ms.user_id)
        await db.commit()
        return {"status": "active"}
    # deferred materialization means a denied join leaves nothing behind
    audit(db, "join.denied", group_id=ms.group_id, actor_id=user.id, target_id=ms.user_id)
    await db.delete(ms)
    await db.commit()
    return {"status": "denied"}


@router.post("/api/groups/{gid}/invite/rotate")
async def rotate_invite(gid: str, user: m.User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await active_membership(db, gid, user, roles=("leader",))
    group = await db.get(m.Group, gid)
    group.invite_code = uuid.uuid4().hex
    audit(db, "invite.rotated", group_id=gid, actor_id=user.id)
    await db.commit()
    return {"invite_code": group.invite_code}


@router.get("/api/groups/{gid}/invite/poster.svg")
async def poster(gid: str, user: m.User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await active_membership(db, gid, user, roles=("leader",))
    group = await db.get(m.Group, gid)
    base = os.environ.get("PUBLIC_BASE_URL", "http://localhost:5173")
    img = qrcode.make(f"{base}/join/{group.invite_code}",
                      image_factory=qrcode.image.svg.SvgPathImage, box_size=16)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml")
