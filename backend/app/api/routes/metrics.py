"""Funnel analytics — powers the Metabase dashboard and the API-metrics endpoint.

Rates: contact_rate = contacted/leads, reply_rate = replied/contacted,
qualification_rate = qualified/replied, booking_rate = booked/qualified,
show_rate = showed/booked.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import api_key, get_session
from app.core.events import EMAIL_SENT, SMS_SENT, log_event  # noqa: F401
from app.core.timeutils import utcnow
from app.domain import state_machine as sm
from app.models import Appointment, Event, Lead

router = APIRouter(prefix="/metrics", tags=["metrics"], dependencies=[Depends(api_key)])


@router.get("/funnel")
def funnel(days: int = Query(default=30, ge=1, le=365), session: Session = Depends(get_session)):
    now = utcnow()
    since = now - timedelta(days=days)

    leads = session.query(Lead).filter(Lead.created_at >= since).all()
    lead_ids = [l.id for l in leads]
    total = len(leads)

    first_contact: dict[int, object] = {}
    replied_ids: set[int] = set()
    if lead_ids:
        rows = (
            session.query(Event)
            .filter(
                Event.lead_id.in_(lead_ids),
                Event.created_at >= since,
                Event.type.in_([SMS_SENT, EMAIL_SENT, "sms_received", "email_received"]),
            )
            .order_by(Event.created_at)
            .all()
        )
        for e in rows:
            if e.type in (SMS_SENT, EMAIL_SENT):
                current = first_contact.get(e.lead_id)
                if current is None or e.created_at < current:
                    first_contact[e.lead_id] = e.created_at
            else:
                replied_ids.add(e.lead_id)

    contacted = len(first_contact)
    replied = len(replied_ids)
    qualified = sum(1 for l in leads if l.tier in ("hot", "warm"))
    appts = session.query(Appointment).filter(Appointment.created_at >= since).all()
    booked = len(appts)
    showed = sum(1 for a in appts if a.status in ("showed", "completed"))
    no_shows = sum(1 for a in appts if a.status == "no_show")

    gaps = [
        (first_contact[l.id] - l.created_at).total_seconds()
        for l in leads
        if l.id in first_contact and first_contact[l.id] >= l.created_at
    ]
    avg_response_seconds = round(sum(gaps) / len(gaps), 1) if gaps else None

    by_source: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    for l in leads:
        by_source[l.source] = by_source.get(l.source, 0) + 1
        by_status[l.status] = by_status.get(l.status, 0) + 1
        by_tier[l.tier] = by_tier.get(l.tier, 0) + 1

    def rate(num: int, den: int) -> float | None:
        return round(num / den, 4) if den else None

    return {
        "window_days": days,
        "leads_total": total,
        "contacted": contacted,
        "replied": replied,
        "qualified": qualified,
        "booked": booked,
        "showed": showed,
        "no_shows": no_shows,
        "avg_time_to_first_contact_seconds": avg_response_seconds,
        "contact_rate": rate(contacted, total),
        "reply_rate": rate(replied, contacted),
        "qualification_rate": rate(qualified, replied),
        "booking_rate": rate(booked, qualified),
        "show_rate": rate(showed, booked),
        "by_source": by_source,
        "by_status": by_status,
        "by_tier": by_tier,
    }
