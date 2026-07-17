"""Site-admin endpoints: overview stats, all groups/users, role & admin grants."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models as m
from ..deps import get_db, require_site_admin
from ..schemas import RolePatch, SiteAdminPatch
from ..security import audit
from .groups import _group_members, _remove_member, _set_role

router = APIRouter()


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


@router.delete("/api/admin/groups/{gid}/members/{user_id}")
async def admin_remove_member(gid: str, user_id: str,
                              admin: m.User = Depends(require_site_admin),
                              db: AsyncSession = Depends(get_db)):
    if not await db.get(m.Group, gid):
        raise HTTPException(404, "Group not found")
    await _remove_member(db, gid, user_id, admin)
    return {"ok": True}


@router.get("/api/admin/users")
async def admin_users(limit: int = Query(200, ge=1, le=500), offset: int = Query(0, ge=0),
                      admin: m.User = Depends(require_site_admin),
                      db: AsyncSession = Depends(get_db)):
    users = (await db.execute(select(m.User).order_by(m.User.created_at)
                              .limit(limit).offset(offset))).scalars().all()
    return [{"id": u.id, "email": u.email, "display_name": u.display_name,
             "is_site_admin": u.is_site_admin, "created_at": u.created_at.isoformat()} for u in users]


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
