"""Pydantic API schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.state_machine import ALL_STATUSES


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    state: str | None = None
    timezone: str
    source: str
    campaign: str | None = None
    ad_name: str | None = None
    status: str
    score: int
    tier: str
    budget_usd: float | None = None
    timeline_months: int | None = None
    readiness: str | None = None
    interest: str | None = None
    consent_sms: bool
    consent_email: bool
    consent_call: bool
    opt_out: bool
    touch_count: int
    replies_count: int
    next_followup_at: datetime | None = None
    meeting_at: datetime | None = None
    crm_provider: str | None = None
    crm_id: str | None = None
    created_at: datetime


class ManualLeadIn(BaseModel):
    full_name: str
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    state: str | None = None
    source: str = "manual"
    campaign: str | None = None
    ad_name: str | None = None
    consent_sms: bool = False
    consent_email: bool = False
    consent_call: bool = False
    interest: str | None = None


class LeadFilter(BaseModel):
    status: str | None = None
    tier: str | None = None
    source: str | None = None
    limit: int = Field(50, ge=1, le=500)
    offset: int = Field(0, ge=0)


class AppointmentIn(BaseModel):
    lead_id: int | None = None
    phone: str | None = None
    email: str | None = None
    scheduled_at: datetime
    timezone: str = "America/New_York"
    meeting_url: str | None = None
    provider: str = "manual"

    @field_validator("scheduled_at")
    @classmethod
    def require_tz(cls, v: datetime) -> datetime:
        return v


class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: int
    scheduled_at: datetime
    timezone: str
    meeting_url: str | None = None
    provider: str
    status: str
    created_at: datetime


class AppointmentStatusIn(BaseModel):
    status: str  # showed | no_show | cancelled | completed

    @field_validator("status")
    @classmethod
    def known_status(cls, v: str) -> str:
        v = v.lower()
        if v not in {"showed", "no_show", "cancelled", "completed"}:
            raise ValueError(f"unknown appointment status {v!r}")
        return v


class TimelineItem(BaseModel):
    kind: str  # event | message
    type: str
    direction: str | None = None
    channel: str | None = None
    body: str | None = None
    payload: dict[str, Any] | None = None
    created_at: datetime


class StatusIn(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def known(cls, v: str) -> str:
        if v not in ALL_STATUSES:
            raise ValueError(f"unknown lead status {v!r}")
        return v
