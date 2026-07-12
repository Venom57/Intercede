"""Feature tests: request updates thread, soft deletion with cascade."""
import pytest
from conftest import join_member, make_group, register


async def _group_with_request(client, leader, privacy="group"):
    g = await make_group(client, leader)
    fam = (await client.post(f"/api/groups/{g['id']}/families",
                             json={"family_name": "The Halls"}, cookies=leader)).json()
    r = await client.post(f"/api/groups/{g['id']}/requests", cookies=leader, json={
        "subject_type": "family", "subject_id": fam["id"],
        "title": "Surgery Friday", "category_slug": "healing", "privacy": privacy})
    assert r.status_code == 201
    return g, fam, r.json()["id"]


@pytest.mark.asyncio
async def test_request_updates_thread(client):
    leader = await register(client, "lead@example.com", "Dana")
    g, _fam, rid = await _group_with_request(client, leader)
    member = await join_member(client, g["invite_code"], "m@example.com")

    r = await client.post(f"/api/requests/{rid}/updates",
                          json={"body": "Surgery moved to Monday"}, cookies=leader)
    assert r.status_code == 201

    # any member who can see the request can read the thread
    upds = (await client.get(f"/api/requests/{rid}/updates", cookies=member)).json()
    assert [u["body"] for u in upds] == ["Surgery moved to Monday"]
    assert upds[0]["author"] == "Dana"

    # but only the author or a leader can post to it
    assert (await client.post(f"/api/requests/{rid}/updates",
                              json={"body": "Hijack"}, cookies=member)).status_code == 403


@pytest.mark.asyncio
async def test_updates_respect_privacy(client):
    leader = await register(client, "lead@example.com")
    g, _fam, rid = await _group_with_request(client, leader, privacy="leaders_only")
    member = await join_member(client, g["invite_code"], "m@example.com")
    # invisible request -> its update thread 404s (existence not leaked)
    assert (await client.get(f"/api/requests/{rid}/updates", cookies=member)).status_code == 404
    assert (await client.post(f"/api/requests/{rid}/updates",
                              json={"body": "x"}, cookies=member)).status_code == 404


@pytest.mark.asyncio
async def test_soft_delete_request(client):
    leader = await register(client, "lead@example.com")
    g, _fam, rid = await _group_with_request(client, leader)
    member = await join_member(client, g["invite_code"], "m@example.com")

    # a non-author member cannot delete it
    assert (await client.delete(f"/api/requests/{rid}", cookies=member)).status_code == 403
    assert (await client.delete(f"/api/requests/{rid}", cookies=leader)).status_code == 200

    wall = (await client.get(f"/api/groups/{g['id']}/wall", cookies=leader)).json()
    assert all(not f["requests"] for f in wall["families"])
    assert (await client.get(f"/api/requests/{rid}/verse", cookies=leader)).status_code == 404
    # double delete is a 404, not an error
    assert (await client.delete(f"/api/requests/{rid}", cookies=leader)).status_code == 404


@pytest.mark.asyncio
async def test_delete_family_cascades(client):
    leader = await register(client, "lead@example.com")
    g, fam, _rid = await _group_with_request(client, leader)
    emma = (await client.post(f"/api/families/{fam['id']}/members",
                              json={"display_name": "Emma"}, cookies=leader)).json()
    r2 = await client.post(f"/api/groups/{g['id']}/requests", cookies=leader, json={
        "subject_type": "member", "subject_id": emma["id"],
        "title": "Urgent exam", "category_slug": "anxiety", "is_urgent": True})
    assert r2.status_code == 201

    assert (await client.delete(f"/api/families/{fam['id']}", cookies=leader)).status_code == 200

    wall = (await client.get(f"/api/groups/{g['id']}/wall", cookies=leader)).json()
    assert wall["families"] == []
    assert wall["urgent"] == []  # cascaded: member requests don't linger in the urgent rail
    # subjects of a deleted family reject new requests
    r3 = await client.post(f"/api/groups/{g['id']}/requests", cookies=leader, json={
        "subject_type": "member", "subject_id": emma["id"],
        "title": "Too late", "category_slug": "general"})
    assert r3.status_code == 422


@pytest.mark.asyncio
async def test_delete_member_is_leader_only(client):
    leader = await register(client, "lead@example.com")
    g, fam, _rid = await _group_with_request(client, leader)
    member = await join_member(client, g["invite_code"], "m@example.com")
    emma = (await client.post(f"/api/families/{fam['id']}/members",
                              json={"display_name": "Emma"}, cookies=leader)).json()
    assert (await client.delete(f"/api/members/{emma['id']}", cookies=member)).status_code == 403
    assert (await client.delete(f"/api/members/{emma['id']}", cookies=leader)).status_code == 200
    wall = (await client.get(f"/api/groups/{g['id']}/wall", cookies=leader)).json()
    fam_section = next(f for f in wall["families"] if f["id"] == fam["id"])
    assert fam_section["members"] == []
