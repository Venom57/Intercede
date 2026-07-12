"""Domain models — Group → Family → Member hierarchy with polymorphic prayer requests.

Members are prayer *subjects* decoupled from Users (auth principals): a child or
non-attending relative can be prayed for without ever having a login.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def uid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Group(Base):
    __tablename__ = "groups"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120))
    invite_code: Mapped[str] = mapped_column(String(32), unique=True, index=True, default=uid)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=True)  # poster hangs in semi-public space
    created_by: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    families: Mapped[list[Family]] = relationship(back_populates="group")


ROLES = ("leader", "steward", "member", "viewer")
MEMBERSHIP_STATUSES = ("active", "pending")


class GroupMembership(Base):
    __tablename__ = "group_memberships"
    __table_args__ = (UniqueConstraint("user_id", "group_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    group_id: Mapped[str] = mapped_column(String(32), ForeignKey("groups.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="member")
    status: Mapped[str] = mapped_column(String(16), default="active")
    family_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("families.id"), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Family(Base):
    __tablename__ = "families"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    group_id: Mapped[str] = mapped_column(String(32), ForeignKey("groups.id"), index=True)
    family_name: Mapped[str] = mapped_column(String(120))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    group: Mapped[Group] = relationship(back_populates="families")
    members: Mapped[list[Member]] = relationship(back_populates="family")


class Member(Base):
    __tablename__ = "members"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    family_id: Mapped[str] = mapped_column(String(32), ForeignKey("families.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    relationship_label: Mapped[str | None] = mapped_column(String(60), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("users.id"), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    family: Mapped[Family] = relationship(back_populates="members")


class Verse(Base):
    """Public-domain (KJV) verse text keyed to a category slug for rotation."""
    __tablename__ = "verses"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    category_slug: Mapped[str] = mapped_column(String(60), index=True)
    reference: Mapped[str] = mapped_column(String(60))
    translation: Mapped[str] = mapped_column(String(16), default="KJV")
    text: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0)


REQUEST_STATUSES = ("active", "answered", "ongoing", "archived")
PRIVACY_LEVELS = ("group", "leaders_only", "family_only")
SUBJECT_TYPES = ("family", "member")


class PrayerRequest(Base):
    __tablename__ = "prayer_requests"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    group_id: Mapped[str] = mapped_column(String(32), ForeignKey("groups.id"), index=True)
    subject_type: Mapped[str] = mapped_column(String(16))          # 'family' | 'member'
    subject_id: Mapped[str] = mapped_column(String(32), index=True)  # Family.id or Member.id
    title: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text, default="")
    category_slug: Mapped[str] = mapped_column(String(60), default="general")
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    privacy: Mapped[str] = mapped_column(String(16), default="group")
    is_urgent: Mapped[bool] = mapped_column(Boolean, default=False)
    verse_index: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    answer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RequestUpdate(Base):
    __tablename__ = "request_updates"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    request_id: Mapped[str] = mapped_column(String(32), ForeignKey("prayer_requests.id"), index=True)
    author_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    """Immutable trail of security-relevant actions, scoped per group where possible."""
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    action: Mapped[str] = mapped_column(String(60), index=True)
    group_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PrayerAction(Base):
    """One 'I prayed' per user per request per calendar day (idempotent)."""
    __tablename__ = "prayer_actions"
    __table_args__ = (UniqueConstraint("request_id", "user_id", "prayed_on"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    request_id: Mapped[str] = mapped_column(String(32), ForeignKey("prayer_requests.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    prayed_on: Mapped[date] = mapped_column(Date)
