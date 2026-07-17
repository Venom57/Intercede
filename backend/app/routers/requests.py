"""Prayer requests: creation, edits, prayed actions, verses, and update threads."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models as m
from ..deps import active_membership, current_user, get_db, today, visible_request_filter
from ..schemas import RequestIn, RequestPatch, UpdateIn
from ..security import audit

router = APIRouter()


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
    await active_membership(db, gid, user, roles=("leader", "steward", "member"))
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


async def _subject_family_id(db: AsyncSession, req: m.PrayerRequest) -> str | None:
    if req.subject_type == "family":
        return req.subject_id
    member = await db.get(m.Member, req.subject_id)
    return member.family_id if member else None


async def _can_manage_request(db: AsyncSession, req: m.PrayerRequest,
                              ms: m.GroupMembership) -> bool:
    """Author, leader, or the steward of the subject's family. Viewers never."""
    if ms.role == "viewer":
        return False
    if ms.role == "leader" or req.created_by == ms.user_id:
        return True
    if ms.role == "steward" and ms.family_id:
        return (await _subject_family_id(db, req)) == ms.family_id
    return False


@router.patch("/api/requests/{rid}")
async def patch_request(rid: str, body: RequestPatch, user: m.User = Depends(current_user),
                        db: AsyncSession = Depends(get_db)):
    req = await db.get(m.PrayerRequest, rid)
    if not req or req.deleted_at:
        raise HTTPException(404, "Request not found")
    ms = await active_membership(db, req.group_id, user)
    if not await _can_manage_request(db, req, ms):
        raise HTTPException(403, "Only the author, a leader, or the family's steward can edit this request")
    data = body.model_dump(exclude_unset=True)
    # None means "clear" only for answer_note; a null title/body/status is ignored
    data = {k: v for k, v in data.items() if v is not None or k == "answer_note"}
    if "answer_note" in data:
        req.answer_note = data.pop("answer_note")
    if data.get("status") == "answered" and req.status != "answered":
        req.answered_at = m.utcnow()
    if "status" in data and data["status"] != req.status:
        audit(db, "request.status_changed", group_id=req.group_id, actor_id=user.id,
              target_id=req.id, detail=f"{req.status} -> {data['status']}")
    for k, v in data.items():
        setattr(req, k, v)
    await db.commit()
    return {"id": req.id, "status": req.status, "answered_at": req.answered_at}


async def _visible_request(
    db: AsyncSession, rid: str, user: m.User
) -> tuple[m.PrayerRequest, m.GroupMembership]:
    """Load a request the user is allowed to see, else 404 (existence is not leaked)."""
    req = await db.get(m.PrayerRequest, rid)
    if not req or req.deleted_at:
        raise HTTPException(404, "Request not found")
    ms = await active_membership(db, req.group_id, user)
    vis = (await db.execute(select(m.PrayerRequest.id).where(
        m.PrayerRequest.id == rid, visible_request_filter(ms)))).first()
    if not vis:
        raise HTTPException(404, "Request not found")
    return req, ms


@router.post("/api/requests/{rid}/prayed")
async def prayed(rid: str, user: m.User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    _req, ms = await _visible_request(db, rid, user)
    if ms.role == "viewer":
        raise HTTPException(403, "Viewers cannot record prayers")
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


async def _verse_pack(db: AsyncSession, category_slug: str) -> list[m.Verse]:
    pack = (await db.execute(select(m.Verse).where(m.Verse.category_slug == category_slug)
                             .order_by(m.Verse.position))).scalars().all()
    if not pack:
        raise HTTPException(404, "No verses for this category")
    return pack


@router.get("/api/requests/{rid}/verse")
async def request_verse(rid: str, user: m.User = Depends(current_user),
                        db: AsyncSession = Depends(get_db)):
    req, _ms = await _visible_request(db, rid, user)
    pack = await _verse_pack(db, req.category_slug)
    v = pack[req.verse_index % len(pack)]
    return {"reference": v.reference, "text": v.text, "translation": v.translation}


@router.post("/api/requests/{rid}/verse/shuffle")
async def shuffle_verse(rid: str, user: m.User = Depends(current_user),
                        db: AsyncSession = Depends(get_db)):
    """Rotate the (request-shared) verse cursor. A POST because it mutates state."""
    req, ms = await _visible_request(db, rid, user)
    if ms.role == "viewer":
        raise HTTPException(403, "Viewers cannot rotate the verse")
    pack = await _verse_pack(db, req.category_slug)
    req.verse_index = (req.verse_index + 1) % len(pack)
    await db.commit()
    v = pack[req.verse_index]
    return {"reference": v.reference, "text": v.text, "translation": v.translation}


# ------------------------------------------------------------- updates ----

@router.post("/api/requests/{rid}/updates", status_code=201)
async def add_update(rid: str, body: UpdateIn, user: m.User = Depends(current_user),
                     db: AsyncSession = Depends(get_db)):
    req, ms = await _visible_request(db, rid, user)
    if not await _can_manage_request(db, req, ms):
        raise HTTPException(403, "Only the author, a leader, or the family's steward can post updates")
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


@router.delete("/api/requests/{rid}")
async def delete_request(rid: str, user: m.User = Depends(current_user),
                         db: AsyncSession = Depends(get_db)):
    req = await db.get(m.PrayerRequest, rid)
    if not req or req.deleted_at:
        raise HTTPException(404, "Request not found")
    ms = await active_membership(db, req.group_id, user)
    if not await _can_manage_request(db, req, ms):
        raise HTTPException(403, "Only the author, a leader, or the family's steward can delete this request")
    req.deleted_at = m.utcnow()
    audit(db, "request.deleted", group_id=req.group_id, actor_id=user.id, target_id=rid)
    await db.commit()
    return {"ok": True}
