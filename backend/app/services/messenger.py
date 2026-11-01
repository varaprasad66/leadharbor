"""Provider clients (Twilio, SendGrid) + LeadMessenger that enforces the compliance guard.

Quiet-hours policy:
- Marketing/follow-up SMS: enforced (deferred Message rows are flushed by the cadence runner).
- Conversation replies: answered immediately (user-initiated contact), still consent-gated.
- Email: never quiet-hour limited; consent + opt-out gated.
"""
from __future__ import annotations

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.events import EMAIL_SENT, EMAIL_SKIPPED, SMS_QUEUED, SMS_SENT, SMS_SKIPPED, log_event
from app.core.timeutils import utcnow
from app.domain.consent import GuardDecision, can_send_email, can_send_sms
from app.models import Lead, Message
from app.services.http_client import request_with_retries

TWILIO_BASE = "https://api.twilio.com/2010-04-01"
SENDGRID_BASE = "https://api.sendgrid.com"


class TwilioClient:
    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        sms_from: str,
        whatsapp_from: str,
        transport: httpx.BaseTransport | None = None,
    ):
        self.sid = account_sid
        self.token = auth_token
        self.sms_from = sms_from
        self.whatsapp_from = whatsapp_from
        self._transport = transport

    def send_sms(self, to: str, body: str, whatsapp: bool = False) -> tuple[bool, str | None, str | None]:
        """Returns (ok, provider_message_id, error)."""
        from_ = self.whatsapp_from if whatsapp else self.sms_from
        to_ = to if to.startswith("whatsapp:") or not whatsapp else f"whatsapp:{to}"
        if not self.sid or not self.token:
            return False, None, "twilio_not_configured"
        try:
            with httpx.Client(
                transport=self._transport,
                base_url=TWILIO_BASE,
                auth=(self.sid, self.token),
                timeout=15,
            ) as client:
                resp = request_with_retries(
                    client, "POST", f"/Accounts/{self.sid}/Messages.json",
                    data={"From": from_, "To": to_, "Body": body[:1600]},
                )
            if resp.status_code in (200, 201):
                return True, resp.json().get("sid"), None
            return False, None, f"twilio_{resp.status_code}: {resp.text[:200]}"
        except httpx.HTTPError as exc:
            return False, None, f"twilio_error: {exc}"


class SendGridClient:
    def __init__(self, api_key: str, transport: httpx.BaseTransport | None = None):
        self.api_key = api_key
        self._transport = transport

    def send_email(
        self, to_email: str, subject: str, body: str, from_email: str, from_name: str
    ) -> tuple[bool, str | None]:
        if not self.api_key:
            return False, "sendgrid_not_configured"
        payload = {
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": {"email": from_email, "name": from_name},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body}],
        }
        try:
            with httpx.Client(
                transport=self._transport,
                base_url=SENDGRID_BASE,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=15,
            ) as client:
                resp = request_with_retries(client, "POST", "/v3/mail/send", json=payload)
            if resp.status_code in (200, 202):
                return True, None
            return False, f"sendgrid_{resp.status_code}: {resp.text[:200]}"
        except httpx.HTTPError as exc:
            return False, f"sendgrid_error: {exc}"


class LeadMessenger:
    def __init__(self, settings: Settings, twilio: TwilioClient, sendgrid: SendGridClient):
        self.settings = settings
        self.twilio = twilio
        self.sendgrid = sendgrid

    # ------------------------------------------------------------------ SMS
    def send_sms(
        self,
        session: Session,
        lead: Lead,
        body: str,
        *,
        enforce_quiet_hours: bool = True,
    ) -> dict:
        if not lead.phone:
            return {"ok": False, "status": "skipped", "reason": "no_phone"}
        now = utcnow()
        guard: GuardDecision = can_send_sms(
            lead, now, self.settings.quiet_hours_start, self.settings.quiet_hours_end
        )
        if enforce_quiet_hours:
            decision = guard
        else:  # conversation reply: consent + opt-out still enforced, quiet hours bypassed
            from app.domain.consent import GuardDecision as GD

            decision = (
                GD(True) if (not lead.opt_out and lead.consent_sms) else GD(False, guard.reason)
            )

        if not decision.allowed:
            if decision.reason == "quiet_hours":
                msg = Message(
                    lead_id=lead.id, direction="outbound", channel="sms", body=body,
                    status="deferred", scheduled_at=decision.defer_until, created_at=now,
                )
                session.add(msg)
                log_event(session, SMS_QUEUED, lead.id, {"scheduled_at": str(decision.defer_until)})
                session.flush()
                return {"ok": False, "status": "deferred", "deferred_until": decision.defer_until, "message_id": msg.id}
            log_event(session, SMS_SKIPPED, lead.id, {"reason": decision.reason})
            return {"ok": False, "status": "skipped", "reason": decision.reason}

        ok, provider_id, error = self.twilio.send_sms(lead.phone or "", body)
        msg = Message(
            lead_id=lead.id, direction="outbound", channel="sms", body=body,
            status="sent" if ok else "failed", provider_message_id=provider_id,
            meta={"error": error} if error else {}, created_at=now,
        )
        session.add(msg)
        if ok:
            lead.last_contacted_at = now
            log_event(session, SMS_SENT, lead.id, {"provider_id": provider_id})
        session.flush()
        return {"ok": ok, "status": "sent" if ok else "failed", "error": error, "message_id": msg.id}

    # ---------------------------------------------------------------- Email
    def send_email(self, session: Session, lead: Lead, subject: str, body: str) -> dict:
        if not lead.email:
            return {"ok": False, "status": "skipped", "reason": "no_email"}
        now = utcnow()
        guard = can_send_email(lead)
        if not guard.allowed:
            log_event(session, EMAIL_SKIPPED, lead.id, {"reason": guard.reason})
            return {"ok": False, "status": "skipped", "reason": guard.reason}
        ok, error = self.sendgrid.send_email(
            lead.email or "", subject, body, self.settings.email_from, self.settings.email_from_name
        )
        msg = Message(
            lead_id=lead.id, direction="outbound", channel="email",
            body=f"{subject}\n\n{body}", status="sent" if ok else "failed",
            meta={"subject": subject, "error": error} if error else {"subject": subject},
            created_at=now,
        )
        session.add(msg)
        if ok:
            lead.last_contacted_at = now
            log_event(session, EMAIL_SENT, lead.id, {"subject": subject})
        session.flush()
        return {"ok": ok, "status": "sent" if ok else "failed", "error": error, "message_id": msg.id}
