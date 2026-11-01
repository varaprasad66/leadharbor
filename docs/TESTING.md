# LeadHarbor — Testing Strategy

**Current status: 111 tests, all passing, in ~9s, fully offline** (providers are exercised
through `httpx.MockTransport`, so no API keys or network are needed).

```bash
cd backend
.venv/Scripts/python -m pytest            # Windows
# python -m pytest                        # macOS/Linux
.venv/Scripts/python -m pytest tests/test_cadence.py -v   # one suite
.venv/Scripts/python -m pytest -k "quiet_hours"           # by keyword
```

## Layout (by milestone)

| Suite | Milestone | Covers |
|---|---|---|
| `test_state_machine.py` | 1 | legal/illegal transitions, terminals, opt-out reachability |
| `test_phone_timezone.py` | 1 | E.164 normalization, invalid inputs, state→timezone map |
| `test_webhook_facebook.py` | 1 | verification token, leadgen → instant SMS+email, E.164/timezone enrichment, dedupe suppression, relay auth, no-consent behavior, quiet-hours deferral |
| `test_agent.py` | 2 | budget/timeline/readiness extraction ($, k/m, AED conversion), escalation detection, FAQ retrieval |
| `test_consent_compliance.py` | 2 | STOP/HELP parsing, consent gating, FB checkbox mapping (generic vs per-channel), CAN-SPAM footer |
| `test_scoring.py` | 2 | rubric bands, tiers, engagement cap, custom rules |
| `test_webhook_twilio.py` | 2 | AI conversation over SMS, qualification updates, booking intent, HOT escalation to Slack, STOP opt-out + confirmation, HELP, unmatched phones, **Twilio HMAC signature enforcement** |
| `test_email_and_voice.py` | 2+3 | inbound email replies, voice post-call extraction, escalation from transcripts |
| `test_cadence.py` | 3 | due follow-ups, sequencing, exhaustion, booking nudges, email steps, quiet-hours defer→flush, opted-out exclusion |
| `test_appointments.py` | 4 | booking transitions, attended/no-show handling, rebooking sequence, 24h/1h reminders, auto no-show detection |
| `test_metrics.py` | 4 | exact funnel math on a seeded scenario, rates, empty windows, manual overrides, unsubscribe link |
| `test_crm_adapters.py` | 4 | GHL + HubSpot request shapes, retry-on-5xx, sync failure isolation |
| `test_llm.py` | 4 | OpenAI-compatible request shape, JSON parsing, deterministic fallback + `llm_error` event |
| `test_internal_and_health.py` | 4 | guarded manual sends, voice call queue, health probes, API-key enforcement |

## Test infrastructure (`tests/conftest.py`)

- **Capturing transports**: every outbound provider call (Twilio, SendGrid, Slack, CRM, LLM,
  Graph API) is served by `httpx.MockTransport` and recorded, so tests assert exact request
  bodies, paths, and counts.
- **Time control without freezing**: quiet-hours tests pick a timezone whose local hour is 22:00
  *right now* (`tz_for_local_hour`), and cadence tests move `next_followup_at`/`scheduled_at`
  relative to real time. Deterministic, no clock mocking.
- **Fresh isolated DB per test** (sqlite via tmp_path) with the same models/code path as
  Postgres.

## What "rigorous" means here

1. **Domain purity**: state machine, scoring, quiet hours, consent are pure functions — exhaustively
   asserted, including edges (DST, midnight wrap, band boundaries, STOP variants).
2. **Integration through the real API**: webhooks are tested via FastAPI's TestClient — the same
   request the provider sends, including form encoding and signature headers.
3. **Negative paths**: no consent, opted-out, unmatched phone, LLM down, CRM down, illegal
   transitions — the system must degrade gracefully, and the tests prove it.
4. **Compliance as tests**: STOP handling, quiet hours, consent gating and the CAN-SPAM footer
   are regression-locked, not just documented.
5. **Manual pre-launch checklist** (real providers, documented in `docs/DEPLOYMENT.md`):
   real phone STOP/HELP, real inbox reply, Twilio sandbox → live number, Meta test lead,
   real booking, quiet-hours overnight observation.
