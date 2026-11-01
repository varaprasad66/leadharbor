# LeadHarbor 🛳️

**AI lead follow-up & sales funnel automation for real estate** — Facebook/Instagram lead ads →
instant SMS + email → AI qualification over SMS/email/voice → scored pipeline in your CRM →
booked Zoom/Meet meetings → funnel analytics dashboard.

Built for the exact workflow Dubai real-estate investment teams run with US investor leads
(10–25 leads/day), and general enough for any high-ticket lead funnel.

```
FB/IG Lead Ads ──► n8n ──► LeadHarbor API (FastAPI)
                             ├─ dedupe + enrich (state → US timezone, E.164 phone)
                             ├─ consent gate (TCPA) + quiet hours scheduler
                             ├─ instant personalized SMS (Twilio) + email (SendGrid)
                             ├─ AI agent: FAQ answers + qualification extraction
                             ├─ scoring rubric → hot/warm/nurture → Slack/WhatsApp/Email alerts
                             ├─ CRM sync (GoHighLevel or HubSpot)
                             └─ Postgres event log ──► Metabase dashboard
Inbound replies ──► AI conversation (memory per lead) ──► booking link / escalation
Cadence engine ──► day 1/3/7/14 follow-ups, stop-on-reply, quiet-hours aware
AI voice (Retell/Vapi) ──► call queue → transcript → qualification
Calendar webhook ──► meeting booked → reminders → no-show rebooking sequence
```

## Why this is production-grade

- **Compliance built-in, not bolted on** — explicit per-channel consent records, instant
  STOP/HELP handling, a persistent opt-out blocklist, timezone-aware quiet hours
  (default 8am–9pm lead-local), CAN-SPAM email footer, Twilio webhook signature verification.
- **Never loses a lead** — CRM/alert/send failures are logged as events and retried; the AI
  falls back to a deterministic agent when the LLM is down; quiet-hours messages are deferred,
  never dropped.
- **Idempotent pipelines** — Facebook `leadgen_id` + phone/email dedupe window prevents
  duplicate outreach; the cadence runner is safe to re-run.
- **Event-sourced analytics** — every action writes an event row; the funnel dashboard is just
  SQL over that log.
- **111 passing tests** across 4 milestone suites (see [docs/TESTING.md](docs/TESTING.md)).

## Quickstart

```bash
# 1. Configure
cp .env.example .env        # fill Twilio/SendGrid/LLM keys when ready — defaults run locally

# 2. Launch the full stack
docker compose up -d        # api :8000, n8n :5678, Metabase :3000, Postgres

# 3. Seed a realistic 30-day demo dataset (optional)
cd backend && python scripts/seed_demo.py

# 4. Verify
curl http://localhost:8000/health
curl -H "X-API-Key: dev-key-change-me" "http://localhost:8000/api/v1/metrics/funnel?days=30"

# 5. Import the n8n workflows (see n8n/README.md) and activate them
```

Local development without Docker:

```bash
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt          # macOS/Linux
.venv/Scripts/python -m pytest        # 111 tests, ~10s, no network needed
.venv/Scripts/uvicorn app.main:app --reload
```

Interactive API docs: `http://localhost:8000/docs`

## How it maps to the client brief

| Requirement | Implementation |
|---|---|
| 1. Instant lead response | Meta `leadgen` webhook → dedupe → consent-gated SMS+email in <60s, quiet-hours aware; AI call queued on consent |
| 2. AI qualification | Conversation agent (LLM with deterministic fallback) extracts budget/timeline/readiness; configurable scoring rubric → hot/warm/nurture |
| 3. Automated follow-up | Cadence engine: day 1/3/7/14 SMS+email steps, AI-personalized, stops on reply/opt-out, defers in quiet hours |
| 4. CRM & pipeline | GoHighLevel **and** HubSpot adapters (config-switched), stage tags/properties synced on every transition |
| 5. Appointment booking | Calendar webhook ingestion (Cal.com/GHL/Calendly), reminders at 24h/1h, auto no-show detection → rebooking sequence |
| 6. Analytics & optimization | Event-sourced funnel metrics API + Metabase dashboard (response time, reply/qualification/booking/show rates, by source/ad) |
| US SMS compliance | TCPA consent capture + language, A2P 10DLC docs, STOP/HELP, blocklist, quiet hours, recording disclosure — see [docs/COMPLIANCE.md](docs/COMPLIANCE.md) |

