"""Lead management API + public unsubscribe endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.api.deps import api_key, get_container, get_session
from app.core.events import OPT_OUT, log_event
from app.core.timeutils import utcnow
from app.models import Event, Lead, Message, OptOut
from app.schemas import LeadOut, ManualLeadIn, StatusIn, TimelineItem
from app.services.flow import transition_lead
from app.services.intake import create_lead

router = APIRouter(prefix="/leads", tags=["leads"], dependencies=[Depends(api_key)])


@router.post("", response_model=LeadOut, status_code=201)
def create_manual_lead(
    payload: ManualLeadIn,
    session: Session = Depends(get_session),
    container=Depends(get_container),
):
    """Manual/demo lead creation (same pipeline as FB intake)."""
    outcome = create_lead(
        session, container,
        full_name=payload.full_name,
        email=str(payload.email) if payload.email else None,
        phone_raw=payload.phone,
        city=payload.city,
        state=payload.state,
        country="US",
        source=payload.source,
        campaign=payload.campaign,
        ad_name=payload.ad_name,
        fb_lead_id=None,
        consent={
            "consent_sms": payload.consent_sms,
            "consent_email": payload.consent_email,
            "consent_call": payload.consent_call,
        },
        interest=payload.interest,
    )
    session.commit()
    session.refresh(outcome.lead)
    return outcome.lead


@router.get("", response_model=list[LeadOut])
def list_leads(
    status: str | None = Query(default=None),
    tier: str | None = Query(default=None),
    source: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
):
    q = session.query(Lead)
    if status:
        q = q.filter_by(status=status)
    if tier:
        q = q.filter_by(tier=tier)
    if source:
        q = q.filter_by(source=source)
    return q.order_by(Lead.id.desc()).offset(offset).limit(limit).all()


@router.get("/{lead_id}", response_model=LeadOut)
def get_lead(lead_id: int, session: Session = Depends(get_session)):
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(404, "lead not found")
    return lead


@router.get("/{lead_id}/timeline", response_model=list[TimelineItem])
def lead_timeline(lead_id: int, session: Session = Depends(get_session)):
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(404, "lead not found")
    items: list[TimelineItem] = []
    for e in session.query(Event).filter(Event.lead_id == lead_id).all():
        items.append(TimelineItem(kind="event", type=e.type, payload=e.payload, created_at=e.created_at))
    for m in session.query(Message).filter(Message.lead_id == lead_id).all():
        items.append(
            TimelineItem(kind="message", type=m.direction, direction=m.direction,
                         channel=m.channel, body=m.body, created_at=m.created_at)
        )
    return sorted(items, key=lambda i: i.created_at, reverse=True)


@router.patch("/{lead_id}/status", response_model=LeadOut)
def set_status(
    lead_id: int,
    payload: StatusIn,
    session: Session = Depends(get_session),
    container=Depends(get_container),
):
    """Manual override for the sales team (won/lost/nurture...)."""
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(404, "lead not found")
    if not transition_lead(session, lead, payload.status, reason="manual_override", crm=container.crm):
        raise HTTPException(409, f"illegal transition {lead.status} -> {payload.status}")
    session.commit()
    session.refresh(lead)
    return lead


# --- public unsubscribe (no API key; used by the CAN-SPAM email footer) ---
public_router = APIRouter(tags=["public"])


@public_router.get("/unsubscribe/{lead_id}", response_class=HTMLResponse)
def unsubscribe(lead_id: int, session: Session = Depends(get_session)):
    lead = session.get(Lead, lead_id)
    if lead is not None:
        lead.opt_out = True
        lead.opt_out_at = utcnow()
        lead.next_followup_at = None
        session.add(OptOut(email=lead.email, phone=lead.phone, channel="email", reason="unsubscribe_link"))
        log_event(session, OPT_OUT, lead.id, {"channel": "email", "via": "unsubscribe_link"})
        session.commit()
    return HTMLResponse(
        "<html><body style='font-family:sans-serif;text-align:center;padding:3rem'>"
        "<h2>You're unsubscribed</h2><p>You will not receive further marketing messages.</p>"
        "</body></html>"
    )
