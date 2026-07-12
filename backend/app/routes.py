"""API routes for the vertical slice."""
from __future__ import annotations

import io
import uuid

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from .deps import (
    active_membership, clear_session_cookie, current_user, get_db,
    hash_password, set_session_cookie, today, verify_password, visible_request_filter,
)

router = APIRouter()

# ---------------------------------------------------------------- auth ----

class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


@router.post("/api/auth/register", status_code=201)
async def register(body: RegisterIn, response: Response, db: AsyncSession = Depends(get_db)):
    user = m.User(email=body.email.lower(), password_hash=hash_password(body.password),
                  display_name=body.display_name)
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "An account with that email already exists")
    set_session_cookie(response, user.id)
    return {"id": user.id, "display_name": user.display_name}


@router.post("/api/auth/login")
async def login(body: LoginIn, response: Response, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(m.User).where(m.User.email == body.email.lower()))).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Email or password is incorrect")
    set_session_cookie(response, user.id)
    return {"id": user.id, "display_name": user.display_name}


@router.post("/api/auth/logout")
async def logout(response: Response):
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/api/me")
async def me(user: m.User = Depends(current_user)):
    return {"id": user.id, "email": user.email, "display_name": user.display_name}


# -------------------------------------------------------------- groups ----

class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    approval_required: bool = True


@router.post("/api/groups", status_code=201)
async def create_group(body: GroupIn, user: m.User = Depends(current_user),
                       db: AsyncSession = Depends(get_db)):
    group = m.Group(name=body.name, created_by=user.id, approval_required=body.approval_required)
    db.add(group)
    await db.flush()
    db.add(m.GroupMembership(user_id=user.id, group_id=group.id, role="leader", status="active"))
    await db.commit()
    return {"id": group.id, "name": group.name, "invite_code": group.invite_code}


