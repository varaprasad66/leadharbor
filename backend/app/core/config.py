"""Central configuration. Every knob is environment-driven (prefix LEADHARBOR_)."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BudgetBand(BaseModel):
    min_usd: float | None = None
    max_usd: float | None = None
    points: int


class ScoringRules(BaseModel):
    """Configurable qualification rubric (client question #3: fully customizable)."""

    budget_bands: list[BudgetBand] = Field(
        default_factory=lambda: [
            BudgetBand(min_usd=200_000, points=40),
            BudgetBand(min_usd=100_000, max_usd=200_000, points=25),
            BudgetBand(min_usd=50_000, max_usd=100_000, points=10),
            BudgetBand(min_usd=0, max_usd=50_000, points=5),
        ]
    )
    timeline_points: dict[str, int] = Field(
        default_factory=lambda: {"0-3": 30, "3-6": 20, "6-12": 10, "12+": 5, "unknown": 0}
    )
    readiness_points: dict[str, int] = Field(
        default_factory=lambda: {
            "meeting_requested": 30,
            "call_requested": 25,
            "pricing_question": 15,
            "general": 5,
        }
    )
    engagement_points_per_reply: int = 5
    engagement_points_max: int = 15
    tier_thresholds: dict[str, int] = Field(
        default_factory=lambda: {"hot": 70, "warm": 40, "nurture": 20}
    )


class CadenceStep(BaseModel):
    step: int
    after_hours: float
    channel: Literal["sms", "email"] = "sms"
    template: str = ""


DEFAULT_CADENCE = [
    CadenceStep(step=1, after_hours=1, channel="sms", template="followup_sms_1"),
    CadenceStep(step=2, after_hours=24, channel="email", template="followup_email_1"),
    CadenceStep(step=3, after_hours=72, channel="sms", template="followup_sms_2"),
    CadenceStep(step=4, after_hours=168, channel="email", template="followup_email_2"),
    CadenceStep(step=5, after_hours=336, channel="sms", template="followup_sms_3"),
]

# Booking nudges replace follow-ups once a lead is QUALIFIED but has not booked.
BOOKING_NUDGE_STEPS = [
    CadenceStep(step=1, after_hours=2, channel="sms", template="booking_nudge_sms"),
    CadenceStep(step=2, after_hours=48, channel="email", template="booking_nudge_email"),
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="LEADHARBOR_", extra="ignore")

    environment: str = "development"
    api_key: str = "dev-key-change-me"
    database_url: str = "sqlite:///./leadharbor.db"
    public_base_url: str = "http://localhost:8000"

    # Brand voice
    company_name: str = "Meridian Realty Dubai"
    advisor_name: str = "Maya"
    booking_link: str = "https://cal.com/leadharbor/intro"

    # Facebook / Instagram lead ads
    facebook_verify_token: str = "leadharbor-verify"
    facebook_access_token: str = ""

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_validate_signatures: bool = True  # disable only in local dev
    twilio_sms_from: str = "+15550000000"
    twilio_whatsapp_from: str = "whatsapp:+14155238886"

    # Email
    sendgrid_api_key: str = ""
    email_from: str = "leads@yourdomain.com"
    email_from_name: str = "LeadHarbor"
    company_address: str = "Office 1204, Marina Plaza, Dubai Marina, Dubai, UAE"

    # LLM
    llm_provider: Literal["mock", "openai-compatible"] = "mock"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"

    # CRM
    crm_provider: Literal["ghl", "hubspot", "none"] = "none"
    ghl_access_token: str = ""
    ghl_location_id: str = ""
    hubspot_access_token: str = ""
    hubspot_custom_properties: bool = True  # set false if leadharbor_* props were not created

    # Sales-team alerts
    alert_channels: str = "slack,whatsapp,email"  # subset of: slack,whatsapp,email
    slack_webhook_url: str = ""
    sales_alert_whatsapp: str = ""
    sales_alert_email: str = ""

    # Compliance
    quiet_hours_start: int = 21  # local hour; sends blocked [start, end)
    quiet_hours_end: int = 8
    default_consent_when_missing: bool = False
    dedupe_window_hours: int = 72

    # Cadence
    cadence_steps_json: str = ""
    max_followups: int = 8

    # Scoring
    scoring_rules_json: str = ""

    @property
    def alert_channel_list(self) -> list[str]:
        return [c.strip() for c in self.alert_channels.split(",") if c.strip()]

    @property
    def scoring_rules(self) -> ScoringRules:
        if self.scoring_rules_json:
            try:
                return ScoringRules.model_validate(json.loads(self.scoring_rules_json))
            except (ValueError, TypeError):
                pass
        return ScoringRules()

    @property
    def cadence_steps(self) -> list[CadenceStep]:
        if self.cadence_steps_json:
            try:
                return [CadenceStep.model_validate(s) for s in json.loads(self.cadence_steps_json)]
            except (ValueError, TypeError):
                pass
        return list(DEFAULT_CADENCE)


@lru_cache
def get_settings() -> Settings:
    return Settings()
