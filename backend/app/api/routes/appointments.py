"""Appointments: booking webhook ingestion, status updates, sales notifications."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import api_key, get_container, get_session
from app.core.events import APPOINTMENT_BOOKED, APPOINTMENT_STATUS_CHANGED, log_event
from app.core.timeutils import utcnow
from app.domain import state_machine as sm
from app.domain.phone import last10, normalize_phone
from app.models import Appointment, Lead
from app.schemas import AppointmentIn, AppointmentOut, AppointmentStatusIn
from app.services.agent import TEMPLATES, _fmt
from app.services.flow import transition_lead

router = APIRouter(prefix="/appointments", tags=["appointments"], dependencies=[Depends(api_key)])


def _find_lead(session: Session, payload: AppointmentIn) -> Lead | None:
    if payload.lead_id:
        return session.get(Lead, payload.lead_id)
    if payload.email:
        lead = session.query(Lead).filter(Lead.email == str(payload.email).lower()).one_or_none()
        if lead:
            return lead
    if payload.phone:
        try:
            norm = normalize_phone(payload.phone)
        except Exception:
            norm = payload.phone
        digits = last10(norm)
        for lead in session.query(Lead).filter(Lead.phone.isnot(None)).all():
            if last10(lead.phone) == digits:
                return lead
    return None


@router.post("", response_model=AppointmentOut, status_code=201)
def book_appointment(
    payload: AppointmentIn,
    session: Session = Depends(get_session),
    container=Depends(get_container),
):
    """Called by the calendar webhook (Cal.com/GHL/Calendly) via n8n."""
    lead = _find_lead(session, payload)
    if lead is None:
        raise HTTPException(404, "lead not found")

    # guide the lead into a booking-legal state
    if not sm.can_transition(lead.status, sm.MEETING_BOOKED):
        if not transition_lead(session, lead, sm.ENGAGED, reason="pre_booking"):
            raise HTTPException(409, f"cannot book meeting from status {lead.status}")
    if not transition_lead(session, lead, sm.MEETING_BOOKED, reason="meeting_booked"):
        raise HTTPException(409, "booking transition refused")

    appt = Appointment(
        lead_id=lead.id,
        scheduled_at=payload.scheduled_at.replace(tzinfo=None),
        timezone=payload.timezone,
        meeting_url=payload.meeting_url,
        provider=payload.provider,
        status="booked",
    )
    session.add(appt)
    session.flush()

    lead.meeting_at = appt.scheduled_at
    lead.meeting_url = appt.meeting_url
    lead.next_followup_at = None  # booking supersedes follow-up nudges
    log_event(session, APPOINTMENT_BOOKED, lead.id,
              {"appointment_id": appt.id, "scheduled_at": str(appt.scheduled_at)})

    container.notifier.alert_meeting_booked(session, lead, appt)
    from app.services.crm import sync_lead

    sync_lead(session, container.crm, lead,
              note=f"Meeting booked for {appt.scheduled_at} ({appt.timezone}).")
    session.commit()
    session.refresh(appt)
    return appt


@router.patch("/{appointment_id}/status", response_model=AppointmentOut)
def update_appointment_status(
    appointment_id: int,
    payload: AppointmentStatusIn,
    session: Session = Depends(get_session),
    container=Depends(get_container),
):
    appt = session.get(Appointment, appointment_id)
    if appt is None:
        raise HTTPException(404, "appointment not found")
    lead = session.get(Lead, appt.lead_id)

    previous = appt.status
    appt.status = payload.status
    log_event(session, APPOINTMENT_STATUS_CHANGED, appt.lead_id,
              {"appointment_id": appt.id, "from": previous, "to": payload.status})

    if payload.status == "showed" or payload.status == "completed":
        transition_lead(session, lead, sm.SHOWED, reason="meeting_attended")
    elif payload.status == "no_show":
        transition_lead(session, lead, sm.NO_SHOW, reason="meeting_no_show")
        transition_lead(session, lead, sm.ENGAGED, reason="rebooking_sequence")
        lead.next_followup_at = utcnow() + timedelta(hours=2)
        container.messenger.send_sms(session, lead, _fmt(TEMPLATES["rebook_sms"], lead, container.settings))
        container.notifier.alert_meeting_no_show(session, lead, appt)
    elif payload.status == "cancelled":
        lead.next_followup_at = utcnow() + timedelta(hours=2)

    session.commit()
    session.refresh(appt)
    return appt


@router.get("", response_model=list[AppointmentOut])
def list_appointments(session: Session = Depends(get_session)):
    return session.query(Appointment).order_by(Appointment.scheduled_at.desc()).limit(200).all()
