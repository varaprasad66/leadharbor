"""SQLAlchemy models. All datetimes are naive UTC (see core/timeutils)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.timeutils import utcnow


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fb_lead_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str | None] = mapped_column(String(320), index=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), index=True, nullable=True)  # E.164
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(10), nullable=True)
    country: Mapped[str] = mapped_column(String(80), default="US")
    timezone: Mapped[str] = mapped_column(String(64), default="America/New_York")

    source: Mapped[str] = mapped_column(String(40), default="facebook")  # facebook|instagram|manual
    campaign: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ad_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    status: Mapped[str] = mapped_column(String(32), default="new", index=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    tier: Mapped[str] = mapped_column(String(20), default="unqualified", index=True)

    budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    timeline_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    readiness: Mapped[str | None] = mapped_column(String(40), nullable=True)
    interest: Mapped[str | None] = mapped_column(String(200), nullable=True)

    consent_sms: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_email: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_call: Mapped[bool] = mapped_column(Boolean, default=False)

    opt_out: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    opt_out_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    touch_count: Mapped[int] = mapped_column(Integer, default=0)
    replies_count: Mapped[int] = mapped_column(Integer, default=0)
    next_followup_at: Mapped[datetime | None] = mapped_column(DateTime, index=True, nullable=True)

    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_reply_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    meeting_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    meeting_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    crm_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    crm_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    messages: Mapped[list["Message"]] = relationship(back_populates="lead", order_by="Message.id")
    events: Mapped[list["Event"]] = relationship(back_populates="lead", order_by="Event.id")
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="lead")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), index=True)
    direction: Mapped[str] = mapped_column(String(10))  # inbound | outbound
    channel: Mapped[str] = mapped_column(String(20))    # sms | email | call | whatsapp
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="sent")  # sent|deferred|failed|received
    provider_message_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, index=True, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    lead: Mapped[Lead] = relationship(back_populates="messages")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("leads.id"), index=True, nullable=True
    )
    type: Mapped[str] = mapped_column(String(50), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    lead: Mapped[Lead | None] = relationship(back_populates="events")


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="America/New_York")
    meeting_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider: Mapped[str] = mapped_column(String(40), default="manual")  # calcom|ghl|calendly|manual
    status: Mapped[str] = mapped_column(String(20), default="booked", index=True)
    remind_24_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    remind_1_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    lead: Mapped[Lead] = relationship(back_populates="appointments")


class ConsentRecord(Base):
    __tablename__ = "consent_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # sms | email | call
    granted: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(40), default="facebook_lead_form")
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class OptOut(Base):
    """Global blocklist by phone/email — survives lead deletion, checked before every send."""
    __tablename__ = "optouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str | None] = mapped_column(String(20), index=True, nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), index=True, nullable=True)
    channel: Mapped[str] = mapped_column(String(20), default="sms")
    reason: Mapped[str] = mapped_column(String(40), default="user_request")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


Index("ix_leads_status_next_followup", Lead.status, Lead.next_followup_at)
Index("ix_events_type_created", Event.type, Event.created_at)
