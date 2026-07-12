"""Intercede — prayer request tracking for Bible study groups (vertical slice).

Dev bootstrap creates tables directly; production should use Alembic migrations
(see README) and PostgreSQL via DATABASE_URL.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .deps import SessionLocal, engine
from .models import Base
from .routes import router
from .verses import seed_verses


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as db:
        await seed_verses(db)
    yield


app = FastAPI(title="Intercede", lifespan=lifespan)
app.include_router(router)


@app.get("/api/health")
async def health():
    return {"ok": True}