@router.get("/api/groups")
async def my_groups(user: m.User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    q = (select(m.Group, m.GroupMembership.role)
         .join(m.GroupMembership, m.GroupMembership.group_id == m.Group.id)
         .where(m.GroupMembership.user_id == user.id, m.GroupMembership.status == "active"))
    rows = (await db.execute(q)).all()
    return [{"id": g.id, "name": g.name, "role": role,
             "invite_code": g.invite_code if role == "leader" else None} for g, role in rows]


# ----------------------------------------------------------- hierarchy ----

class FamilyIn(BaseModel):
    family_name: str = Field(min_length=1, max_length=120)


class MemberIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    relationship_label: str | None = Field(default=None, max_length=60)


@router.post("/api/groups/{gid}/families", status_code=201)
async def create_family(gid: str, body: FamilyIn, user: m.User = Depends(current_user),
                        db: AsyncSession = Depends(get_db)):
    await active_membership(db, gid, user, roles=("leader", "steward", "member"))
    fam = m.Family(group_id=gid, family_name=body.family_name)
    db.add(fam)
    await db.commit()
    return {"id": fam.id, "family_name": fam.family_name}


@router.post("/api/families/{fid}/members", status_code=201)
async def add_member(fid: str, body: MemberIn, user: m.User = Depends(current_user),
                     db: AsyncSession = Depends(get_db)):
    fam = await db.get(m.Family, fid)
    if not fam or fam.deleted_at:
        raise HTTPException(404, "Family not found")
    ms = await active_membership(db, fam.group_id, user)
    # stewards/members may only add to their own family; leaders anywhere
    if ms.role != "leader" and ms.family_id != fid:
        raise HTTPException(403, "You can only add members to your own family")
    member = m.Member(family_id=fid, display_name=body.display_name,
                      relationship_label=body.relationship_label)
    db.add(member)
    await db.commit()
    return {"id": member.id, "display_name": member.display_name}


# ------------------------------------------------------------ requests ----

class RequestIn(BaseModel):
    subject_type: str = Field(pattern="^(family|member)$")
    subject_id: str
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(default="", max_length=4000)
    category_slug: str = "general"
    privacy: str = Field(default="group", pattern="^(group|leaders_only|family_only)$")
    is_urgent: bool = False


async def _subject_group(db: AsyncSession, subject_type: str, subject_id: str) -> str | None:
    if subject_type == "family":
        fam = await db.get(m.Family, subject_id)
        return fam.group_id if fam and not fam.deleted_at else None
    member = await db.get(m.Member, subject_id)
    if not member or member.deleted_at:
        return None
    fam = await db.get(m.Family, member.family_id)
    return fam.group_id if fam and not fam.deleted_at else None


@router.post("/api/groups/{gid}/requests", status_code=201)
async def create_request(gid: str, body: RequestIn, user: m.User = Depends(current_user),
                         db: AsyncSession = Depends(get_db)):
    await active_membership(db, gid, user)
    subject_gid = await _subject_group(db, body.subject_type, body.subject_id)
    if subject_gid != gid:
        raise HTTPException(422, "Subject does not belong to this group")
    if not (await db.execute(select(m.Verse.id).where(
            m.Verse.category_slug == body.category_slug).limit(1))).first():
        raise HTTPException(422, "Unknown category")
    req = m.PrayerRequest(group_id=gid, created_by=user.id, **body.model_dump())
    db.add(req)
    await db.commit()
    return {"id": req.id, "title": req.title, "status": req.status}


class RequestPatch(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    body: str | None = Field(default=None, max_length=4000)
    status: str | None = Field(default=None, pattern="^(active|answered|ongoing|archived)$")
    answer_note: str | None = Field(default=None, max_length=4000)
    is_urgent: bool | None = None


@router.patch("/api/requests/{rid}")
async def patch_request(rid: str, body: RequestPatch, user: m.User = Depends(current_user),
                        db: AsyncSession = Depends(get_db)):
    req = await db.get(m.PrayerRequest, rid)
    if not req or req.deleted_at:
        raise HTTPException(404, "Request not found")
    ms = await active_membership(db, req.group_id, user)
    if req.created_by != user.id and ms.role != "leader":
        raise HTTPException(403, "Only the author or a leader can edit this request")
    data = body.model_dump(exclude_none=True)
    if data.get("status") == "answered":
        req.answered_at = m.utcnow()
        req.answer_note = data.pop("answer_note", None)
    for k, v in data.items():
        setattr(req, k, v)
    await db.commit()
    return {"id": req.id, "status": req.status, "answered_at": req.answered_at}


@router.post("/api/requests/{rid}/prayed")
async def prayed(rid: str, user: m.User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    req = await db.get(m.PrayerRequest, rid)
    if not req or req.deleted_at:
        raise HTTPException(404, "Request not found")
    ms = await active_membership(db, req.group_id, user)
    # must be able to *see* the request to pray for it
    vis = (await db.execute(select(m.PrayerRequest.id).where(
        m.PrayerRequest.id == rid, visible_request_filter(ms)))).first()
    if not vis:
        raise HTTPException(404, "Request not found")
    db.add(m.PrayerAction(request_id=rid, user_id=user.id, prayed_on=today()))
    try:
        await db.commit()
        added = True
    except IntegrityError:  # already prayed today — idempotent
        await db.rollback()
        added = False
    count = (await db.execute(select(func.count()).select_from(m.PrayerAction)
                              .where(m.PrayerAction.request_id == rid))).scalar_one()
    return {"prayed_today": True, "newly_recorded": added, "total_prayers": count}


@router.get("/api/requests/{rid}/verse")
async def request_verse(rid: str, shuffle: bool = False,
                        user: m.User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    req = await db.get(m.PrayerRequest, rid)
    if not req or req.deleted_at:
        raise HTTPException(404, "Request not found")
    ms = await active_membership(db, req.group_id, user)
    vis = (await db.execute(select(m.PrayerRequest.id).where(
        m.PrayerRequest.id == rid, visible_request_filter(ms)))).first()
    if not vis:
        raise HTTPException(404, "Request not found")
    pack = (await db.execute(select(m.Verse).where(m.Verse.category_slug == req.category_slug)
                             .order_by(m.Verse.position))).scalars().all()
    if not pack:
        raise HTTPException(404, "No verses for this category")
    if shuffle:
        req.verse_index = (req.verse_index + 1) % len(pack)
        await db.commit()
    v = pack[req.verse_index % len(pack)]
    return {"reference": v.reference, "text": v.text, "translation": v.translation}


# ------------------------------------------------------------ the wall ----

@router.get("/api/groups/{gid}/wall")
async def wall(gid: str, user: m.User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    ms = await active_membership(db, gid, user)
    reqs = (await db.execute(
        select(m.PrayerRequest).where(
            visible_request_filter(ms),
            m.PrayerRequest.deleted_at.is_(None),
            m.PrayerRequest.status.in_(("active", "ongoing")),
        ).order_by(m.PrayerRequest.created_at.desc())
    )).scalars().all()

    counts = dict((await db.execute(
        select(m.PrayerAction.request_id, func.count())
        .where(m.PrayerAction.request_id.in_([r.id for r in reqs] or [""]))
        .group_by(m.PrayerAction.request_id))).all())

    def req_json(r: m.PrayerRequest):
        return {"id": r.id, "title": r.title, "body": r.body, "category": r.category_slug,
                "status": r.status, "privacy": r.privacy, "is_urgent": r.is_urgent,
                "subject_type": r.subject_type, "subject_id": r.subject_id,
                "prayer_count": counts.get(r.id, 0), "created_at": r.created_at.isoformat()}

    families = (await db.execute(select(m.Family).where(
        m.Family.group_id == gid, m.Family.deleted_at.is_(None))
        .order_by(m.Family.family_name))).scalars().all()
    members = (await db.execute(select(m.Member).where(
        m.Member.family_id.in_([f.id for f in families] or [""]),
        m.Member.deleted_at.is_(None)))).scalars().all()
    by_family_members: dict[str, list[m.Member]] = {}
    for mem in members:
        by_family_members.setdefault(mem.family_id, []).append(mem)

    fam_reqs: dict[str, list] = {}
    mem_reqs: dict[str, list] = {}
    for r in reqs:
        (fam_reqs if r.subject_type == "family" else mem_reqs).setdefault(r.subject_id, []).append(req_json(r))

    return {
        "urgent": [req_json(r) for r in reqs if r.is_urgent],
        "families": [{
            "id": f.id, "family_name": f.family_name,
            "requests": fam_reqs.get(f.id, []),
            "members": [{
                "id": mem.id, "display_name": mem.display_name,
                "relationship_label": mem.relationship_label,
                "requests": mem_reqs.get(mem.id, []),
            } for mem in by_family_members.get(f.id, [])],
        } for f in families],
    }


@router.get("/api/groups/{gid}/praise-wall")
async def praise_wall(gid: str, user: m.User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    ms = await active_membership(db, gid, user)
    reqs = (await db.execute(select(m.PrayerRequest).where(
        visible_request_filter(ms), m.PrayerRequest.status == "answered",
        m.PrayerRequest.deleted_at.is_(None))
        .order_by(m.PrayerRequest.answered_at.desc()))).scalars().all()
    return [{"id": r.id, "title": r.title, "answer_note": r.answer_note,
             "answered_at": r.answered_at.isoformat() if r.answered_at else None} for r in reqs]


# ---------------------------------------------------------- QR join flow ----

class JoinIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    family_id: str | None = None          # join an existing family…
    new_family_name: str | None = Field(default=None, max_length=120)  # …or create one


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
async def join(code: str, body: JoinIn, response: Response, db: AsyncSession = Depends(get_db)):
    group = (await db.execute(select(m.Group).where(m.Group.invite_code == code))).scalar_one_or_none()
    if not group:
        raise HTTPException(404, "Invite not found or expired")
    if bool(body.family_id) == bool(body.new_family_name):
        raise HTTPException(422, "Choose an existing family or name a new one (not both)")

    # single transaction: user + (family?) + member record + membership
    user = m.User(email=body.email.lower(), password_hash=hash_password(body.password),
                  display_name=body.display_name)
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "An account with that email already exists — sign in instead")

    if body.new_family_name:
        fam = m.Family(group_id=group.id, family_name=body.new_family_name)
        db.add(fam)
        await db.flush()
    else:
        fam = await db.get(m.Family, body.family_id)
        if not fam or fam.group_id != group.id or fam.deleted_at:
            raise HTTPException(422, "That family is not part of this group")

    db.add(m.Member(family_id=fam.id, display_name=body.display_name, user_id=user.id))
    status = "pending" if group.approval_required else "active"
    db.add(m.GroupMembership(user_id=user.id, group_id=group.id, role="member",
                             status=status, family_id=fam.id))
    await db.commit()
    set_session_cookie(response, user.id)
    return {"status": status, "group_name": group.name,
            "message": "Your leader will approve you shortly" if status == "pending" else "Welcome!"}


@router.get("/api/groups/{gid}/join-queue")
async def join_queue(gid: str, user: m.User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await active_membership(db, gid, user, roles=("leader",))
    q = (select(m.GroupMembership, m.User.display_name, m.User.email)
         .join(m.User, m.User.id == m.GroupMembership.user_id)
         .where(m.GroupMembership.group_id == gid, m.GroupMembership.status == "pending"))
    rows = (await db.execute(q)).all()
    return [{"membership_id": ms.id, "display_name": name, "email": email} for ms, name, email in rows]


class ApproveIn(BaseModel):
    approve: bool


@router.patch("/api/join-queue/{membership_id}")
async def approve(membership_id: str, body: ApproveIn,
                  user: m.User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    ms = await db.get(m.GroupMembership, membership_id)
    if not ms or ms.status != "pending":
        raise HTTPException(404, "Pending membership not found")
    await active_membership(db, ms.group_id, user, roles=("leader",))
    if body.approve:
        ms.status = "active"
        await db.commit()
        return {"status": "active"}
    await db.delete(ms)
    await db.commit()
    return {"status": "denied"}


@router.post("/api/groups/{gid}/invite/rotate")
async def rotate_invite(gid: str, user: m.User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await active_membership(db, gid, user, roles=("leader",))
    group = await db.get(m.Group, gid)
    group.invite_code = uuid.uuid4().hex
    await db.commit()
    return {"invite_code": group.invite_code}


@router.get("/api/groups/{gid}/invite/poster.svg")
async def poster(gid: str, user: m.User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await active_membership(db, gid, user, roles=("leader",))
    group = await db.get(m.Group, gid)
    import os
    base = os.environ.get("PUBLIC_BASE_URL", "http://localhost:5173")
    img = qrcode.make(f"{base}/join/{group.invite_code}",
                      image_factory=qrcode.image.svg.SvgPathImage, box_size=16)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml")
