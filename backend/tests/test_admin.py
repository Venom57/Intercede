"""Site-admin + per-group admin-designation tests.

- first account bootstraps as site admin; everyone after is not
- /api/admin/* is deny-by-default for non-admins
- a site admin manages any group's roles without being a member
- last-leader and last-site-admin demotions are blocked (409)
"""
import pytest
from conftest import join_member, make_group, register


async def me(client, cookies):
    r = await client.get("/api/me", cookies=cookies)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_first_user_bootstraps_as_site_admin(client):
    admin = await register(client, "first@example.com", "Root")
    other = await register(client, "second@example.com", "Two")
    assert (await me(client, admin))["is_site_admin"] is True
    assert (await me(client, other))["is_site_admin"] is False


@pytest.mark.asyncio
async def test_non_admin_gets_403_on_admin_routes(client):
    await register(client, "first@example.com")  # site admin exists but isn't the caller
    user = await register(client, "pleb@example.com")
    for path in ("/api/admin/overview", "/api/admin/groups", "/api/admin/users",
                 "/api/admin/groups/x/members"):
        assert (await client.get(path, cookies=user)).status_code == 403
    r = await client.patch("/api/admin/groups/x/members/y", json={"role": "leader"}, cookies=user)
    assert r.status_code == 403
    r = await client.patch("/api/admin/users/x", json={"is_site_admin": True}, cookies=user)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_overview_counts(client):
    admin = await register(client, "root@example.com", "Root")
    g = await make_group(client, admin)
    await join_member(client, g["invite_code"], "m1@example.com")  # +1 user, +1 family
    fam = (await client.post(f"/api/groups/{g['id']}/families",
                             json={"family_name": "The Lees"}, cookies=admin)).json()
    r = await client.post(f"/api/groups/{g['id']}/requests", cookies=admin, json={
        "subject_type": "family", "subject_id": fam["id"],
        "title": "Provision", "category_slug": "provision"})
    assert r.status_code == 201
    await client.post(f"/api/requests/{r.json()['id']}/prayed", cookies=admin)

    ov = (await client.get("/api/admin/overview", cookies=admin)).json()
    assert ov == {"users": 2, "groups": 1, "families": 2, "requests": 1, "prayers": 1}


@pytest.mark.asyncio
async def test_site_admin_manages_group_without_membership(client):
    admin = await register(client, "root@example.com", "Root")
    leader = await register(client, "lead@example.com", "Lead")
    g = await make_group(client, leader)
    member = await join_member(client, g["invite_code"], "mel@example.com", name="Mel")
    gid = g["id"]

    # admin is not a member of the group, but sees its roster and roll-up
    r = await client.get(f"/api/admin/groups/{gid}/members", cookies=admin)
    assert r.status_code == 200
    rows = r.json()
    assert {row["display_name"] for row in rows} == {"Lead", "Mel"}
    assert (await client.get("/api/admin/groups/nope/members", cookies=admin)).status_code == 404

    glist = (await client.get("/api/admin/groups", cookies=admin)).json()
    assert glist[0]["member_count"] == 2 and glist[0]["leaders"] == ["Lead"]

    # designate Mel as the class admin (leader)
    mel_id = next(row["user_id"] for row in rows if row["display_name"] == "Mel")
    r = await client.patch(f"/api/admin/groups/{gid}/members/{mel_id}",
                           json={"role": "leader"}, cookies=admin)
    assert r.status_code == 200 and r.json()["role"] == "leader"

    # Mel can now hit a leader-only endpoint, and the change is audited
    assert (await client.get(f"/api/groups/{gid}/join-queue", cookies=member)).status_code == 200
    trail = (await client.get(f"/api/groups/{gid}/audit", cookies=member)).json()
    changed = [a for a in trail if a["action"] == "role_changed"]
    assert changed and changed[0]["detail"] == "member -> leader"


@pytest.mark.asyncio
async def test_demoting_last_leader_conflicts_on_both_routes(client):
    admin = await register(client, "root@example.com", "Root")
    leader = await register(client, "lead@example.com", "Lead")
    g = await make_group(client, leader)
    lead_id = (await me(client, leader))["id"]

    r = await client.patch(f"/api/admin/groups/{g['id']}/members/{lead_id}",
                           json={"role": "member"}, cookies=admin)
    assert r.status_code == 409
    r = await client.patch(f"/api/groups/{g['id']}/members/{lead_id}",
                           json={"role": "member"}, cookies=leader)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_leader_promotes_second_leader_then_steps_down(client):
    await register(client, "root@example.com", "Root")
    leader = await register(client, "lead@example.com", "Lead")
    g = await make_group(client, leader)
    member = await join_member(client, g["invite_code"], "mel@example.com", name="Mel")
    gid = g["id"]
    lead_id = (await me(client, leader))["id"]
    mel_id = (await me(client, member))["id"]

    # plain member may not change roles
    r = await client.patch(f"/api/groups/{gid}/members/{lead_id}",
                           json={"role": "member"}, cookies=member)
    assert r.status_code == 403

    r = await client.patch(f"/api/groups/{gid}/members/{mel_id}",
                           json={"role": "leader"}, cookies=leader)
    assert r.status_code == 200 and r.json()["role"] == "leader"

    # with a second leader in place, the original may step down
    r = await client.patch(f"/api/groups/{gid}/members/{lead_id}",
                           json={"role": "member"}, cookies=leader)
    assert r.status_code == 200 and r.json()["role"] == "member"


@pytest.mark.asyncio
async def test_last_site_admin_cannot_be_revoked(client):
    admin = await register(client, "root@example.com", "Root")
    other = await register(client, "second@example.com", "Two")
    admin_id = (await me(client, admin))["id"]
    other_id = (await me(client, other))["id"]

    r = await client.patch(f"/api/admin/users/{admin_id}",
                           json={"is_site_admin": False}, cookies=admin)
    assert r.status_code == 409
    assert (await client.patch("/api/admin/users/nope",
                               json={"is_site_admin": True}, cookies=admin)).status_code == 404

    r = await client.patch(f"/api/admin/users/{other_id}",
                           json={"is_site_admin": True}, cookies=admin)
    assert r.status_code == 200 and r.json()["is_site_admin"] is True
    r = await client.patch(f"/api/admin/users/{admin_id}",
                           json={"is_site_admin": False}, cookies=admin)
    assert r.status_code == 200
    assert (await me(client, admin))["is_site_admin"] is False

    users = (await client.get("/api/admin/users", cookies=other)).json()
    assert [u["is_site_admin"] for u in users] == [False, True]
