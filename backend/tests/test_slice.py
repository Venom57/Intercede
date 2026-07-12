"""Vertical-slice tests: golden paths + the security properties that matter most.

- privacy enforcement is server-side (family_only invisible to outsiders)
- multi-tenant isolation (no cross-group reads)
- prayed action is idempotent per user per day
- QR join approval gate blocks pending members from all group data
"""
import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.deps import SessionLocal, engine
from app.models import Base
from app.verses import seed_verses


@pytest_asyncio.fixture()
async def client():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as db:
        await seed_verses(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def cookies_of(resp):
    return {"intercede_session": resp.cookies["intercede_session"]}


async def register(client, email, name="Someone"):
    r = await client.post("/api/auth/register",
                          json={"email": email, "password": "sufficiently-long", "display_name": name})
    assert r.status_code == 201, r.text
    return cookies_of(r)


@pytest.mark.asyncio
async def test_golden_path_hierarchy_request_verse_prayed_answered(client):
    leader = await register(client, "lead@example.com", "Dana")
    g = (await client.post("/api/groups", json={"name": "Tuesday Night Study"}, cookies=leader)).json()

    fam = (await client.post(f"/api/groups/{g['id']}/families",
                             json={"family_name": "The Andersons"}, cookies=leader)).json()
    emma = (await client.post(f"/api/families/{fam['id']}/members",
                              json={"display_name": "Emma", "relationship_label": "daughter"},
                              cookies=leader)).json()

    # member-level request
    r = await client.post(f"/api/groups/{g['id']}/requests", cookies=leader, json={
        "subject_type": "member", "subject_id": emma["id"],
        "title": "Emma's entrance exams", "category_slug": "anxiety"})
    assert r.status_code == 201
    rid = r.json()["id"]

    # family-level request
    r2 = await client.post(f"/api/groups/{g['id']}/requests", cookies=leader, json={
        "subject_type": "family", "subject_id": fam["id"],
        "title": "Move to Ohio", "category_slug": "guidance", "is_urgent": True})
    assert r2.status_code == 201

    # verse mapping + rotation
    v1 = (await client.get(f"/api/requests/{rid}/verse", cookies=leader)).json()
    assert v1["translation"] == "KJV" and "Philippians" in v1["reference"]
    v2 = (await client.get(f"/api/requests/{rid}/verse?shuffle=true", cookies=leader)).json()
    assert v2["reference"] != v1["reference"]

    # wall rollup: family section contains both family- and member-level requests
    wall = (await client.get(f"/api/groups/{g['id']}/wall", cookies=leader)).json()
    fam_section = wall["families"][0]
    assert fam_section["family_name"] == "The Andersons"
    assert fam_section["requests"][0]["title"] == "Move to Ohio"
    assert fam_section["members"][0]["requests"][0]["title"] == "Emma's entrance exams"
    assert wall["urgent"][0]["title"] == "Move to Ohio"

    # prayed idempotency (same user, same day)
    p1 = (await client.post(f"/api/requests/{rid}/prayed", cookies=leader)).json()
    p2 = (await client.post(f"/api/requests/{rid}/prayed", cookies=leader)).json()
    assert p1["newly_recorded"] is True and p2["newly_recorded"] is False
    assert p2["total_prayers"] == 1

    # answered → praise wall
    pr = await client.patch(f"/api/requests/{rid}", cookies=leader,
                            json={"status": "answered", "answer_note": "She passed!"})
    assert pr.status_code == 200
    praise = (await client.get(f"/api/groups/{g['id']}/praise-wall", cookies=leader)).json()
    assert praise[0]["answer_note"] == "She passed!"
    wall2 = (await client.get(f"/api/groups/{g['id']}/wall", cookies=leader)).json()
    assert wall2["families"][0]["members"][0]["requests"] == []  # left the active wall


@pytest.mark.asyncio
async def test_family_only_privacy_enforced_server_side(client):
    leader = await register(client, "lead@example.com")
    g = (await client.post("/api/groups", json={"name": "Study", "approval_required": False},
                           cookies=leader)).json()
    fam_a = (await client.post(f"/api/groups/{g['id']}/families",
                               json={"family_name": "Family A"}, cookies=leader)).json()
    (await client.post(f"/api/groups/{g['id']}/families",
                       json={"family_name": "Family B"}, cookies=leader)).json()

    # outsider joins family B via invite
    info = (await client.get(f"/api/join/{g['invite_code']}")).json()
    fam_b_id = next(f["id"] for f in info["families"] if f["family_name"] == "Family B")
    jb = await client.post(f"/api/join/{g['invite_code']}", json={
        "display_name": "Bea", "email": "bea@example.com",
        "password": "sufficiently-long", "family_id": fam_b_id})
    bea = cookies_of(jb)

    # family_only request on Family A
    r = await client.post(f"/api/groups/{g['id']}/requests", cookies=leader, json={
        "subject_type": "family", "subject_id": fam_a["id"],
        "title": "Private matter", "category_slug": "family", "privacy": "family_only"})
    rid = r.json()["id"]

    # Bea (family B) cannot see it anywhere
    wall = (await client.get(f"/api/groups/{g['id']}/wall", cookies=bea)).json()
    titles = [q["title"] for f in wall["families"] for q in f["requests"]]
    assert "Private matter" not in titles
    assert (await client.get(f"/api/requests/{rid}/verse", cookies=bea)).status_code == 404
    assert (await client.post(f"/api/requests/{rid}/prayed", cookies=bea)).status_code == 404
    # leader still sees it
    lw = (await client.get(f"/api/groups/{g['id']}/wall", cookies=leader)).json()
    assert "Private matter" in [q["title"] for f in lw["families"] for q in f["requests"]]


@pytest.mark.asyncio
async def test_multi_tenant_isolation(client):
    a = await register(client, "a@example.com")
    b = await register(client, "b@example.com")
    ga = (await client.post("/api/groups", json={"name": "Group A"}, cookies=a)).json()
    assert (await client.get(f"/api/groups/{ga['id']}/wall", cookies=b)).status_code == 403
    assert (await client.post(f"/api/groups/{ga['id']}/families",
                              json={"family_name": "Intruders"}, cookies=b)).status_code == 403
    # cannot attach a request to a subject from another group
    gb = (await client.post("/api/groups", json={"name": "Group B"}, cookies=b)).json()
    fam_a = (await client.post(f"/api/groups/{ga['id']}/families",
                               json={"family_name": "A Fam"}, cookies=a)).json()
    r = await client.post(f"/api/groups/{gb['id']}/requests", cookies=b, json={
        "subject_type": "family", "subject_id": fam_a["id"],
        "title": "Cross-group", "category_slug": "general"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_qr_join_approval_gate(client):
    leader = await register(client, "lead@example.com")
    g = (await client.post("/api/groups", json={"name": "Study"}, cookies=leader)).json()  # approval ON

    j = await client.post(f"/api/join/{g['invite_code']}", json={
        "display_name": "Newcomer", "email": "new@example.com",
        "password": "sufficiently-long", "new_family_name": "The Newcomers"})
    assert j.status_code == 201 and j.json()["status"] == "pending"
    newbie = cookies_of(j)

    # pending member is locked out of everything
    assert (await client.get(f"/api/groups/{g['id']}/wall", cookies=newbie)).status_code == 403
    assert (await client.get("/api/groups", cookies=newbie)).json() == []

    # leader approves from the queue
    queue = (await client.get(f"/api/groups/{g['id']}/join-queue", cookies=leader)).json()
    assert queue[0]["email"] == "new@example.com"
    ok = await client.patch(f"/api/join-queue/{queue[0]['membership_id']}",
                            json={"approve": True}, cookies=leader)
    assert ok.json()["status"] == "active"
    wall = (await client.get(f"/api/groups/{g['id']}/wall", cookies=newbie)).json()
    assert wall["families"][0]["family_name"] == "The Newcomers"

    # code rotation kills the old poster
    (await client.post(f"/api/groups/{g['id']}/invite/rotate", cookies=leader)).json()
    assert (await client.get(f"/api/join/{g['invite_code']}")).status_code == 404
