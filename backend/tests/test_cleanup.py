"""Tests for the cleanup pass: session revocation, rate-limit spoof resistance,
author visibility, join deferral, viewer/steward role enforcement, answer-note
preservation, and member removal."""
import pytest
from conftest import join_member, make_group, register

from app import security


@pytest.mark.asyncio
async def test_logout_revokes_all_sessions(client):
    cookies = await register(client, "who@example.com")
    assert (await client.get("/api/me", cookies=cookies)).status_code == 200
    assert (await client.post("/api/auth/logout", cookies=cookies)).status_code == 200
    # the old cookie value is dead server-side, not merely cleared client-side
    assert (await client.get("/api/me", cookies=cookies)).status_code == 401


@pytest.mark.asyncio
async def test_rate_limit_not_bypassed_by_spoofed_forwarded_for(client, monkeypatch):
    # TRUST_PROXY defaults off: X-Forwarded-For is attacker-controlled and ignored
    monkeypatch.setattr(security, "RATE_MAX_ATTEMPTS", 3)
    bad = {"email": "nobody@example.com", "password": "wrong-password"}
    for i in range(3):
        r = await client.post("/api/auth/login", json=bad,
                              headers={"X-Forwarded-For": f"10.0.0.{i}"})
        assert r.status_code == 401
    r = await client.post("/api/auth/login", json=bad,
                          headers={"X-Forwarded-For": "10.0.0.99"})
    assert r.status_code == 429


@pytest.mark.asyncio
async def test_author_sees_own_leaders_only_request(client):
    leader = await register(client, "lead@example.com")
    g = await make_group(client, leader)
    member = await join_member(client, g["invite_code"], "m@example.com")
    fam = (await client.post(f"/api/groups/{g['id']}/families",
                             json={"family_name": "The Smiths"}, cookies=leader)).json()

    r = await client.post(f"/api/groups/{g['id']}/requests", cookies=member, json={
        "subject_type": "family", "subject_id": fam["id"],
        "title": "For leaders, from me", "category_slug": "general", "privacy": "leaders_only"})
    rid = r.json()["id"]

    # the author still sees their own request on the wall and can read/post updates
    wall = (await client.get(f"/api/groups/{g['id']}/wall", cookies=member)).json()
    titles = [q["title"] for f in wall["families"] for q in f["requests"]]
    assert "For leaders, from me" in titles
    assert (await client.get(f"/api/requests/{rid}/updates", cookies=member)).status_code == 200
    assert (await client.post(f"/api/requests/{rid}/updates", cookies=member,
                              json={"body": "still visible to me"})).status_code == 201

    # another member still cannot
    other = await join_member(client, g["invite_code"], "other@example.com", family_name="Others")
    other_wall = (await client.get(f"/api/groups/{g['id']}/wall", cookies=other)).json()
    other_titles = [q["title"] for f in other_wall["families"] for q in f["requests"]]
    assert "For leaders, from me" not in other_titles


@pytest.mark.asyncio
async def test_denied_join_leaves_nothing_behind(client):
    leader = await register(client, "lead@example.com")
    g = await make_group(client, leader, approval_required=True)

    j = await client.post(f"/api/join/{g['invite_code']}", json={
        "display_name": "Stranger", "email": "stranger@example.com",
        "password": "sufficiently-long", "new_family_name": "The Strangers"})
    assert j.json()["status"] == "pending"

    # nothing materialized while pending
    assert (await client.get(f"/api/join/{g['invite_code']}")).json()["families"] == []
    wall = (await client.get(f"/api/groups/{g['id']}/wall", cookies=leader)).json()
    assert wall["families"] == []

    queue = (await client.get(f"/api/groups/{g['id']}/join-queue", cookies=leader)).json()
    deny = await client.patch(f"/api/join-queue/{queue[0]['membership_id']}",
                              json={"approve": False}, cookies=leader)
    assert deny.json()["status"] == "denied"

    # still nothing on the wall or in the family picker, and they can try again
    assert (await client.get(f"/api/join/{g['invite_code']}")).json()["families"] == []
    retry = await client.post(f"/api/join/{g['invite_code']}", json={
        "display_name": "Stranger", "email": "stranger2@example.com",
        "password": "sufficiently-long", "new_family_name": "The Strangers"})
    assert retry.status_code == 201


