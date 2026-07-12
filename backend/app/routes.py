"""API routes for the vertical slice."""
from __future__ import annotations

import io
import uuid

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from . import models as m
from .deps import (
    DUMMY_HASH,
    active_membership,
    bootstrap_site_admin,
    clear_session_cookie,
    current_user,
    get_db,
    hash_password,
    require_site_admin,
    set_session_cookie,
    today,
    verify_password,
    visible_request_filter,
)
from .security import audit, rate_limit

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
async def register(body: RegisterIn, request: Request, response: Response,
                   db: AsyncSession = Depends(get_db)):
    rate_limit(request, "register")
    user = m.User(email=body.email.lower(), password_hash=hash_password(body.password),
                  display_name=body.display_name)
    await bootstrap_site_admin(db, user)
    db.add(user)
    try:
        await db.flush()
        audit(db, "auth.register", actor_id=user.id)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "An account with that email already exists") from None
    set_session_cookie(response, user.id)
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
    set_session_cookie(response, user.id)
    return {"id": user.id, "display_name": user.display_name}


@router.post("/api/auth/logout")
async def logout(response: Response):
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/api/me")
async def me(user: m.User = Depends(current_user)):
    return {"id": user.id, "email": user.email, "display_name": user.display_name,
            "is_site_admin": user.is_site_admin}


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
    if "status" in data and data["status"] != req.status:
        audit(db, "request.status_changed", group_id=req.group_id, actor_id=user.id,
              target_id=req.id, detail=f"{req.status} -> {data['status']}")
    for k, v in data.items():
        setattr(req, k, v)
    await db.commit()
    return {"id": req.id, "status": req.status, "answered_at": req.answered_at}


async def _visible_request(db: AsyncSession, rid: str, user: m.User) -> m.PrayerRequest:
    """Load a request the user is allowed to see, else 404 (existence is not leaked)."""
    req = await db.get(m.PrayerRequest, rid)
    if not req or req.deleted_at:
        raise HTTPException(404, "Request not found")
    ms = await active_membership(db, req.group_id, user)
    vis = (await db.execute(select(m.PrayerRequest.id).where(
        m.PrayerRequest.id == rid, visible_request_filter(ms)))).first()
    if not vis:
        raise HTTPException(404, "Request not found")
    return req


