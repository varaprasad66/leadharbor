"""Cross-cutting lead flow helpers: audited transitions + rescoring."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.events import QUALIFICATION_UPDATED, STAGE_CHANGED, log_event
from app.core.logging import get_logger
from app.domain import state_machine as sm
from app.domain.scoring import score_lead
from app.models import Lead
from app.services.crm import CRMAdapter, sync_lead
from app.services.notifier import Notifier

log = get_logger("leadharbor.flow")


def transition_lead(
    session: Session,
    lead: Lead,
    target: str,
    *,
    reason: str = "",
    crm: CRMAdapter | None = None,
) -> bool:
    """Audited, validated state transition. Returns False (and logs) when illegal."""
    if lead.status == target:
        return True
    try:
        sm.assert_transition(lead.status, target)
    except sm.InvalidTransition as exc:
        log.warning("blocked transition lead=%s: %s", lead.id, exc)
        log_event(session, "transition_blocked", lead.id, {"from": lead.status, "to": target})
        return False
    previous = lead.status
    lead.status = target
    log_event(session, STAGE_CHANGED, lead.id, {"from": previous, "to": target, "reason": reason})
    if crm is not None:
        sync_lead(session, crm, lead)
    return True


def rescore_lead(
    session: Session,
    settings: Settings,
    lead: Lead,
    *,
    notifier: Notifier | None = None,
    crm: CRMAdapter | None = None,
) -> str:
    """Recompute score/tier; escalate HOT leads to the sales team on tier change."""
    result = score_lead(
        budget_usd=lead.budget_usd,
        timeline_months=lead.timeline_months,
        readiness=lead.readiness,
        replies_count=lead.replies_count,
        rules=settings.scoring_rules,
    )
    previous_tier = lead.tier
    lead.score = result.score
    lead.tier = result.tier
    log_event(
        session, QUALIFICATION_UPDATED, lead.id,
        {"score": result.score, "tier": result.tier, "breakdown": result.breakdown},
    )
    if (
        result.tier == "hot"
        and previous_tier != "hot"
        and notifier is not None
        and lead.status not in {sm.MEETING_BOOKED, sm.SHOWED, sm.WON}
    ):
        log_event(session, "escalated", lead.id, {"tier": "hot", "score": result.score})
        notifier.alert_hot_lead(session, lead, f"Score {result.score} ({result.breakdown})")
        if crm is not None:
            sync_lead(session, crm, lead, note=f"HOT lead (score {result.score}) — immediate follow-up required.")
    return result.tier
