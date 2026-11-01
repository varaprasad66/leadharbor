"""Internal endpoints for n8n: manual message sends + AI-voice call queue."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import api_key, get_container, get_session
from app.core.events import CALL_COMPLETED, CALL_SCHEDULED, log_event
from app.core.timeutils import utcnow
from app.models import Event, Lead

router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(api_key)])


class SendSMSIn(BaseModel):
    lead_id: int
    body: str


class SendEmailIn(BaseModel):
    lead_id: int
    subject: str
    body: str


@router.post("/messages/sms")
def send_sms(payload: SendSMSIn, session: Session = Depends(get_session), container=Depends(get_container)):
    lead = session.get(Lead, payload.lead_id)
    if lead is None:
        raise HTTPException(404, "lead not found")
    result = container.messenger.send_sms(session, lead, payload.body)
    session.commit()
    return result


@router.post("/messages/email")
def send_email(payload: SendEmailIn, session: Session = Depends(get_session), container=Depends(get_container)):
    lead = session.get(Lead, payload.lead_id)
    if lead is None:
        raise HTTPException(404, "lead not found")
    result = container.messenger.send_email(session, lead, payload.subject, payload.body)
    session.commit()
    return result


@router.get("/voice/calls-due")
def calls_due(session: Session = Depends(get_session)):
    """Leads whose AI call is due (n8n triggers Retell/Vapi with these)."""
    now = utcnow()
    scheduled = (
        session.query(Event)
        .filter(Event.type == CALL_SCHEDULED, Event.created_at <= now - timedelta(minutes=5))
        .order_by(Event.created_at)
        .all()
    )
    completed_lead_ids = {
        e.lead_id for e in session.query(Event).filter(Event.type == CALL_COMPLETED).all()
    }
    due: list[dict] = []
    seen: set[int] = set()
    for e in scheduled:
        if e.lead_id is None or e.lead_id in completed_lead_ids or e.lead_id in seen:
            continue
        lead = session.get(Lead, e.lead_id)
        if lead is None or lead.opt_out or not lead.consent_call or lead.phone is None:
            continue
        if lead.status not in ("new", "contacted"):
            continue
        seen.add(lead.id)
        due.append({"lead_id": lead.id, "name": lead.full_name, "phone": lead.phone})
    return {"ok": True, "calls_due": due}
