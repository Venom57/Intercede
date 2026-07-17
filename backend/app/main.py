"""Intercede — prayer request tracking for Bible study groups (vertical slice).

Dev bootstrap creates tables directly; production should use Alembic migrations
(see README) and PostgreSQL via DATABASE_URL.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .deps import SessionLocal, engine
from .models import Base
from .routers import admin, auth, groups, join, requests
from .security import security_headers_middleware
from .verses import seed_verses


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as db:
        await seed_verses(db)
    yield


app = FastAPI(title="Intercede", lifespan=lifespan)
app.middleware("http")(security_headers_middleware)
for feature_router in (auth.router, groups.router, requests.router, join.router, admin.router):
    app.include_router(feature_router)


@app.get("/api/health")
async def health():
    return {"ok": True}
