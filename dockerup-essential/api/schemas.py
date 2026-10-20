"""Request / response models (shown in the OpenAPI docs at /docs)."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, StrictInt, StrictStr


# ─── Auth & accounts ─────────────────────────────────────────────────────────

class LoginBody(BaseModel):
    username: str = Field("", max_length=64)
    password: str = Field("", max_length=256)


class LoginResult(BaseModel):
    ok: bool = True
    token: str
    role: Literal["admin", "uploader"]
    username: str
    display_name: Optional[str] = None
    expires_at: datetime


class Identity(BaseModel):
    username: str
    role: Literal["admin", "uploader"]
    display_name: Optional[str] = None
    expires_at: datetime


class ChangePasswordBody(BaseModel):
    old_password: str = Field("", max_length=256)
    new_password: str = Field("", max_length=256)


class UserCreate(BaseModel):
    username: str = Field(..., max_length=64)
    display_name: str = Field("", max_length=100)
    department: Optional[str] = Field(None, max_length=100)
    email: Optional[str] = Field(None, max_length=200)
    password: str = Field("", max_length=256)


class UserUpdate(BaseModel):
    display_name: Optional[str] = Field(None, max_length=100)
    department: Optional[str] = Field(None, max_length=100)
    email: Optional[str] = Field(None, max_length=200)
    enabled: Optional[bool] = None


class PasswordBody(BaseModel):
    password: str = Field("", max_length=256)


# ─── Products ────────────────────────────────────────────────────────────────

class ProductResolveBody(BaseModel):
    text: str = Field("", max_length=120)


class ProductCreateBody(BaseModel):
    name: str = Field("", max_length=120)
    note: Optional[str] = Field(None, max_length=200)


class ProductUpdateBody(BaseModel):
    status: Optional[Literal["active", "retired"]] = None
    note: Optional[str] = Field(None, max_length=200)
    aliases: Optional[list[str]] = None


# ─── Log upload ──────────────────────────────────────────────────────────────

class FolderStartBody(BaseModel):
    name: str = Field("", max_length=200)
    files: int = Field(0, ge=0)
    bytes: int = Field(0, ge=0)


class ValidateBody(BaseModel):
    upload_id: str = Field("", max_length=64)
    work_order: Optional[str] = Field("", max_length=64)
    product: Optional[str] = Field("", max_length=120)
    planned_qty: Optional[StrictInt | StrictStr] = None    # strict: JSON true must not become 1


class CommitBody(ValidateBody):
    ack_warnings: bool = False
    mode: Literal["chat", "form"] = "form"


class ChatBody(BaseModel):
    upload_id: str = Field("", max_length=64)
    messages: list = Field(default_factory=list)
    known: Optional[dict] = None
    lang: str = Field("zh", max_length=8)      # UI language for the reply
