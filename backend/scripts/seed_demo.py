"""Seed a realistic 30-day demo dataset: ~60 US leads spread across the funnel.

Usage (from backend/):  .venv/Scripts/python scripts/seed_demo.py
                        DATABASE_URL=postgresql+psycopg://... python scripts/seed_demo.py
Deterministic (seeded RNG) so demos and screenshots are reproducible.
"""
from __future__ import annotations

import random
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402
from app.core.db import make_engine, make_sessionmaker  # noqa: E402
from app.core.db import Base  # noqa: E402
from app.core.timeutils import utcnow  # noqa: E402
from app.domain import state_machine as sm  # noqa: E402
from app.domain.scoring import score_lead  # noqa: E402
from app.domain.us_timezone import state_to_timezone  # noqa: E402
from app.models import Appointment, Event, Lead, Message, OptOut  # noqa: E402

FIRST = ["James", "Michael", "Jennifer", "Linda", "David", "Sarah", "Robert", "Emily", "Daniel",
         "Jessica", "Kevin", "Amanda", "Brian", "Rachel", "Jason", "Megan", "Eric", "Lauren",
         "Mark", "Olivia", "Paul", "Sophia", "Todd", "Natalie", "Craig", "Hannah", "Derek",
         "Grace", "Sean", "Madison", "Travis", "Chloe", "Randall", "Victoria", "Glen", "Abigail"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Miller", "Davis", "Wilson",
        "Anderson", "Taylor", "Thomas", "Moore", "Martin", "Lee", "Clark", "Lewis", "Walker",
        "Hall", "Young", "King", "Wright", "Scott", "Green", "Baker", "Adams", "Nelson",
        "Carter", "Mitchell", "Turner", "Phillips", "Campbell", "Parker", "Evans", "Edwards"]
STATES = ["CA", "TX", "NY", "FL", "IL", "WA", "GA", "AZ", "CO", "MA", "NC", "NJ", "VA", "OH"]
ADS = ["Golden Visa US", "Dubai 8% Yields", "Own a Dubai Marina Studio", "Dubai vs Miami Invest",
       "0% Tax Property", "Dubai Payment Plans"]
SOURCES = ["facebook"] * 8 + ["instagram"] * 2


