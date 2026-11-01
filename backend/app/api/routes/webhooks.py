"""Inbound webhooks: Facebook/Instagram leadgen, Twilio SMS, SendGrid email, AI voice."""
from __future__ import annotations

import re
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.container import Container
from app.core import events as evt
from app.core.events import UNMATCHED_INBOUND, log_event
from app.core.security import verify_twilio_signature
from app.core.timeutils import utcnow
from app.domain import state_machine as sm
from app.domain.phone import last10
from app.api.deps import api_key as api_key_dep
from app.api.deps import get_container, get_session
from app.models import Lead, Message, OptOut
from app.services.agent import (
    TEMPLATES,
    _fmt,
    detect_escalation,
    detect_readiness,
    extract_budget_usd,
    extract_timeline_months,
)
from app.services.flow import rescore_lead, transition_lead
from app.services.intake import create_lead_from_fb

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

GRAPH_BASE = "https://graph.facebook.com"


def _fetch_fb_lead(container: Container, leadgen_id: str) -> list[dict]:
    """Fetch field_data via Graph API (Meta webhooks carry only the leadgen id)."""
    settings = container.settings
    with httpx.Client(base_url=GRAPH_BASE, timeout=15) as client:
        resp = client.get(
            f"/v19.0/{leadgen_id}",
            params={"fields": "field_data", "access_token": settings.facebook_access_token},
        )
        resp.raise_for_status()
        return resp.json().get("field_data", [])


@router.get("/facebook")
def facebook_verify(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    container: Container = Depends(get_container),
):
    if hub_mode == "subscribe" and hub_verify_token == container.settings.facebook_verify_token:
        return PlainTextResponse(hub_challenge or "")
    return PlainTextResponse("forbidden", status_code=403)


@router.post("/facebook")
async def facebook_leadgen(
    request: Request, session: Session = Depends(get_session)
):
    container: Container = request.app.state.container
    body = await request.json()
    if body.get("object") != "page":
        return {"ok": True, "ignored": True}
    results: list[dict] = []
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "leadgen":
                continue
            value = change.get("value", {})
            field_data = value.get("field_data")
            if field_data is None:
                leadgen_id = value.get("leadgen_id", "")
                if not leadgen_id or not container.settings.facebook_access_token:
                    log_event(session, evt.ERROR, None, {"error": "fb_missing_field_data"})
                    continue
                field_data = _fetch_fb_lead(container, leadgen_id)
            outcome = create_lead_from_fb(
                session, container,
                field_data=field_data,
                fb_lead_id=value.get("leadgen_id"),
                campaign=value.get("form_id"),
                ad_name=value.get("ad_id"),
            )
            results.append({"lead_id": outcome.lead.id, "created": outcome.created})
    session.commit()
    return {"ok": True, "results": results}


@router.post("/facebook/relay", dependencies=[Depends(api_key_dep)])
async def facebook_relay(request: Request, session: Session = Depends(get_session)):
    """n8n relays the FB Lead Ads trigger output here (works without Graph API access)."""
    container: Container = request.app.state.container
    body = await request.json()
    payload = body.get("body") if isinstance(body.get("body"), dict) else body
    outcome = create_lead_from_fb(
        session, container,
        field_data=payload.get("field_data", []),
        source=payload.get("source", "facebook"),
        fb_lead_id=payload.get("leadgen_id"),
        campaign=payload.get("form_id"),
        ad_name=payload.get("ad_id"),
    )
    session.commit()
    return {"ok": True, "lead_id": outcome.lead.id, "created": outcome.created, "actions": outcome.actions}


def _find_lead_by_phone(session: Session, phone: str) -> Lead | None:
    if not phone:
        return None
    digits = last10(phone)
    for lead in session.query(Lead).filter(Lead.phone.isnot(None)).all():
        if last10(lead.phone) == digits:
            return lead
    return None


def _opt_out(session: Session, container: Container, lead: Lead, channel: str) -> None:
    """Compliance action: bypasses the state machine (opt-out wins from any status)."""
    lead.opt_out = True
    lead.opt_out_at = utcnow()
    lead.next_followup_at = None
    log_event(session, evt.OPT_OUT, lead.id, {"channel": channel})
    session.add(OptOut(phone=lead.phone, email=lead.email, channel=channel, reason="user_request"))
    session.add(
        Message(lead_id=lead.id, direction="outbound", channel=channel,
                body=_fmt(TEMPLATES["stop_confirm"], lead, container.settings), status="sent")
    )