@router.post("/api/requests/{rid}/prayed")
async def prayed(rid: str, user: m.User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await _visible_request(db, rid, user)
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
    req = await _visible_request(db, rid, user)
    pack = (await db.execute(select(m.Verse).where(m.Verse.category_slug == req.category_slug)
                             .order_by(m.Verse.position))).scalars().all()
    if not pack:
        raise HTTPException(404, "No verses for this category")
    if shuffle:
        req.verse_index = (req.verse_index + 1) % len(pack)
        await db.commit()
    v = pack[req.verse_index % len(pack)]
    return {"reference": v.reference, "text": v.text, "translation": v.translation}


# ------------------------------------------------------------- updates ----

class UpdateIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


@router.post("/api/requests/{rid}/updates", status_code=201)
async def add_update(rid: str, body: UpdateIn, user: m.User = Depends(current_user),
                     db: AsyncSession = Depends(get_db)):
    req = await _visible_request(db, rid, user)
    ms = await active_membership(db, req.group_id, user)
    if req.created_by != user.id and ms.role != "leader":
        raise HTTPException(403, "Only the author or a leader can post updates")
    upd = m.RequestUpdate(request_id=rid, author_id=user.id, body=body.body)
    db.add(upd)
    await db.commit()
    return {"id": upd.id, "created_at": upd.created_at.isoformat()}


@router.get("/api/requests/{rid}/updates")
async def list_updates(rid: str, user: m.User = Depends(current_user),
                       db: AsyncSession = Depends(get_db)):
    await _visible_request(db, rid, user)
    q = (select(m.RequestUpdate, m.User.display_name)
         .join(m.User, m.User.id == m.RequestUpdate.author_id)
         .where(m.RequestUpdate.request_id == rid)
         .order_by(m.RequestUpdate.created_at))
    rows = (await db.execute(q)).all()
    return [{"id": u.id, "body": u.body, "author": name,
             "created_at": u.created_at.isoformat()} for u, name in rows]


# ------------------------------------------------------- soft deletion ----

@router.delete("/api/requests/{rid}")
async def delete_request(rid: str, user: m.User = Depends(current_user),
                         db: AsyncSession = Depends(get_db)):
    req = await db.get(m.PrayerRequest, rid)
    if not req or req.deleted_at:
        raise HTTPException(404, "Request not found")
    ms = await active_membership(db, req.group_id, user)
    if req.created_by != user.id and ms.role != "leader":
        raise HTTPException(403, "Only the author or a leader can delete this request")
    req.deleted_at = m.utcnow()
    audit(db, "request.deleted", group_id=req.group_id, actor_id=user.id, target_id=rid)
    await db.commit()
    return {"ok": True}


async def _soft_delete_subject_requests(db: AsyncSession, subject_type: str, subject_id: str):
    reqs = (await db.execute(select(m.PrayerRequest).where(
        m.PrayerRequest.subject_type == subject_type,
        m.PrayerRequest.subject_id == subject_id,
        m.PrayerRequest.deleted_at.is_(None)))).scalars().all()
    for r in reqs:
        r.deleted_at = m.utcnow()


@router.delete("/api/families/{fid}")
async def delete_family(fid: str, user: m.User = Depends(current_user),
                        db: AsyncSession = Depends(get_db)):
    fam = await db.get(m.Family, fid)
    if not fam or fam.deleted_at:
        raise HTTPException(404, "Family not found")
    await active_membership(db, fam.group_id, user, roles=("leader",))
    fam.deleted_at = m.utcnow()
    members = (await db.execute(select(m.Member).where(
        m.Member.family_id == fid, m.Member.deleted_at.is_(None)))).scalars().all()
    for mem in members:
        mem.deleted_at = m.utcnow()
        await _soft_delete_subject_requests(db, "member", mem.id)
    await _soft_delete_subject_requests(db, "family", fid)
    audit(db, "family.deleted", group_id=fam.group_id, actor_id=user.id, target_id=fid)
    await db.commit()
    return {"ok": True}


@router.delete("/api/members/{mid}")
async def delete_member(mid: str, user: m.User = Depends(current_user),
                        db: AsyncSession = Depends(get_db)):
    member = await db.get(m.Member, mid)
    if not member or member.deleted_at:
        raise HTTPException(404, "Member not found")
    fam = await db.get(m.Family, member.family_id)
    await active_membership(db, fam.group_id, user, roles=("leader",))
    member.deleted_at = m.utcnow()
    await _soft_delete_subject_requests(db, "member", mid)
    audit(db, "member.deleted", group_id=fam.group_id, actor_id=user.id, target_id=mid)
    await db.commit()
    return {"ok": True}


# ----------------------------------------------------------- audit log ----

@router.get("/api/groups/{gid}/audit")
async def group_audit(gid: str, user: m.User = Depends(current_user),
                      db: AsyncSession = Depends(get_db)):
    await active_membership(db, gid, user, roles=("leader",))
    rows = (await db.execute(select(m.AuditLog).where(m.AuditLog.group_id == gid)
                             .order_by(m.AuditLog.created_at.desc()).limit(100))).scalars().all()
    return [{"id": a.id, "action": a.action, "actor_id": a.actor_id, "target_id": a.target_id,
             "detail": a.detail, "created_at": a.created_at.isoformat()} for a in rows]


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

    prayed_ids = set((await db.execute(
        select(m.PrayerAction.request_id).where(
            m.PrayerAction.user_id == user.id, m.PrayerAction.prayed_on == today(),
            m.PrayerAction.request_id.in_([r.id for r in reqs] or [""])))).scalars().all())

    packs: dict[str, list[m.Verse]] = {}
    verses = (await db.execute(select(m.Verse).where(
        m.Verse.category_slug.in_(list({r.category_slug for r in reqs}) or [""]))
        .order_by(m.Verse.position))).scalars().all()
    for v in verses:
        packs.setdefault(v.category_slug, []).append(v)

    def req_json(r: m.PrayerRequest):
        pack = packs.get(r.category_slug, [])
        v = pack[r.verse_index % len(pack)] if pack else None
        return {"id": r.id, "title": r.title, "body": r.body, "category": r.category_slug,
                "status": r.status, "privacy": r.privacy, "is_urgent": r.is_urgent,
                "subject_type": r.subject_type, "subject_id": r.subject_id,
                "prayer_count": counts.get(r.id, 0), "created_at": r.created_at.isoformat(),
                "created_by": r.created_by, "prayed_today": r.id in prayed_ids,
                "verse": {"reference": v.reference, "text": v.text,
                          "translation": v.translation} if v else None}

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
async def join(code: str, body: JoinIn, request: Request, response: Response,
               db: AsyncSession = Depends(get_db)):
    rate_limit(request, "join")
    group = (await db.execute(select(m.Group).where(m.Group.invite_code == code))).scalar_one_or_none()
    if not group:
        raise HTTPException(404, "Invite not found or expired")
    if bool(body.family_id) == bool(body.new_family_name):
        raise HTTPException(422, "Choose an existing family or name a new one (not both)")

    # single transaction: user + (family?) + member record + membership
    user = m.User(email=body.email.lower(), password_hash=hash_password(body.password),
                  display_name=body.display_name)
    await bootstrap_site_admin(db, user)
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "An account with that email already exists — sign in instead") from None

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
    audit(db, "join.requested", group_id=group.id, actor_id=user.id, detail=status)
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
        audit(db, "join.approved", group_id=ms.group_id, actor_id=user.id, target_id=ms.user_id)
        await db.commit()
        return {"status": "active"}
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
    import os
    base = os.environ.get("PUBLIC_BASE_URL", "http://localhost:5173")
    img = qrcode.make(f"{base}/join/{group.invite_code}",
                      image_factory=qrcode.image.svg.SvgPathImage, box_size=16)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml")


