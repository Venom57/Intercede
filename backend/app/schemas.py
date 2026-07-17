"""Request/response bodies (Pydantic) shared by the routers."""
from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    approval_required: bool = True


class FamilyIn(BaseModel):
    family_name: str = Field(min_length=1, max_length=120)


class MemberIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    relationship_label: str | None = Field(default=None, max_length=60)


class RequestIn(BaseModel):
    subject_type: str = Field(pattern="^(family|member)$")
    subject_id: str
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(default="", max_length=4000)
    category_slug: str = "general"
    privacy: str = Field(default="group", pattern="^(group|leaders_only|family_only)$")
    is_urgent: bool = False


class RequestPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    body: str | None = Field(default=None, max_length=4000)
    status: str | None = Field(default=None, pattern="^(active|answered|ongoing|archived)$")
    answer_note: str | None = Field(default=None, max_length=4000)
    is_urgent: bool | None = None


class UpdateIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class JoinIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    family_id: str | None = None          # join an existing family…
    new_family_name: str | None = Field(default=None, max_length=120)  # …or create one


class ApproveIn(BaseModel):
    approve: bool


class RolePatch(BaseModel):
    role: str = Field(pattern="^(leader|steward|member|viewer)$")


class SiteAdminPatch(BaseModel):
    is_site_admin: bool