## API surface (v1, `X-API-Key` protected except webhooks/health)

| Endpoint | Purpose |
|---|---|
| `GET/POST /api/v1/webhooks/facebook` | Meta leadgen verification + webhook (with `/relay` for n8n) |
| `POST /api/v1/webhooks/twilio/sms` | Inbound SMS (STOP/HELP/conversation), signature-verified |
| `POST /api/v1/webhooks/sendgrid/inbound` | Inbound email replies |
| `POST /api/v1/webhooks/voice/postcall` | Retell/Vapi post-call transcripts → qualification |
| `POST /api/v1/leads` | Manual/demo lead creation (same pipeline) |
| `GET /api/v1/leads`, `/leads/{id}`, `/leads/{id}/timeline` | CRM-style lead views + full audit timeline |
| `PATCH /api/v1/leads/{id}/status` | Sales-team manual stage override (validated transitions) |
| `POST/PATCH /api/v1/appointments` | Booking ingestion + status updates |
| `POST /api/v1/cadence/run` | Cadence tick (n8n schedule, every 15 min) |
| `GET /api/v1/internal/voice/calls-due` | AI-call queue for the voice workflow |
| `POST /api/v1/internal/messages/{sms,email}` | Guarded manual sends |
| `GET /api/v1/metrics/funnel?days=30` | Funnel analytics |
| `GET /health`, `/health/ready` | Probes |
| `GET /unsubscribe/{lead_id}` | CAN-SPAM one-click opt-out |

## Configuration

Everything is env-driven (`LEADHARBOR_` prefix) — full annotated list in
[.env.example](.env.example). Highlights:

| Variable | Default | Notes |
|---|---|---|
| `CRM_PROVIDER` | `none` | `ghl` \| `hubspot` \| `none` — both supported, see [docs/CRM_INTEGRATION.md](docs/CRM_INTEGRATION.md) |
| `ALERT_CHANNELS` | `slack,whatsapp,email` | any subset, failures never block the funnel |
| `QUIET_HOURS_START` / `END` | `21` / `8` | lead-local hours; customizable per client brief |
| `DEFAULT_CONSENT_WHEN_MISSING` | `false` | keep false in production — no consent, no outreach |
| `DEDUPE_WINDOW_HOURS` | `72` | same phone/email within window = duplicate |
| `CADENCE_STEPS_JSON` | built-in 5-step | customize timing per client brief |
| `SCORING_RULES_JSON` | built-in rubric | bands/points/tiers fully configurable |
| `LLM_PROVIDER` | `mock` | `openai-compatible` works with OpenAI/Groq/OpenRouter/vLLM |

## Project structure

```
leadharbor/
├── backend/app/
│   ├── core/          config, DB, events, security, logging
│   ├── domain/        state machine, scoring, quiet hours, consent, phone/tz  (pure, tested)
│   ├── services/      agent (FAQ+LLM), intake, cadence, messenger, notifier, crm/
│   ├── api/routes/    webhooks, leads, appointments, cadence, metrics, internal, health
│   └── knowledge/     Dubai RE FAQ knowledge base (RAG source)
├── backend/tests/     111 tests across the 4 milestones
├── n8n/workflows/     6 importable workflow JSONs (see n8n/README.md)
├── docs/              architecture, compliance, CRM, deployment, testing, milestones, assumptions
└── docker-compose.yml FastAPI + Postgres + n8n + Metabase
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, data flow, state machine, design decisions
- [docs/COMPLIANCE.md](docs/COMPLIANCE.md) — TCPA/CAN-SPAM playbook incl. exact lead-form consent language
- [docs/CRM_INTEGRATION.md](docs/CRM_INTEGRATION.md) — GoHighLevel + HubSpot setup
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — production rollout, accounts to create, operations runbook
- [docs/TESTING.md](docs/TESTING.md) — test strategy + how to run per-milestone suites
- [docs/MILESTONES.md](docs/MILESTONES.md) — the 4 delivery milestones, acceptance criteria, test evidence
- [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md) — every assumption made (market data sources, brand voice, defaults)

## Extending

- Swap the FAQ retrieval for pgvector embeddings (the `FAQKnowledge` interface is one method).
- Add a CRM adapter by implementing `upsert_lead/update_stage/log_note` — 3 methods.
- Voice agents: any provider that POSTs a transcript to `/webhooks/voice/postcall` works.
