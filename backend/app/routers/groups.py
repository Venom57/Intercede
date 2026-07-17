"""Groups, the Family → Member hierarchy, the wall rollups, roles, and audit."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models as m
from ..deps import active_membership, current_user, get_db, today, visible_request_filter
from ..schemas import FamilyIn, GroupIn, MemberIn, RolePatch
from ..security import audit

router = APIRouter()


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
    # pending memberships are included (with status) so the app can show a
    # "waiting for approval" state instead of an empty create-a-group screen
    q = (select(m.Group, m.GroupMembership.role, m.GroupMembership.status)
         .join(m.GroupMembership, m.GroupMembership.group_id == m.Group.id)
         .where(m.GroupMembership.user_id == user.id,
                m.GroupMembership.status.in_(("active", "pending"))))
    rows = (await db.execute(q)).all()
    return [{"id": g.id, "name": g.name, "role": role, "status": status,
             "invite_code": g.invite_code if role == "leader" and status == "active" else None}
            for g, role, status in rows]


# ----------------------------------------------------------- hierarchy ----

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
    ms = await active_membership(db, fam.group_id, user, roles=("leader", "steward", "member"))
    # stewards/members may only add to their own family; leaders anywhere
    if ms.role != "leader" and ms.family_id != fid:
        raise HTTPException(403, "You can only add members to your own family")
    member = m.Member(family_id=fid, display_name=body.display_name,
                      relationship_label=body.relationship_label)
    db.add(member)
    await db.commit()
    return {"id": member.id, "display_name": member.display_name}


# ------------------------------------------------------- soft deletion ----

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
async def group_audit(gid: str, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                      user: m.User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await active_membership(db, gid, user, roles=("leader",))
    rows = (await db.execute(
        select(m.AuditLog, m.User.display_name)
        .outerjoin(m.User, m.User.id == m.AuditLog.actor_id)
        .where(m.AuditLog.group_id == gid)
        .order_by(m.AuditLog.created_at.desc()).limit(limit).offset(offset))).all()
    return [{"id": a.id, "action": a.action, "actor_id": a.actor_id, "actor": actor_name,
             "target_id": a.target_id, "detail": a.detail,
             "created_at": a.created_at.isoformat()} for a, actor_name in rows]


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
    req_ids = [r.id for r in reqs]

    counts: dict[str, int] = {}
    prayed_ids: set[str] = set()
    if req_ids:
        counts = dict((await db.execute(
            select(m.PrayerAction.request_id, func.count())
            .where(m.PrayerAction.request_id.in_(req_ids))
            .group_by(m.PrayerAction.request_id))).all())
        prayed_ids = set((await db.execute(
            select(m.PrayerAction.request_id).where(
                m.PrayerAction.user_id == user.id, m.PrayerAction.prayed_on == today(),
                m.PrayerAction.request_id.in_(req_ids)))).scalars().all())

    packs: dict[str, list[m.Verse]] = {}
    slugs = list({r.category_slug for r in reqs})
    if slugs:
        verses = (await db.execute(select(m.Verse).where(m.Verse.category_slug.in_(slugs))
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
    by_family_members: dict[str, list[m.Member]] = {}
    if families:
        members = (await db.execute(select(m.Member).where(
            m.Member.family_id.in_([f.id for f in families]),
            m.Member.deleted_at.is_(None)))).scalars().all()
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


# ------------------------------------------------------- role management ----

async def _group_members(db: AsyncSession, gid: str) -> list[dict]:
    q = (select(m.GroupMembership, m.User)
         .join(m.User, m.User.id == m.GroupMembership.user_id)
         .where(m.GroupMembership.group_id == gid)
         .order_by(m.User.display_name))
    rows = (await db.execute(q)).all()
    return [{"user_id": u.id, "membership_id": ms.id, "display_name": u.display_name,
             "email": u.email, "role": ms.role, "status": ms.status} for ms, u in rows]


async def _active_membership_of(db: AsyncSession, gid: str, user_id: str) -> m.GroupMembership:
    ms = (await db.execute(select(m.GroupMembership).where(
        m.GroupMembership.group_id == gid, m.GroupMembership.user_id == user_id,
        m.GroupMembership.status == "active"))).scalar_one_or_none()
    if ms is None:
        raise HTTPException(404, "Member not found")
    return ms


async def _ensure_not_last_leader(db: AsyncSession, gid: str) -> None:
    leaders = (await db.execute(select(func.count()).select_from(m.GroupMembership).where(
        m.GroupMembership.group_id == gid, m.GroupMembership.role == "leader",
        m.GroupMembership.status == "active"))).scalar_one()
    if leaders <= 1:
        raise HTTPException(409, "Promote another admin first")


async def _set_role(db: AsyncSession, gid: str, user_id: str, new_role: str,
                    actor: m.User) -> m.GroupMembership:
    ms = await _active_membership_of(db, gid, user_id)
    old = ms.role
    if old == "leader" and new_role != "leader":
        await _ensure_not_last_leader(db, gid)
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


async def _remove_member(db: AsyncSession, gid: str, user_id: str, actor: m.User) -> None:
    """Remove an active member. Their linked prayer-subject rows and attached
    requests are retired; content they authored about others stays."""
    ms = await _active_membership_of(db, gid, user_id)
    if ms.role == "leader":
        await _ensure_not_last_leader(db, gid)
    linked = (await db.execute(
        select(m.Member).join(m.Family, m.Family.id == m.Member.family_id)
        .where(m.Member.user_id == user_id, m.Family.group_id == gid,
               m.Member.deleted_at.is_(None)))).scalars().all()
    for mem in linked:
        mem.deleted_at = m.utcnow()
        await _soft_delete_subject_requests(db, "member", mem.id)
    await db.delete(ms)
    audit(db, "member_removed", group_id=gid, actor_id=actor.id, target_id=user_id)
    await db.commit()


@router.delete("/api/groups/{gid}/members/{user_id}")
async def remove_member_from_group(gid: str, user_id: str,
                                   user: m.User = Depends(current_user),
                                   db: AsyncSession = Depends(get_db)):
    await active_membership(db, gid, user, roles=("leader",))
    await _remove_member(db, gid, user_id, user)
    return {"ok": True}
