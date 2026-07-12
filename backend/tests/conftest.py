"""Shared fixtures. Env must be set before the app modules import."""
import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
# generous suite-wide ceiling; the rate-limit test tightens it via monkeypatch
os.environ.setdefault("RATE_MAX_ATTEMPTS", "500")

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.deps import SessionLocal, engine
from app.main import app
from app.models import Base
from app.security import reset_rate_limits
from app.verses import seed_verses


@pytest_asyncio.fixture()
async def client():
    reset_rate_limits()
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


async def make_group(client, leader_cookies, name="Study", approval_required=False):
    r = await client.post("/api/groups", json={"name": name, "approval_required": approval_required},
                          cookies=leader_cookies)
    assert r.status_code == 201, r.text
    return r.json()


async def join_member(client, invite_code, email, family_name="A Family", name="Member"):
    """Join an open (no-approval) group by creating a new family; returns cookies."""
    r = await client.post(f"/api/join/{invite_code}", json={
        "display_name": name, "email": email,
        "password": "sufficiently-long", "new_family_name": family_name})
    assert r.status_code == 201, r.text
    return cookies_of(r)