# ------------------------------------------------------- role management ----

class RolePatch(BaseModel):
    role: str = Field(pattern="^(leader|steward|member|viewer)$")


async def _group_members(db: AsyncSession, gid: str) -> list[dict]:
    q = (select(m.GroupMembership, m.User)
         .join(m.User, m.User.id == m.GroupMembership.user_id)
         .where(m.GroupMembership.group_id == gid)
         .order_by(m.User.display_name))
    rows = (await db.execute(q)).all()
    return [{"user_id": u.id, "membership_id": ms.id, "display_name": u.display_name,
             "email": u.email, "role": ms.role, "status": ms.status} for ms, u in rows]


async def _set_role(db: AsyncSession, gid: str, user_id: str, new_role: str,
                    actor: m.User) -> m.GroupMembership:
    ms = (await db.execute(select(m.GroupMembership).where(
        m.GroupMembership.group_id == gid, m.GroupMembership.user_id == user_id,
        m.GroupMembership.status == "active"))).scalar_one_or_none()
    if ms is None:
        raise HTTPException(404, "Member not found")
    old = ms.role
    if old == "leader" and new_role != "leader":
        leaders = (await db.execute(select(func.count()).select_from(m.GroupMembership).where(
            m.GroupMembership.group_id == gid, m.GroupMembership.role == "leader",
            m.GroupMembership.status == "active"))).scalar_one()
        if leaders <= 1:
            raise HTTPException(409, "Promote another admin first")
    ms.role = new_role
    audit(db, "role_changed", group_id=gid, actor_id=actor.id, target_id=user_id,
          detail=f"{old} -> {new_role}")
    await db.commit()
    return ms


@router.get("/api/groups/{gid}/members")
async def group_members(gid: str, user: m.User = Depends(current_user),
                        db: AsyncSession = Depends(get_db)):
    await active_membership(db, gid, user, roles=("leader",))
    return await _group_members(db, gid)


