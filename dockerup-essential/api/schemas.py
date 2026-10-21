"""Request / response models (shown in the OpenAPI docs at /docs)."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr


# ─── Auth & accounts ─────────────────────────────────────────────────────────

class LoginBody(BaseModel):
    username: str = Field("", max_length=64)
    password: str = Field("", max_length=256)


Role = Literal["admin", "uploader", "rd"]


class LoginResult(BaseModel):
    ok: bool = True
    token: str
    role: Role
    username: str
    display_name: Optional[str] = None
    expires_at: datetime


class Identity(BaseModel):
    username: str
    role: Role
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
    role: Literal["uploader", "rd"] = "uploader"      # rd: reads every analysis, classifies failures; cannot upload


class UserUpdate(BaseModel):
    display_name: Optional[str] = Field(None, max_length=100)
    department: Optional[str] = Field(None, max_length=100)
    email: Optional[str] = Field(None, max_length=200)
    enabled: Optional[bool] = None
    role: Optional[Literal["uploader", "rd"]] = None


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


# ─── Settings (administrator) ────────────────────────────────────────────────

Number = Optional[StrictInt | StrictFloat]


class SettingsBody(BaseModel):
    """A partial update: only the fields sent change. Ranges are checked in services.settings."""
    model_config = ConfigDict(extra="forbid")
    yield_good: Number = None
    yield_warn: Number = None
    work_start: Optional[StrictStr] = Field(None, max_length=5)
    work_end: Optional[StrictStr] = Field(None, max_length=5)
    active_minutes: Optional[StrictInt] = None
    done_idle_hours: Optional[StrictInt] = None
    fail_streak: Optional[StrictInt] = None
    tp_drop_pct: Optional[StrictInt] = None
    behind_pct: Optional[StrictInt] = None
    uph_targets: Optional[dict[str, Number]] = Field(None, description="product → units per hour; null clears the override")


# ─── Work-order detail (5B) ──────────────────────────────────────────────────

class WorkOrderKey(BaseModel):
    work_order: StrictStr = Field(..., min_length=1, max_length=64)
    product_model: Optional[StrictStr] = Field(None, max_length=128)


class PlanBody(WorkOrderKey):
    planned_qty: StrictInt


class AskTurn(BaseModel):
    q: str = Field("", max_length=300)
    a: str = Field("", max_length=800)


class AskBody(WorkOrderKey):
    question: StrictStr = Field(..., min_length=1, max_length=300)
    lang: Literal["zh", "en"] = "zh"
    history: list[AskTurn] = Field(default_factory=list, max_length=3)
