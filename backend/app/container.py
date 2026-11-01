"""Service container: builds all clients/services from settings. Tests swap transports/fakes."""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.db import make_engine, make_sessionmaker
from app.services.agent import AgentService, FAQKnowledge, LLMClient
from app.services.crm import CRMAdapter, get_crm
from app.services.messenger import LeadMessenger, SendGridClient, TwilioClient
from app.services.notifier import Notifier


@dataclass
class Container:
    settings: Settings
    engine: object
    sessionmaker: sessionmaker
    twilio: TwilioClient
    sendgrid: SendGridClient
    messenger: LeadMessenger
    notifier: Notifier
    crm: CRMAdapter
    agent: AgentService
    faq: FAQKnowledge
    llm: LLMClient
    captured_requests: dict = field(default_factory=dict)  # test introspection

    def close(self) -> None:
        self.engine.dispose()


def build_container(settings: Settings, transports: dict[str, httpx.BaseTransport] | None = None) -> Container:
    transports = transports or {}
    engine = make_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)
    captured: dict = {"twilio": [], "sendgrid": [], "slack": [], "crm": [], "llm": [], "graph": []}

    twilio = TwilioClient(
        settings.twilio_account_sid, settings.twilio_auth_token,
        settings.twilio_sms_from, settings.twilio_whatsapp_from,
        transport=transports.get("twilio"),
    )
    sendgrid = SendGridClient(settings.sendgrid_api_key, transport=transports.get("sendgrid"))
    messenger = LeadMessenger(settings, twilio, sendgrid)
    notifier = Notifier(settings, twilio, sendgrid, transport=transports.get("slack"))
    crm = get_crm(settings, transport=transports.get("crm"))
    faq = FAQKnowledge.load_default()
    llm = LLMClient(settings, transport=transports.get("llm"))
    agent = AgentService(settings, faq, llm, messenger, notifier)

    return Container(
        settings=settings, engine=engine, sessionmaker=sessionmaker, twilio=twilio, sendgrid=sendgrid,
        messenger=messenger, notifier=notifier, crm=crm, agent=agent, faq=faq, llm=llm,
        captured_requests=captured,
    )