def main() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session = make_sessionmaker(engine)()
    rng = random.Random(42)
    now = utcnow()

    if session.query(Lead).count():
        print("Database not empty — skipping seed (use a fresh database).")
        return

    funnels = (
        ["meeting_booked"] * 3 + ["showed"] * 2 + ["won"] * 1
        + ["qualified"] * 6 + ["engaged"] * 8 + ["nurture"] * 6
        + ["contacted"] * 12 + ["new"] * 8 + ["disqualified"] * 3 + ["opted_out"] * 2
    )
    rng.shuffle(funnels)

    used_phones = set()
    for idx, final_status in enumerate(funnels):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        state = rng.choice(STATES)
        phone = f"+1555{rng.randint(200, 989)}{rng.randint(10000, 99999)}"
        while phone in used_phones:
            phone = f"+1555{rng.randint(200, 989)}{rng.randint(10000, 99999)}"
        used_phones.add(phone)
        created = now - timedelta(days=rng.uniform(0.2, 30))
        consent_call = rng.random() < 0.8

        lead = Lead(
            fb_lead_id=f"seed_{idx}",
            full_name=f"{first} {last}",
            email=f"{first.lower()}.{last.lower()}{idx}@example.com",
            phone=phone,
            city=None,
            state=state,
            timezone=state_to_timezone(state),
            source=rng.choice(SOURCES),
            campaign="dubai_investors_us",
            ad_name=rng.choice(ADS),
            consent_sms=True,
            consent_email=rng.random() < 0.9,
            consent_call=consent_call,
            created_at=created,
            updated_at=created,
        )
        session.add(lead)
        session.flush()
        session.add(Event(type="lead_created", lead_id=lead.id, payload={"source": lead.source},
                          created_at=created))
        session.add(Event(type="consent_recorded", lead_id=lead.id,
                          payload={"consent_sms": True}, created_at=created))

        final_status = final_status if lead.consent_call or final_status != "opted_out" else "contacted"
        if final_status == "opted_out":
            lead.status = sm.CONTACTED
            session.add(Message(lead_id=lead.id, direction="outbound", channel="sms",
                                body="Instant SMS", status="sent", created_at=created))
            session.add(Event(type="sms_sent", lead_id=lead.id, payload={}, created_at=created))
            opt_ts = created + timedelta(hours=2)
            lead.opt_out = True
            lead.opt_out_at = opt_ts
            lead.status = sm.OPTED_OUT
            session.add(Event(type="opt_out", lead_id=lead.id, payload={"channel": "sms"}, created_at=opt_ts))
            session.add(OptOut(phone=lead.phone, email=lead.email, channel="sms", created_at=opt_ts))
            continue

        # every lead got the instant SMS + email
        session.add(Message(lead_id=lead.id, direction="outbound", channel="sms",
                            body="Instant SMS", status="sent", created_at=created))
        session.add(Event(type="sms_sent", lead_id=lead.id, payload={}, created_at=created))
        if lead.consent_email:
            session.add(Event(type="email_sent", lead_id=lead.id, payload={}, created_at=created))
        lead.status = sm.CONTACTED
        lead.last_contacted_at = created
        lead.touch_count = 1

        tier_plan = {"meeting_booked": "hot", "showed": "hot", "won": "hot", "qualified": "hot",
                     "engaged": rng.choice(["warm", "hot", "nurture"]),
                     "nurture": rng.choice(["nurture", "warm"]),
                     "contacted": "unqualified", "new": "unqualified",
                     "disqualified": "unqualified"}[final_status]

        if final_status == "new":
            lead.status = sm.NEW
            lead.touch_count = 0
            lead.next_followup_at = created + timedelta(hours=1)
            continue

        if final_status in ("contacted", "nurture", "disqualified"):
            replies = 0 if final_status == "contacted" else rng.randint(1, 2)
            lead.replies_count = replies
            budget = {"contacted": None,
                      "nurture": rng.choice([40_000, 60_000, 90_000]),
                      "disqualified": rng.choice([5_000, 15_000])}[final_status]
            lead.budget_usd = budget
            lead.timeline_months = {"contacted": None, "nurture": rng.choice([6, 9, 12]),
                                    "disqualified": rng.choice([18, 24])}[final_status]
            lead.readiness = "general"
            result = score_lead(budget_usd=budget, timeline_months=lead.timeline_months,
                                readiness="general", replies_count=replies)
            lead.score, lead.tier = result.score, result.tier
            reply_ts = created + timedelta(hours=rng.uniform(1, 20))
            if replies:
                lead.last_reply_at = reply_ts
                lead.status = sm.ENGAGED if final_status == "nurture" else lead.status
                if final_status == "nurture":
                    lead.status = sm.NURTURE
                if final_status == "disqualified":
                    lead.status = sm.DISQUALIFIED
                session.add(Message(lead_id=lead.id, direction="inbound", channel="sms",
                                    body="Sample reply", status="received", created_at=reply_ts))
                session.add(Event(type="sms_received", lead_id=lead.id, payload={}, created_at=reply_ts))
            if final_status == "contacted":
                lead.touch_count = rng.randint(1, 4)
            lead.next_followup_at = created + timedelta(hours=24 * lead.touch_count)
            if final_status == "disqualified":
                lead.next_followup_at = None
            continue

        # qualified-and-above paths
        budget = rng.choice([150_000, 220_000, 300_000, 450_000, 700_000])
        timeline = rng.choice([1, 2, 3, 4])
        replies = rng.randint(2, 5)
        lead.budget_usd, lead.timeline_months, lead.replies_count = budget, timeline, replies
        lead.readiness = "meeting_requested"
        result = score_lead(budget_usd=budget, timeline_months=timeline,
                            readiness="meeting_requested", replies_count=replies)
        lead.score, lead.tier = result.score, result.tier
        reply_ts = created + timedelta(hours=rng.uniform(0.5, 8))
        lead.last_reply_at = reply_ts
        lead.status = sm.ENGAGED
        for r in range(replies):
            session.add(Message(lead_id=lead.id, direction="inbound", channel="sms",
                                body="Sample qualification reply", status="received",
                                created_at=reply_ts + timedelta(minutes=10 * r)))
            session.add(Event(type="sms_received", lead_id=lead.id, payload={},
                              created_at=reply_ts + timedelta(minutes=10 * r)))
        session.add(Event(type="qualification_updated", lead_id=lead.id,
                          payload={"tier": lead.tier, "score": lead.score},
                          created_at=reply_ts + timedelta(minutes=30)))

        if final_status == "engaged":
            lead.next_followup_at = None
            continue

        lead.status = sm.QUALIFIED
        qualified_ts = reply_ts + timedelta(hours=1)
        meeting_ts = created + timedelta(days=rng.uniform(1, 12))
        meeting_ts = meeting_ts.replace(hour=17, minute=0, second=0, microsecond=0)
        if meeting_ts < now:
            lead.status = final_status if final_status in ("showed", "won") else sm.MEETING_BOOKED
            lead.meeting_at = meeting_ts
            appt = Appointment(lead_id=lead.id, scheduled_at=meeting_ts,
                               timezone=lead.timezone, meeting_url="https://zoom.us/j/seed",
                               provider="calcom",
                               status="showed" if final_status in ("showed", "won") else (
                                   "no_show" if rng.random() < 0.2 else "booked"),
                               created_at=qualified_ts)
            session.add(appt)
            session.flush()
            session.add(Event(type="appointment_booked", lead_id=lead.id,
                              payload={"appointment_id": appt.id}, created_at=qualified_ts))
            if appt.status == "no_show":
                lead.status = sm.ENGAGED
            if final_status == "won":
                appt.status = "showed"
                lead.status = sm.WON
                session.add(Event(type="stage_changed", lead_id=lead.id,
                                  payload={"to": "won"}, created_at=meeting_ts + timedelta(hours=2)))
        else:
            lead.status = sm.MEETING_BOOKED
            lead.meeting_at = meeting_ts
            session.add(Appointment(lead_id=lead.id, scheduled_at=meeting_ts, timezone=lead.timezone,
                                    meeting_url="https://zoom.us/j/seed", provider="calcom",
                                    status="booked", created_at=qualified_ts))
            session.add(Event(type="appointment_booked", lead_id=lead.id, payload={},
                              created_at=qualified_ts))
        lead.next_followup_at = None

    session.commit()
    counts = {
        "leads": session.query(Lead).count(),
        "messages": session.query(Message).count(),
        "events": session.query(Event).count(),
        "appointments": session.query(Appointment).count(),
    }
    session.close()
    print(f"Seeded: {counts}")
    print("Open Metabase (http://localhost:3000) or GET /api/v1/metrics/funnel to see the funnel.")


if __name__ == "__main__":
    main()
