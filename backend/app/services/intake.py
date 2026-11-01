"""Lead intake: field mapping, dedupe, consent capture, instant response, team alert."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.events import CALL_SCHEDULED, CONSENT_RECORDED, DUPLICATE_SUPPRESSED, LEAD_CREATED, log_event
from app.core.logging import get_logger
from app.core.timeutils import utcnow
from app.domain.consent import parse_consent_from_fb_fields
from app.domain.phone import InvalidPhone, normalize_phone
from app.domain.state_machine import CONTACTED
from app.domain.us_timezone import state_to_timezone
from app.models import ConsentRecord, Lead
from app.services.agent import AgentService
from app.services.crm import CRMAdapter, sync_lead
from app.services.flow import transition_lead
from app.services.messenger import LeadMessenger
from app.services.notifier import Notifier

log = get_logger("leadharbor.intake")

FB_FIELD_ALIASES = {
    "full_name": "full_name", "name": "full_name", "first_name": "first_name",
    "last_name": "last_name", "email": "email", "phone": "phone",
    "phone_number": "phone", "city": "city", "state": "state",
    "zip_code": "zip", "zip": "zip", "postal_code": "zip", "country": "country",
}


@dataclass
class IntakeOutcome:
    lead: Lead
    created: bool
    actions: dict = field(default_factory=dict)


def map_fb_fields(field_data: list[dict]) -> dict:
    """FB lead field_data [{name, values}] -> normalized dict."""
    mapped: dict = {}
    for item in field_data or []:
        key = str(item.get("name", "")).strip().lower().replace(" ", "_")
        key = FB_FIELD_ALIASES.get(key, key)
        values = [v for v in item.get("values", []) if str(v).strip()]
        if values:
            mapped[key] = str(values[0]).strip()
    full = mapped.pop("full_name", "") or " ".join(
        x for x in (mapped.pop("first_name", ""), mapped.pop("last_name", "")) if x
    ).strip()
    mapped["full_name"] = full
    return mapped


def find_duplicate(
    session: Session, settings: Settings, *, fb_lead_id: str | None, phone: str | None, email: str | None
) -> Lead | None:
    now = utcnow()
    window_start = now - timedelta(hours=settings.dedupe_window_hours)
    if fb_lead_id:
        hit = session.query(Lead).filter(Lead.fb_lead_id == fb_lead_id).one_or_none()
        if hit:
            return hit
    q = session.query(Lead).filter(Lead.created_at >= window_start, Lead.opt_out.is_(False))
    if phone:
        hit = q.filter(Lead.phone == phone).order_by(Lead.id.desc()).first()
        if hit:
            return hit
    if email:
        hit = q.filter(Lead.email == email.lower() if email else None).order_by(Lead.id.desc()).first()
        if hit:
            return hit
    return None


def process_new_lead(session: Session, container, lead: Lead) -> dict:
    """Shared post-creation pipeline: CRM sync -> instant email + SMS -> AI call -> alert."""
    settings: Settings = container.settings
    messenger: LeadMessenger = container.messenger
    agent: AgentService = container.agent
    crm: CRMAdapter = container.crm
    notifier: Notifier = container.notifier
    now = utcnow()
    actions: dict = {}

    sync_lead(session, crm, lead)

    # Instant email (no quiet-hours constraint)
    if lead.consent_email and lead.email:
        subject = agent.followup_text(lead, "instant_email_subject")
        body = _instant_email_body(agent, lead, settings)
        actions["email"] = messenger.send_email(session, lead, subject, body)

    # Instant SMS (quiet-hours aware -> deferred Message if late at night)
    if lead.consent_sms and lead.phone:
        actions["sms"] = messenger.send_sms(session, lead, agent.followup_text(lead, "instant_sms"))
        if actions["sms"].get("status") in {"sent", "deferred"}:
            transition_lead(session, lead, CONTACTED, reason="instant_response")

    # AI voice call (FCC: requires explicit call consent; dialer triggers via /internal/voice)
    if lead.consent_call and lead.phone:
        log_event(session, CALL_SCHEDULED, lead.id, {"attempt_at": str(now + timedelta(minutes=5))})

    first_step = settings.cadence_steps[0]
    lead.next_followup_at = now + timedelta(hours=first_step.after_hours)
    session.flush()

    notifier.alert_new_lead(session, lead)
    return actions


def _instant_email_body(agent: AgentService, lead: Lead, settings: Settings) -> str:
    from app.domain.consent import email_footer

    body = agent.followup_text(lead, "instant_email_body")
    return body + email_footer(settings.public_base_url, settings.company_address, lead.id)


def create_lead(
    session: Session,
    container,
    *,
    full_name: str,
    email: str | None,
    phone_raw: str | None,
    city: str | None,
    state: str | None,
    country: str,
    source: str,
    campaign: str | None,
    ad_name: str | None,
    fb_lead_id: str | None,
    consent: dict[str, bool],
    interest: str | None = None,
) -> IntakeOutcome:
    settings: Settings = container.settings
    now = utcnow()

    phone = None
    if phone_raw:
        try:
            phone = normalize_phone(phone_raw)
        except InvalidPhone as exc:
            log.warning("invalid phone %r: %s", phone_raw, exc)

    duplicate = find_duplicate(
        session, settings, fb_lead_id=fb_lead_id, phone=phone, email=email.lower() if email else None
    )
    if duplicate:
        log_event(
            session, DUPLICATE_SUPPRESSED, duplicate.id,
            {"fb_lead_id": fb_lead_id, "phone": phone, "email": email},
        )
        return IntakeOutcome(duplicate, created=False)

    lead = Lead(
        fb_lead_id=fb_lead_id,
        full_name=full_name or "Unknown",
        email=email.lower() if email else None,
        phone=phone,
        city=city,
        state=state,
        country=country or "US",
        timezone=state_to_timezone(state),
        source=source,
        campaign=campaign,
        ad_name=ad_name,
        interest=interest,
        created_at=now,
        updated_at=now,
        **consent,
    )
    session.add(lead)
    session.flush()

    for kind, granted in consent.items():
        session.add(
            ConsentRecord(
                lead_id=lead.id, kind=kind.removeprefix("consent_"), granted=granted,
                source=source, created_at=now,
            )
        )
    log_event(session, CONSENT_RECORDED, lead.id, consent)
    log_event(session, LEAD_CREATED, lead.id, {"source": source, "ad": ad_name})
    session.flush()

    actions = process_new_lead(session, container, lead)
    return IntakeOutcome(lead, created=True, actions=actions)


def create_lead_from_fb(
    session: Session,
    container,
    *,
    field_data: list[dict],
    source: str = "facebook",
    fb_lead_id: str | None = None,
    campaign: str | None = None,
    ad_name: str | None = None,
) -> IntakeOutcome:
    settings: Settings = container.settings
    mapped = map_fb_fields(field_data)
    consent = parse_consent_from_fb_fields(
        field_data, default=settings.default_consent_when_missing
    )
    return create_lead(
        session, container,
        full_name=mapped.get("full_name", ""),
        email=mapped.get("email"),
        phone_raw=mapped.get("phone"),
        city=mapped.get("city"),
        state=mapped.get("state"),
        country=mapped.get("country", "US"),
        source=source,
        campaign=campaign,
        ad_name=ad_name,
        fb_lead_id=fb_lead_id,
        consent=consent,
        interest=ad_name or campaign,
    )