@router.patch("/api/groups/{gid}/members/{user_id}")
async def set_member_role(gid: str, user_id: str, body: RolePatch,
                          user: m.User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await active_membership(db, gid, user, roles=("leader",))
    ms = await _set_role(db, gid, user_id, body.role, user)
    return {"user_id": user_id, "role": ms.role}


# ----------------------------------------------------------- site admin ----

@router.get("/api/admin/overview")
async def admin_overview(admin: m.User = Depends(require_site_admin),
                         db: AsyncSession = Depends(get_db)):
    async def count(stmt) -> int:
        return (await db.execute(stmt)).scalar_one()

    return {
        "users": await count(select(func.count()).select_from(m.User)),
        "groups": await count(select(func.count()).select_from(m.Group)),
        "families": await count(select(func.count()).select_from(m.Family)
                                .where(m.Family.deleted_at.is_(None))),
        "requests": await count(select(func.count()).select_from(m.PrayerRequest)
                                .where(m.PrayerRequest.deleted_at.is_(None))),
        "prayers": await count(select(func.count()).select_from(m.PrayerAction)),
    }


@router.get("/api/admin/groups")
async def admin_groups(admin: m.User = Depends(require_site_admin),
                       db: AsyncSession = Depends(get_db)):
    groups = (await db.execute(select(m.Group).order_by(m.Group.name))).scalars().all()
    counts = dict((await db.execute(
        select(m.GroupMembership.group_id, func.count())
        .where(m.GroupMembership.status == "active")
        .group_by(m.GroupMembership.group_id))).all())
    leaders: dict[str, list[str]] = {}
    leader_rows = (await db.execute(
        select(m.GroupMembership.group_id, m.User.display_name)
        .join(m.User, m.User.id == m.GroupMembership.user_id)
        .where(m.GroupMembership.role == "leader", m.GroupMembership.status == "active")
        .order_by(m.User.display_name))).all()
    for gid, name in leader_rows:
        leaders.setdefault(gid, []).append(name)
    return [{"id": g.id, "name": g.name, "created_at": g.created_at.isoformat(),
             "approval_required": g.approval_required, "member_count": counts.get(g.id, 0),
             "leaders": leaders.get(g.id, [])} for g in groups]


@router.get("/api/admin/groups/{gid}/members")
async def admin_group_members(gid: str, admin: m.User = Depends(require_site_admin),
                              db: AsyncSession = Depends(get_db)):
    if not await db.get(m.Group, gid):
        raise HTTPException(404, "Group not found")
    return await _group_members(db, gid)


@router.patch("/api/admin/groups/{gid}/members/{user_id}")
async def admin_set_member_role(gid: str, user_id: str, body: RolePatch,
                                admin: m.User = Depends(require_site_admin),
                                db: AsyncSession = Depends(get_db)):
    ms = await _set_role(db, gid, user_id, body.role, admin)
    return {"user_id": user_id, "role": ms.role}


@router.get("/api/admin/users")
async def admin_users(admin: m.User = Depends(require_site_admin),
                      db: AsyncSession = Depends(get_db)):
    users = (await db.execute(select(m.User).order_by(m.User.created_at).limit(500))).scalars().all()
    return [{"id": u.id, "email": u.email, "display_name": u.display_name,
             "is_site_admin": u.is_site_admin, "created_at": u.created_at.isoformat()} for u in users]


class SiteAdminPatch(BaseModel):
    is_site_admin: bool


@router.patch("/api/admin/users/{uid}")
async def admin_set_site_admin(uid: str, body: SiteAdminPatch,
                               admin: m.User = Depends(require_site_admin),
                               db: AsyncSession = Depends(get_db)):
    target = await db.get(m.User, uid)
    if not target:
        raise HTTPException(404, "User not found")
    if target.is_site_admin and not body.is_site_admin:
        admins = (await db.execute(select(func.count()).select_from(m.User)
                                   .where(m.User.is_site_admin.is_(True)))).scalar_one()
        if admins <= 1:
            raise HTTPException(409, "At least one site admin is required")
    target.is_site_admin = body.is_site_admin
    audit(db, "site_admin_granted" if body.is_site_admin else "site_admin_revoked",
          actor_id=admin.id, target_id=uid)
    await db.commit()
    return {"id": target.id, "is_site_admin": target.is_site_admin}