@pytest.mark.asyncio
async def test_viewer_is_read_only(client):
    leader = await register(client, "lead@example.com")
    g = await make_group(client, leader)
    viewer = await join_member(client, g["invite_code"], "v@example.com", family_name="Viewers")
    fam = (await client.post(f"/api/groups/{g['id']}/families",
                             json={"family_name": "The Smiths"}, cookies=leader)).json()
    r = await client.post(f"/api/groups/{g['id']}/requests", cookies=leader, json={
        "subject_type": "family", "subject_id": fam["id"],
        "title": "Open request", "category_slug": "general"})
    rid = r.json()["id"]

    viewer_id = (await client.get("/api/me", cookies=viewer)).json()["id"]
    ok = await client.patch(f"/api/groups/{g['id']}/members/{viewer_id}",
                            json={"role": "viewer"}, cookies=leader)
    assert ok.status_code == 200

    # can read
    assert (await client.get(f"/api/groups/{g['id']}/wall", cookies=viewer)).status_code == 200
    assert (await client.get(f"/api/requests/{rid}/updates", cookies=viewer)).status_code == 200
    # cannot write
    assert (await client.post(f"/api/requests/{rid}/prayed", cookies=viewer)).status_code == 403
    assert (await client.post(f"/api/requests/{rid}/verse/shuffle", cookies=viewer)).status_code == 403
    assert (await client.post(f"/api/groups/{g['id']}/requests", cookies=viewer, json={
        "subject_type": "family", "subject_id": fam["id"],
        "title": "Nope", "category_slug": "general"})).status_code == 403
    assert (await client.post(f"/api/groups/{g['id']}/families",
                              json={"family_name": "Nope"}, cookies=viewer)).status_code == 403
    assert (await client.post(f"/api/requests/{rid}/updates", cookies=viewer,
                              json={"body": "nope"})).status_code == 403


@pytest.mark.asyncio
async def test_steward_scoped_to_own_family(client):
    leader = await register(client, "lead@example.com")
    g = await make_group(client, leader)
    steward = await join_member(client, g["invite_code"], "s@example.com", family_name="Stew Fam")
    steward_id = (await client.get("/api/me", cookies=steward)).json()["id"]
    await client.patch(f"/api/groups/{g['id']}/members/{steward_id}",
                       json={"role": "steward"}, cookies=leader)

    info = (await client.get(f"/api/join/{g['invite_code']}")).json()
    stew_fam = next(f["id"] for f in info["families"] if f["family_name"] == "Stew Fam")
    other_fam = (await client.post(f"/api/groups/{g['id']}/families",
                                   json={"family_name": "Other Fam"}, cookies=leader)).json()["id"]

    own = (await client.post(f"/api/groups/{g['id']}/requests", cookies=leader, json={
        "subject_type": "family", "subject_id": stew_fam,
        "title": "About stew fam", "category_slug": "general"})).json()["id"]
    foreign = (await client.post(f"/api/groups/{g['id']}/requests", cookies=leader, json={
        "subject_type": "family", "subject_id": other_fam,
        "title": "About other fam", "category_slug": "general"})).json()["id"]

    # steward manages requests about their own family, even authored by others…
    assert (await client.patch(f"/api/requests/{own}", cookies=steward,
                               json={"title": "Renamed by steward"})).status_code == 200
    assert (await client.post(f"/api/requests/{own}/updates", cookies=steward,
                              json={"body": "an update"})).status_code == 201
    # …but not requests about other families
    assert (await client.patch(f"/api/requests/{foreign}", cookies=steward,
                               json={"title": "Nope"})).status_code == 403
    assert (await client.delete(f"/api/requests/{foreign}", cookies=steward)).status_code == 403


@pytest.mark.asyncio
async def test_answer_note_preserved_and_title_validated(client):
    leader = await register(client, "lead@example.com")
    g = await make_group(client, leader)
    fam = (await client.post(f"/api/groups/{g['id']}/families",
                             json={"family_name": "The Smiths"}, cookies=leader)).json()
    rid = (await client.post(f"/api/groups/{g['id']}/requests", cookies=leader, json={
        "subject_type": "family", "subject_id": fam["id"],
        "title": "Job search", "category_slug": "provision"})).json()["id"]

    # empty title is rejected
    assert (await client.patch(f"/api/requests/{rid}", cookies=leader,
                               json={"title": ""})).status_code == 422

    await client.patch(f"/api/requests/{rid}", cookies=leader,
                       json={"status": "answered", "answer_note": "Got the job!"})
    await client.patch(f"/api/requests/{rid}", cookies=leader, json={"status": "active"})
    # re-answering without a note keeps the original note
    await client.patch(f"/api/requests/{rid}", cookies=leader, json={"status": "answered"})
    praise = (await client.get(f"/api/groups/{g['id']}/praise-wall", cookies=leader)).json()
    assert praise[0]["answer_note"] == "Got the job!"


@pytest.mark.asyncio
async def test_member_removal(client):
    leader = await register(client, "lead@example.com")
    g = await make_group(client, leader)
    member = await join_member(client, g["invite_code"], "m@example.com", family_name="Leavers")
    member_id = (await client.get("/api/me", cookies=member)).json()["id"]
    leader_id = (await client.get("/api/me", cookies=leader)).json()["id"]

    # sole leader cannot be removed
    assert (await client.delete(f"/api/groups/{g['id']}/members/{leader_id}",
                                cookies=leader)).status_code == 409

    ok = await client.delete(f"/api/groups/{g['id']}/members/{member_id}", cookies=leader)
    assert ok.status_code == 200
    # locked out, and their prayer-subject row left the wall
    assert (await client.get(f"/api/groups/{g['id']}/wall", cookies=member)).status_code == 403
    wall = (await client.get(f"/api/groups/{g['id']}/wall", cookies=leader)).json()
    assert all(mem["display_name"] != "Member"
               for f in wall["families"] for mem in f["members"])
