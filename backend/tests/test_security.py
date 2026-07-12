"""Security-property tests: rate limiting, enumeration resistance, headers,
role gates, approval gate on write paths, audit log access control."""
import pytest
from conftest import cookies_of, join_member, make_group, register

from app import security


@pytest.mark.asyncio
async def test_login_rate_limited(client, monkeypatch):
    monkeypatch.setattr(security, "RATE_MAX_ATTEMPTS", 3)
    bad = {"email": "nobody@example.com", "password": "wrong-password"}
    for _ in range(3):
        assert (await client.post("/api/auth/login", json=bad)).status_code == 401
    r = await client.post("/api/auth/login", json=bad)
    assert r.status_code == 429
    assert "retry-after" in {k.lower() for k in r.headers.keys()}


@pytest.mark.asyncio
async def test_join_rate_limited(client, monkeypatch):
    monkeypatch.setattr(security, "RATE_MAX_ATTEMPTS", 2)
    payload = {"display_name": "X", "email": "x@example.com",
               "password": "sufficiently-long", "new_family_name": "F"}
    for _ in range(2):
        await client.post("/api/join/not-a-real-code", json=payload)
    r = await client.post("/api/join/not-a-real-code", json=payload)
    assert r.status_code == 429


@pytest.mark.asyncio
async def test_login_no_account_enumeration(client):
    await register(client, "real@example.com")
    unknown = await client.post("/api/auth/login",
                                json={"email": "ghost@example.com", "password": "whatever-long"})
    wrongpw = await client.post("/api/auth/login",
                                json={"email": "real@example.com", "password": "wrong-password"})
    assert unknown.status_code == wrongpw.status_code == 401
    assert unknown.json() == wrongpw.json()  # identical body, nothing to distinguish


@pytest.mark.asyncio
async def test_security_headers_present(client):
    r = await client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_unauthenticated_requests_rejected(client):
    assert (await client.get("/api/me")).status_code == 401
    assert (await client.get("/api/groups")).status_code == 401
    # tampered cookie is rejected, not just missing one
    bad = {"intercede_session": "forged.token.value"}
    assert (await client.get("/api/me", cookies=bad)).status_code == 401


@pytest.mark.asyncio
async def test_member_role_cannot_use_leader_endpoints(client):
    leader = await register(client, "lead@example.com")
    g = await make_group(client, leader)
    member = await join_member(client, g["invite_code"], "m@example.com")

    gid = g["id"]
    assert (await client.get(f"/api/groups/{gid}/join-queue", cookies=member)).status_code == 403
    assert (await client.post(f"/api/groups/{gid}/invite/rotate", cookies=member)).status_code == 403
    assert (await client.get(f"/api/groups/{gid}/invite/poster.svg", cookies=member)).status_code == 403
    assert (await client.get(f"/api/groups/{gid}/audit", cookies=member)).status_code == 403
    # invite code is not echoed to non-leaders in the group list
    groups = (await client.get("/api/groups", cookies=member)).json()
    assert groups[0]["invite_code"] is None


@pytest.mark.asyncio
async def test_pending_member_blocked_from_writes(client):
    leader = await register(client, "lead@example.com")
    g = await make_group(client, leader, approval_required=True)
    j = await client.post(f"/api/join/{g['invite_code']}", json={
        "display_name": "P", "email": "pending@example.com",
        "password": "sufficiently-long", "new_family_name": "Pendings"})
    pending = cookies_of(j)

    fams = (await client.get(f"/api/join/{g['invite_code']}")).json()["families"]
    r = await client.post(f"/api/groups/{g['id']}/requests", cookies=pending, json={
        "subject_type": "family", "subject_id": fams[0]["id"],
        "title": "Should be blocked", "category_slug": "general"})
    assert r.status_code == 403
    assert (await client.post(f"/api/groups/{g['id']}/families",
                              json={"family_name": "Nope"}, cookies=pending)).status_code == 403


@pytest.mark.asyncio
async def test_leaders_only_privacy(client):
    leader = await register(client, "lead@example.com")
    g = await make_group(client, leader)
    member = await join_member(client, g["invite_code"], "m@example.com")
    fam = (await client.post(f"/api/groups/{g['id']}/families",
                             json={"family_name": "The Smiths"}, cookies=leader)).json()

    r = await client.post(f"/api/groups/{g['id']}/requests", cookies=leader, json={
        "subject_type": "family", "subject_id": fam["id"],
        "title": "Leader eyes only", "category_slug": "general", "privacy": "leaders_only"})
    rid = r.json()["id"]

    member_wall = (await client.get(f"/api/groups/{g['id']}/wall", cookies=member)).json()
    titles = [q["title"] for f in member_wall["families"] for q in f["requests"]]
    assert "Leader eyes only" not in titles
    assert (await client.get(f"/api/requests/{rid}/verse", cookies=member)).status_code == 404

    leader_wall = (await client.get(f"/api/groups/{g['id']}/wall", cookies=leader)).json()
    assert "Leader eyes only" in [q["title"] for f in leader_wall["families"] for q in f["requests"]]


@pytest.mark.asyncio
async def test_audit_log_records_sensitive_actions_leader_only(client):
    leader = await register(client, "lead@example.com")
    g = await make_group(client, leader, approval_required=True)
    await client.post(f"/api/join/{g['invite_code']}", json={
        "display_name": "N", "email": "new@example.com",
        "password": "sufficiently-long", "new_family_name": "News"})
    queue = (await client.get(f"/api/groups/{g['id']}/join-queue", cookies=leader)).json()
    await client.patch(f"/api/join-queue/{queue[0]['membership_id']}",
                       json={"approve": True}, cookies=leader)
    await client.post(f"/api/groups/{g['id']}/invite/rotate", cookies=leader)

    audit = (await client.get(f"/api/groups/{g['id']}/audit", cookies=leader)).json()
    actions = [a["action"] for a in audit]
    assert "join.requested" in actions
    assert "join.approved" in actions
    assert "invite.rotated" in actions

    # the approved member still can't read the audit trail
    login = await client.post("/api/auth/login",
                              json={"email": "new@example.com", "password": "sufficiently-long"})
    assert (await client.get(f"/api/groups/{g['id']}/audit",
                             cookies=cookies_of(login))).status_code == 403


@pytest.mark.asyncio
async def test_session_cookie_flags(client):
    r = await client.post("/api/auth/register", json={
        "email": "flags@example.com", "password": "sufficiently-long", "display_name": "F"})
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
