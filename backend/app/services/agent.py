"""The AI brain: FAQ retrieval (RAG), qualification extraction, conversation agent.

LLM: any OpenAI-compatible API. If the LLM fails or is disabled (provider=mock),
a deterministic fallback agent keeps the funnel running — production safety net.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.events import EMAIL_RECEIVED, LLM_ERROR, REPLY_HANDLED, SMS_RECEIVED, log_event
from app.core.logging import get_logger
from app.core.timeutils import utcnow
from app.domain.consent import email_footer
from app.models import Lead, Message
from app.services.flow import rescore_lead, transition_lead
from app.services.http_client import request_with_retries
from app.services.messenger import LeadMessenger
from app.services.notifier import Notifier

log = get_logger("leadharbor.agent")

KB_PATH = Path(__file__).resolve().parent.parent / "knowledge" / "dubai_real_estate_faq.md"
AED_PER_USD = 3.6725

# --------------------------------------------------------------------------- FAQ
_STOPWORDS = {
    "a", "an", "the", "is", "are", "do", "does", "i", "my", "me", "can", "how",
    "what", "when", "where", "in", "on", "of", "for", "to", "and", "it", "with",
    "you", "your", "about", "there", "any", "if", "we", "us",
}


class FAQKnowledge:
    def __init__(self, markdown: str):
        self.sections: list[tuple[str, str]] = []
        current_title: str | None = None
        body: list[str] = []
        for line in markdown.splitlines():
            if line.startswith("## "):
                if current_title is not None:
                    self.sections.append((current_title, "\n".join(body).strip()))
                current_title = line[3:].strip()
                body = []
            elif current_title is not None:
                body.append(line)
        if current_title is not None:
            self.sections.append((current_title, "\n".join(body).strip()))

    @classmethod
    def load_default(cls) -> "FAQKnowledge":
        if KB_PATH.exists():
            return cls(KB_PATH.read_text(encoding="utf-8"))
        log.warning("knowledge base not found at %s — FAQ disabled", KB_PATH)
        return cls("")

    def answer(self, question: str) -> tuple[str, str] | None:
        q_tokens = {t for t in re.findall(r"[a-z0-9]+", question.lower()) if t not in _STOPWORDS}
        if not q_tokens:
            return None
        best, best_score = None, 0
        for title, body in self.sections:
            hay = re.findall(r"[a-z0-9]+", f"{title} {body}".lower())
            score = sum(1 for t in q_tokens if t in hay)
            if score > best_score:
                best, best_score = (title, body), score
        if best and best_score >= 2:
            return best
        return None


# -------------------------------------------------------------------- extraction
_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


def extract_budget_usd(text: str) -> float | None:
    """Parses '$250k', '250,000', '1.5M', 'AED 2m', '500 thousand' -> USD float."""
    if not text:
        return None
    t = text.lower().replace(",", "")
    amounts: list[float] = []
    is_aed = "aed" in t or "dirham" in t
    suffix = {"k": 1_000, "m": 1_000_000, "million": 1_000_000, "thousand": 1_000}

    if is_aed:
        for m in re.finditer(r"aed\s*([\d.]+)\s*(k|m|million|thousand)?", t):
            val = float(m.group(1)) * suffix.get((m.group(2) or "").strip(), 1)
            amounts.append(val / AED_PER_USD)
        return max(amounts) if amounts else None

    for m in re.finditer(r"\$?\s*([\d.]+)\s*(k\b|m\b|million|thousand)", t):
        amounts.append(float(m.group(1)) * suffix.get(m.group(2), 1))
    for m in re.finditer(r"\$\s*([\d]{4,})", t):
        amounts.append(float(m.group(1)))
    for m in re.finditer(r"\b([\d.]+)\s*(hundred|thousand)\b", t):
        amounts.append(float(m.group(1)) * (100 if m.group(2) == "hundred" else 1_000))
    if "half a million" in t or "half million" in t:
        amounts.append(500_000)
    return max(amounts) if amounts else None


def extract_timeline_months(text: str) -> int | None:
    if not text:
        return None
    t = text.lower()
    if re.search(r"\b(asap|immediately|right away|this month)\b", t):
        return 1
    if "next month" in t:
        return 1
    if "next year" in t:
        return 12
    if re.search(r"(\d+)\s*years?", t):
        return int(re.search(r"(\d+)\s*years?", t).group(1)) * 12
    m = re.search(r"(\d+)\s*(?:-|to)?\s*(\d+)?\s*months?", t)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(" + "|".join(_NUM_WORDS) + r")\s*months?\b", t)
    if m:
        return _NUM_WORDS[m.group(1)]
    if "end of year" in t:
        return 6
    return None


def detect_readiness(text: str) -> str:
    t = text.lower()
    if re.search(r"\b(book|schedule|appointment|calendar|availability|available slot|set up a meeting|meet)\b", t):
        return "meeting_requested"
    if re.search(r"\b(call me|give me a call|ring me|phone me|have them call)\b", t):
        return "call_requested"
    if re.search(r"\b(price|pricing|cost|how much|payment plan|roi|yield|returns)\b", t):
        return "pricing_question"
    return "general"


def detect_escalation(text: str) -> bool:
    t = text.lower()
    return bool(
        re.search(
            r"\b(human|real person|actual person|representative|manager|"
            r"sales ?rep|someone else|speak to someone|talk to someone|your team)\b",
            t,
        )
    )


# ------------------------------------------------------------------------- LLM
@dataclass
class AgentReply:
    text: str
    intent: str = "answer"  # answer | booking | escalate
    escalate: bool = False
    used_llm: bool = False
    extracted: dict = field(default_factory=dict)


class LLMClient:
    """OpenAI-compatible chat client returning strict JSON, or None on any failure."""

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self._transport = transport

    def _system_prompt(self) -> str:
        s = self.settings
        return (
            f"You are {s.advisor_name}, a warm, professional assistant for {s.company_name}, "
            "helping US investors interested in Dubai real estate.\n"
            "Goals: (1) answer questions using only the provided knowledge context, "
            "(2) qualify the lead on budget (USD), timeline (months), and readiness, "
            f"(3) invite qualified leads to book a meeting: {s.booking_link}\n"
            "Rules: reply max 320 characters; never guarantee returns or profits; no legal or tax "
            "advice; if the user asks for a human or regulated advice, set escalate=true.\n"
            'Output ONLY JSON: {"reply": str, "intent": "answer|booking|escalate", "escalate": bool, '
            '"budget_usd": number|null, "timeline_months": int|null, '
            '"readiness": "meeting_requested|call_requested|pricing_question|general"}'
        )

    def chat_json(self, history: list[dict], knowledge_context: str | None) -> dict | None:
        if self.settings.llm_provider != "openai-compatible" or not self.settings.llm_api_key:
            return None
        messages: list[dict] = [{"role": "system", "content": self._system_prompt()}]
        if knowledge_context:
            messages[0]["content"] += f"\n\nKnowledge context:\n{knowledge_context}"
        messages.extend(history)
        try:
            with httpx.Client(transport=self._transport, base_url=self.settings.llm_base_url, timeout=30) as client:
                resp = request_with_retries(
                    client, "POST", "/chat/completions",
                    headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                    json={
                        "model": self.settings.llm_model,
                        "messages": messages,
                        "temperature": 0.4,
                        "response_format": {"type": "json_object"},
                    },
                )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return json.loads(content.strip().removeprefix("```json").removeprefix("```").removesuffix("```"))
        except Exception as exc:  # noqa: BLE001
            log.warning("llm call failed: %s", exc)
            return None


# ------------------------------------------------------------------- templates
class _SafeDict(dict):
    def __missing__(self, key):  # noqa: ANN202
        return ""


def _fmt(template: str, lead: Lead, settings: Settings) -> str:
    return template.format_map(
        _SafeDict(
            first_name=(lead.full_name or "there").split(" ")[0],
            advisor=settings.advisor_name,
            company=settings.company_name,
            booking_link=settings.booking_link,
            email_from=settings.email_from,
            address=settings.company_address,
        )
    )


TEMPLATES: dict[str, str] = {
    "instant_sms": (
        "Hi {first_name}, it's {advisor} from {company} — thanks for your interest in Dubai real estate! "
        "Quick one so I can send the right options: are you exploring around $100K or $250K+? "
        "Reply STOP to opt out."
    ),
    "instant_email_subject": "Your Dubai investment guide, {first_name}",
    "instant_email_body": (
        "Hi {first_name},\n\n"
        "Thanks for your interest in investing in Dubai real estate. A few highlights our US "
        "investors care about most:\n\n"
        "1. Residency: property from AED 2M (~USD 545K) can qualify for the 10-year Golden Visa.\n"
        "2. Yields: Dubai averages ~6.5% gross rental yield; value communities like JVC and "
        "Dubai Silicon Oasis reach 7-8.7%.\n"
        "3. Tax: 0% property tax and 0% income tax on rental income; AED is pegged to the USD.\n\n"
        "Book a free 1-on-1 with our senior advisors here: {booking_link}\n\n"
        "Best,\n{advisor}\n{company}"
    ),
    "followup_sms_1": (
        "Hi {first_name}, {advisor} from {company} here. Whenever you're ready, I can send our "
        "top 3 Dubai picks around your budget. Want me to send them over?"
    ),
    "followup_email_1_subject": "Top 3 Dubai investment picks, {first_name}",
    "followup_email_1_body": (
        "Hi {first_name},\n\n"
        "Following up on your interest in Dubai real estate. Based on what investors with your "
        "profile usually ask for, here are three communities worth a look:\n\n"
        "- Jumeirah Village Circle (JVC): strong rental demand, ~7-8% gross yields.\n"
        "- Dubai Silicon Oasis: best value yields right now (~8.7%).\n"
        "- Dubai Marina / Business Bay: premium, better for capital appreciation.\n\n"
        "Happy to tailor this to your budget and goals — grab a slot here: {booking_link}\n\n"
        "Best,\n{advisor}\n{company}"
    ),
    "followup_sms_2": (
        "Just checking in, {first_name} — still interested in Dubai property? Happy to answer "
        "anything about yields, the Golden Visa or payment plans."
    ),
    "followup_email_2_subject": "Still thinking about Dubai, {first_name}?",
    "followup_email_2_body": (
        "Hi {first_name},\n\n"
        "Circling back — a lot of our US investors start with a 20-minute call to map out budget, "
        "residency (Golden Visa) and target yields.\n\n"
        "Pick a time here: {booking_link}\n\nBest,\n{advisor}\n{company}"
    ),
    "followup_sms_3": (
        "Last note from me, {first_name} — if Dubai isn't a priority right now, no worries at all. "
        "If it is, I'm one message away or you can book directly: {booking_link}"
    ),
    "booking_nudge_sms": (
        "Great news, {first_name} — you qualify for a 1-on-1 with our senior Dubai team. "
        "Grab a slot here: {booking_link}"
    ),
    "booking_nudge_email_subject": "Your private Dubai investment consultation, {first_name}",
    "booking_nudge_email_body": (
        "Hi {first_name},\n\n"
        "Based on your criteria you qualify for a private consultation with our senior investment "
        "advisors — they'll walk you through the best current projects, payment plans and the "
        "Golden Visa process.\n\nBook here: {booking_link}\n\nBest,\n{advisor}\n{company}"
    ),
    "reminder_24_sms": (
        "Reminder: your Dubai investment call is tomorrow at {local_time}. Need to reschedule? "
        "Just reply here."
    ),
    "reminder_1_sms": (
        "Your Dubai investment call starts in about an hour. Talk soon! — {advisor}, {company}"
    ),
    "rebook_sms": (
        "Sorry we missed each other, {first_name} — want to pick a new time for your Dubai "
        "investment call? {booking_link}"
    ),
    "stop_confirm": (
        "You have been unsubscribed and will receive no further messages from {company}. "
        "Reply HELP for assistance."
    ),
    "help_reply": (
        "{company}: happy to help! Reply with your question anytime, or reach us at {email_from}. "
        "Msg&data rates may apply. Reply STOP to opt out."
    ),
}

EMAIL_TEMPLATES = {
    "followup_email_1": ("followup_email_1_subject", "followup_email_1_body"),
    "followup_email_2": ("followup_email_2_subject", "followup_email_2_body"),
    "booking_nudge_email": ("booking_nudge_email_subject", "booking_nudge_email_body"),
}


def render_email(lead: Lead, template_key: str, settings: Settings) -> tuple[str, str]:
    subject_key, body_key = EMAIL_TEMPLATES[template_key]
    subject = _fmt(TEMPLATES[subject_key], lead, settings)
    body = _fmt(TEMPLATES[body_key], lead, settings)
    body += email_footer(settings.public_base_url, settings.company_address, lead.id)
    return subject, body


# --------------------------------------------------------------------- service
class AgentService:
    def __init__(
        self,
        settings: Settings,
        faq: FAQKnowledge,
        llm: LLMClient,
        messenger: LeadMessenger,
        notifier: Notifier,
    ):
        self.settings = settings
        self.faq = faq
        self.llm = llm
        self.messenger = messenger
        self.notifier = notifier

    # ------------------------------------------------------------ composition
    def _fallback_reply(self, lead: Lead, text: str) -> AgentReply:
        # deterministic extraction runs regardless of which branch answers
        budget = extract_budget_usd(text)
        timeline = extract_timeline_months(text)
        readiness = detect_readiness(text)
        extracted = {
            "budget_usd": budget,
            "timeline_months": timeline,
            "readiness": readiness if readiness != "general" else None,
        }

        if detect_escalation(text):
            return AgentReply(
                "Of course — I'll have one of our senior advisors reach out personally. "
                "Is there anything specific you'd like us to prepare?",
                intent="escalate",
                escalate=True,
                extracted=extracted,
            )
        if readiness == "meeting_requested":
            return AgentReply(
                f"Perfect! You can pick a time that suits you here: {self.settings.booking_link} "
                "— and I'll send a confirmation right away.",
                intent="booking",
                extracted=extracted,
            )
        faq_hit = self.faq.answer(text)
        if faq_hit:
            title, body = faq_hit
            sentences = re.split(r"(?<=[.!?])\s+", body.strip())
            summary = " ".join(sentences[:2])[:400]
            return AgentReply(
                f"{summary} Happy to share options that match — what budget are you considering?",
                intent="answer",
                extracted=extracted,
            )
        if budget is not None:
            pretty = f"${budget:,.0f}"
            return AgentReply(
                f"Thanks! Great — around {pretty}. And when are you looking to make the investment?",
                intent="answer",
                extracted=extracted,
            )
        return AgentReply(
            "Thanks for reaching out! To point you to the right Dubai projects — "
            "what budget range are you considering?",
            extracted={},
        )

    def compose_reply(self, session: Session, lead: Lead, text: str) -> AgentReply:
        """LLM-first with deterministic fallback. Also returns extracted fields."""
        history = [
            {"role": "assistant" if m.direction == "outbound" else "user", "content": m.body}
            for m in lead.messages[-10:]
            if m.status in {"sent", "received", "deferred"}
        ]
        history.append({"role": "user", "content": text})
        faq_hit = self.faq.answer(text)
        knowledge = f"Q: {faq_hit[0]}\n{faq_hit[1]}" if faq_hit else None

        data = self.llm.chat_json(history, knowledge)
        if data and isinstance(data, dict) and data.get("reply"):
            extracted = {
                "budget_usd": data.get("budget_usd"),
                "timeline_months": data.get("timeline_months"),
                "readiness": data.get("readiness"),
            }
            return AgentReply(
                text=str(data["reply"])[:1600],
                intent=data.get("intent", "answer"),
                escalate=bool(data.get("escalate")),
                used_llm=True,
                extracted=extracted,
            )
        # LLM unavailable/failed -> deterministic fallback (never lose the lead)
        if self.settings.llm_provider == "openai-compatible":
            log_event(session, LLM_ERROR, lead.id, {"stage": "compose_reply"})
        return self._fallback_reply(lead, text)

    # ------------------------------------------------------------- inbound SMS/email
    def handle_inbound(self, session: Session, lead: Lead, channel: str, text: str) -> AgentReply:
        now = utcnow()
        session.add(
            Message(lead_id=lead.id, direction="inbound", channel=channel, body=text,
                    status="received", created_at=now)
        )
        log_event(session, SMS_RECEIVED if channel == "sms" else EMAIL_RECEIVED, lead.id,
                  {"text": text[:500]})
        lead.replies_count += 1
        lead.last_reply_at = now

        reply = self.compose_reply(session, lead, text)

        # merge extracted qualification (newest non-null wins)
        if reply.extracted.get("budget_usd"):
            lead.budget_usd = float(reply.extracted["budget_usd"])
        if reply.extracted.get("timeline_months"):
            lead.timeline_months = int(reply.extracted["timeline_months"])
        if reply.extracted.get("readiness") and reply.extracted["readiness"] != "general":
            lead.readiness = reply.extracted["readiness"]
        if reply.intent == "booking":
            lead.readiness = "meeting_requested"

        transition_lead(session, lead, "engaged", reason="inbound_reply")
        rescore_lead(session, self.settings, lead, notifier=self.notifier, crm=None)

        # conversational reply: consent-gated, quiet-hours bypassed (user-initiated)
        if channel == "sms":
            self.messenger.send_sms(session, lead, reply.text[:320], enforce_quiet_hours=False)
        else:
            subject = f"Re: your Dubai real estate question"
            self.messenger.send_email(session, lead, subject, reply.text)

        log_event(session, REPLY_HANDLED, lead.id, {"channel": channel, "intent": reply.intent})
        return reply

    # ------------------------------------------------------------- follow-ups
    def followup_text(self, lead: Lead, template_key: str) -> str:
        return _fmt(TEMPLATES[template_key], lead, self.settings)

    def personalize_followup(self, session: Session, lead: Lead, template_key: str) -> str:
        """LLM personalization of follow-up copy; falls back to the template."""
        base = self.followup_text(lead, template_key)
        data = self.llm.chat_json(
            [{"role": "user", "content": (
                f"Rewrite this follow-up message so it feels personal and human. Reference the "
                f"lead's ad interest if relevant. Keep it under 320 characters, friendly tone, "
                f"no guarantees. Message: {base}"
            )}],
            knowledge_context=None,
        )
        if data and isinstance(data, dict) and data.get("reply"):
            return str(data["reply"])[:320]
        if self.settings.llm_provider == "openai-compatible":
            log_event(session, LLM_ERROR, lead.id, {"stage": "personalize_followup"})
        return base
