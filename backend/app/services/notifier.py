"""Internal sales-team alerts across Slack, WhatsApp and Email (client question #4).

Any subset can be enabled via LEADHARBOR_ALERT_CHANNELS. Failures never propagate.
"""
from __future__ import annotations

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.events import ERROR, TEAM_ALERTED, log_event
from app.core.logging import get_logger
from app.core.timeutils import to_local, utcnow
from app.models import Appointment, Lead
from app.services.messenger import SendGridClient, TwilioClient

log = get_logger("leadharbor.notify")


class Notifier:
    def __init__(
        self,
        settings: Settings,
        twilio: TwilioClient,
        sendgrid: SendGridClient,
        transport: httpx.BaseTransport | None = None,
    ):
        self.settings = settings
        self.twilio = twilio
        self.sendgrid = sendgrid
        self._transport = transport

    def _describe_lead(self, lead: Lead) -> str:
        local = to_local(utcnow(), lead.timezone)
        return (
            f"{lead.full_name} ({lead.email or 'no email'}, {lead.phone or 'no phone'})\n"
            f"Location: {lead.city or '?'}, {lead.state or '?'} (local {local:%H:%M})\n"
            f"Status: {lead.status} | Tier: {lead.tier} | Score: {lead.score}\n"
            f"Source: {lead.source} / {lead.ad_name or lead.campaign or 'n/a'}\n"
            f"Budget: {lead.budget_usd or 'unknown'} | Timeline: {lead.timeline_months or '?'} months"
        )

    def _send_all(self, session: Session, lead: Lead, title: str, detail: str) -> None:
        now = utcnow()
        for channel in self.settings.alert_channel_list:
            try:
                ok, error = self._send_one(channel, lead, f"{title}\n{detail}")
                log_event(
                    session, TEAM_ALERTED, lead.id,
                    {"channel": channel, "title": title, "ok": ok, "error": error},
                )
            except Exception as exc:  # noqa: BLE001 - alerting must never break flow
                log.warning("alert via %s failed: %s", channel, exc)
                log_event(session, ERROR, lead.id, {"alert_channel": channel, "error": str(exc)[:300]})

    def _send_one(self, channel: str, lead: Lead, text: str) -> tuple[bool, str | None]:
        if channel == "slack":
            if not self.settings.slack_webhook_url:
                return False, "slack_not_configured"
            with httpx.Client(transport=self._transport, timeout=10) as client:
                resp = client.post(self.settings.slack_webhook_url, json={"text": text})
                if resp.status_code == 200:
                    return True, None
                return False, f"slack_{resp.status_code}"
        if channel == "whatsapp":
            if not self.settings.sales_alert_whatsapp:
                return False, "whatsapp_not_configured"
            ok, _sid, err = self.twilio.send_sms(self.settings.sales_alert_whatsapp, text, whatsapp=True)
            return ok, err
        if channel == "email":
            if not self.settings.sales_alert_email:
                return False, "email_not_configured"
            ok, err = self.sendgrid.send_email(
                self.settings.sales_alert_email,
                "LeadHarbor alert",
                text,
                self.settings.email_from,
                self.settings.email_from_name,
            )
            return ok, err
        return False, f"unknown_channel:{channel}"

    # ------------------------------------------------------------- public API
    def alert_new_lead(self, session: Session, lead: Lead) -> None:
        self._send_all(session, lead, "🔥 New lead captured", self._describe_lead(lead))

    def alert_hot_lead(self, session: Session, lead: Lead, summary: str) -> None:
        self._send_all(
            session, lead,
            "🚨 HOT LEAD — call now",
            f"{self._describe_lead(lead)}\n\nConversation summary:\n{summary}",
        )

    def alert_meeting_booked(self, session: Session, lead: Lead, appt: Appointment) -> None:
        local = to_local(appt.scheduled_at, appt.timezone)
        self._send_all(
            session, lead,
            "📅 Meeting booked",
            f"{self._describe_lead(lead)}\n\nWhen: {local:%a %d %b %Y %H:%M} ({appt.timezone})\nLink: {appt.meeting_url or 'n/a'}",
        )

    def alert_meeting_no_show(self, session: Session, lead: Lead, appt: Appointment) -> None:
        self._send_all(
            session, lead,
            "⚠️ Meeting no-show — rebooking sequence started",
            self._describe_lead(lead),
        )
