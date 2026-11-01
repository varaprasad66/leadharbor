# LeadHarbor — Assumptions & Defaults

The client had no historical lead data and answered configuration questions with "make it
production-grade / you decide". Every decision made on their behalf is listed here so it can be
reversed by config, not code.

## Business assumptions

| # | Assumption | Where to change |
|---|---|---|
| 1 | Brand voice: "Meridian Realty Dubai", advisor persona "Maya" | `COMPANY_NAME`, `ADVISOR_NAME` |
| 2 | Booking link `cal.com/leadharbor/intro` (Cal.com; GHL/Calendly work the same via webhook) | `BOOKING_LINK` |
| 3 | Qualification rubric: budget bands ($200K/$100K/$50K), timeline (≤3/6/12 mo), readiness weights — see ARCHITECTURE.md | `SCORING_RULES_JSON` |
| 4 | Cadence: SMS +1h, email +24h, SMS +72h, email +7d, SMS +14d; booking nudges +2h/+48h for qualified leads | `CADENCE_STEPS_JSON` |
| 5 | Quiet hours 21:00–08:00 lead-local | `QUIET_HOURS_START/END` |
| 6 | Dedupe window 72h on phone/email | `DEDUPE_WINDOW_HOURS` |
| 7 | Leads are US-based (default country US, +1 parsing, US state→tz map) | `domain/phone.py`, `domain/us_timezone.py` |
| 8 | Conversation replies answer immediately even in quiet hours (user-initiated); sequences never do | `messenger.py` policy |
| 9 | A single generic consent checkbox grants all channels (client opted for "do the best") | `domain/consent.py` parser |
| 10 | Email-only leads (no phone) proceed via email; phone-only leads skip email | messenger guards |
| 11 | Auto no-show after 120-minute grace, rebooking nudge +2h later | `cadence.py::NO_SHOW_GRACE_MINUTES` |
| 12 | SMS replies truncated to 320 chars (2 segments) to control cost | `agent.py` |

## Market data in the knowledge base (verified 2026-11)

Used by the AI to answer FAQs; each claim traces to public sources:

- **Golden Visa**: AED 2M (~USD 545K) property, completed + freehold + DLD-registered,
  10-year renewable, family sponsorship — [GetGoldenVisa guide 2026](https://getgoldenvisa.com/uae-citizenship-by-investment-or-talent),
  [Dubai Visitor Visa 2026 guide](https://dubaivisitorvisa.com/blog/dubai-golden-visa).
- **Yields**: city average ~6.3–6.5%; JVC ~7–8% (studios up to ~7.9%); Dubai Silicon Oasis
  ~8.7%; International City ~8.6%; Marina/Business Bay lower yield, higher appreciation —
  [Palm Legacy ROI 2025](https://palmlegacy.com/top-5-areas-in-dubai-offering-the-highest-property-roi-in-2025),
  [GuestReady yield report](https://www.guestready.com/blog/best-rental-yields-in-dubai).
- Costs/taxes (0% property/income tax, 4% DLD fee), AED peg 3.6725, mortgage LTV ranges,
  off-plan escrow, DTCM short-term rental permits: standard market knowledge — the agent is
  instructed to defer exact figures to advisors.

The agent never quotes numbers outside the knowledge base and adds "exact numbers depend on the
project" framing. Update `backend/app/knowledge/dubai_real_estate_faq.md` quarterly; retrieval
picks changes up on restart.

## Technical assumptions

- **n8n is the scheduler/trigger layer only** — if the client drops n8n, every trigger is a
  documented HTTP call (cron + curl can replace it).
- **SQLite works for dev/tests; Postgres for production** — no DB-specific SQL in the hot path
  (metrics aggregation is Python-side by design).
- **Any OpenAI-compatible endpoint** (OpenAI, Groq, OpenRouter, vLLM) works via
  `LLM_BASE_URL`; `gpt-4o-mini` class models are the intended tier (SMS-length replies).
- **Voice provider assumed Retell-shaped**; Vapi/others only need to POST
  `{phone|lead_id, transcript, summary}` to `/webhooks/voice/postcall`.
- **Twilio LOOKUP/state field**: timezone derives from the form's state field (asked on the
  form). If the client removes that field, add zip→tz mapping or Twilio Lookup.
- **Graph API fetch**: Meta webhooks deliver only `leadgen_id`; the system fetches field data
  with a page token. The n8n relay path bypasses this for teams without Graph access.
- **Schema migrations**: `create_all` on boot is additive; introduce Alembic when destructive
  schema changes are needed.

## Deliberate non-goals (v1)

- No A/B testing of message variants (roadmap).
- No multilingual agent (US leads → English).
- No payment processing / document collection in-funnel.
- No per-user auth on the dashboard (Metabase provides its own).