@router.post("/twilio/sms")
async def twilio_inbound_sms(request: Request, session: Session = Depends(get_session)):
    container: Container = request.app.state.container
    settings = container.settings
    form = await request.form()
    params: dict[str, str] = {k: v for k, v in form.multi_items() if isinstance(v, str)}
    verify_twilio_signature(request, params, settings)

    from_phone = params.get("From", "")
    body = params.get("Body", "")
    if from_phone.startswith("whatsapp:"):
        from_phone = from_phone.removeprefix("whatsapp:")

    lead = _find_lead_by_phone(session, from_phone)
    if lead is None:
        log_event(session, UNMATCHED_INBOUND, None, {"channel": "sms", "from": from_phone})
        session.commit()
        return {"ok": True, "matched": False}

    from app.domain.consent import parse_inbound_sms_command

    command = parse_inbound_sms_command(body)
    if command == "stop":
        _opt_out(session, container, lead, "sms")
        container.twilio.send_sms(lead.phone or "", _fmt(TEMPLATES["stop_confirm"], lead, settings))
        session.commit()
        return {"ok": True, "action": "opted_out"}
    if command == "help":
        log_event(session, evt.HELP_REPLY, lead.id, {})
        help_text = _fmt(TEMPLATES["help_reply"], lead, container.settings)
        ok, provider_id, _err = container.twilio.send_sms(lead.phone or "", help_text)
        session.add(
            Message(lead_id=lead.id, direction="outbound", channel="sms", body=help_text,
                    status="sent" if ok else "failed", provider_message_id=provider_id)
        )
        session.commit()
        return {"ok": True, "action": "helped"}

    container.agent.handle_inbound(session, lead, "sms", body)
    session.commit()
    return {"ok": True, "action": "reply_sent", "lead_id": lead.id}


def _extract_email(value: str) -> str | None:
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", value or "")
    return m.group(0).lower() if m else None


@router.post("/sendgrid/inbound")
async def sendgrid_inbound(request: Request, session: Session = Depends(get_session)):
    """SendGrid Inbound Parse (JSON) or a JSON relay from n8n."""
    container: Container = request.app.state.container
    content_type = request.headers.get("content-type", "")
    if "json" in content_type:
        body = await request.json()
    else:
        form = await request.form()
        body = {k: v for k, v in form.items()}
    payload: dict[str, Any] = body.get("body") if isinstance(body.get("body"), dict) else body

    email = _extract_email(str(payload.get("from", "")))
    text = str(payload.get("text") or payload.get("body_plain") or "")
    subject = str(payload.get("subject") or "")
    lead = session.query(Lead).filter(Lead.email == email).one_or_none() if email else None
    if lead is None:
        log_event(session, UNMATCHED_INBOUND, None, {"channel": "email", "from": email})
        session.commit()
        return {"ok": True, "matched": False}

    container.agent.handle_inbound(session, lead, "email", f"{subject}\n{text}".strip())
    session.commit()
    return {"ok": True, "action": "reply_sent", "lead_id": lead.id}


@router.post("/voice/postcall")
async def voice_postcall(request: Request, session: Session = Depends(get_session)):
    """Retell/Vapi post-call webhook -> qualification extraction from the transcript."""
    container: Container = request.app.state.container
    body = await request.json()
    payload: dict[str, Any] = body.get("body") if isinstance(body.get("body"), dict) else body

    lead = None
    if payload.get("lead_id"):
        lead = session.get(Lead, int(payload["lead_id"]))
    if lead is None and payload.get("phone"):
        lead = _find_lead_by_phone(session, str(payload["phone"]))
    if lead is None:
        log_event(session, UNMATCHED_INBOUND, None, {"channel": "call"})
        session.commit()
        return {"ok": True, "matched": False}

    text = str(payload.get("transcript") or payload.get("summary") or "")[:8000]
    session.add(
        Message(lead_id=lead.id, direction="inbound", channel="call", body=text,
                status="received", meta={"call_status": payload.get("call_status")})
    )
    log_event(session, evt.CALL_COMPLETED, lead.id, {"status": payload.get("call_status")})

    if budget := extract_budget_usd(text):
        lead.budget_usd = budget
    if months := extract_timeline_months(text):
        lead.timeline_months = months
    readiness = detect_readiness(text)
    if readiness != "general":
        lead.readiness = readiness
    if detect_escalation(text):
        container.notifier.alert_hot_lead(session, lead, "Lead asked for a human on the AI call.")

    transition_lead(session, lead, sm.ENGAGED, reason="ai_call_completed")
    rescore_lead(session, container.settings, lead, notifier=container.notifier, crm=None)
    session.commit()
    return {"ok": True, "lead_id": lead.id, "score": lead.score, "tier": lead.tier}
