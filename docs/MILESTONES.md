# LeadHarbor — Delivery Milestones

Four milestones, each with scope, acceptance criteria and the test evidence proving it.
Current evidence: **111/111 automated tests passing** (`cd backend && python -m pytest`).

---

## M1 — Lead capture & instant response

**Scope:** Meta leadgen webhook (with verification), field mapping, E.164 phone normalization,
state→timezone enrichment, dedupe, consent capture + audit records, instant personalized SMS +
email, CRM upsert, team alert, AI-call scheduling event.

**Acceptance criteria**
- New lead produces SMS + email within 60s of form submit (immediate in tests; network-bound in prod).
- Duplicate `leadgen_id`/phone/email never triggers double outreach.
- No consent → no outreach, sales team still alerted.
- Quiet hours → SMS deferred to 8am lead-local, email still immediate.

**Test evidence** (`test_webhook_facebook.py`, `test_state_machine.py`, `test_phone_timezone.py`,
`test_internal_and_health.py` — 30 tests): verification token accept/reject, full capture flow,
duplicate suppression, relay auth (401 without key), no-consent suppression, quiet-hours
deferral, phone/timezone edge cases, API-key enforcement.

---

## M2 — AI qualification (SMS + email)

**Scope:** inbound SMS/email webhooks (Twilio signature-verified), conversation agent with
per-lead memory, FAQ RAG over the Dubai RE knowledge base, budget/timeline/readiness extraction
(USD + AED), configurable scoring rubric, tier assignment, HOT escalation, STOP/HELP compliance.

**Acceptance criteria**
- A reply moves the lead to `engaged`, updates qualification, and gets a natural reply.
- $300k + 2-month + booking-intent conversation escalates as HOT to Slack/WhatsApp/Email.
- STOP instantly opts out everywhere with confirmation; HELP answers; blocklist persists.
- LLM outage degrades to the deterministic agent without losing the lead.

**Test evidence** (`test_webhook_twilio.py`, `test_email_and_voice.py`, `test_agent.py`,
`test_consent_compliance.py`, `test_scoring.py`, `test_llm.py` — 47 tests): extraction
(incl. AED conversion), FAQ retrieval, scoring edges, STOP/HELP matrix, signature enforcement,
LLM fallback + error events, escalation delivery.

---

## M3 — Follow-up sequences & AI voice

**Scope:** cadence engine (day 1/3/7/14 SMS+email steps), AI-personalized copy, stop-on-reply
and stop-on-opt-out, step exhaustion, booking nudges for qualified leads, quiet-hours
deferral→flush, deferred message queue, voice call queue + Retell/Vapi trigger workflow +
post-call transcript processing.

**Acceptance criteria**
- Unresponsive leads receive the full sequence then stop; engaged leads are never auto-messaged.
- Quiet-hours sends defer and flush exactly at the window edge.
- AI-call transcripts update budget/timeline/readiness and rescore the lead.

**Test evidence** (`test_cadence.py`, `test_email_and_voice.py`, `test_llm.py` — 21 tests):
due/not-due/engaged/exhausted matrix, booking nudge content, email-step routing,
defer→flush cycle, opted-out exclusion, voice extraction + escalation, call queue lifecycle.

---

## M4 — Booking, CRM & analytics

**Scope:** appointment ingestion (Cal.com/GHL/Calendly via n8n), state transitions to
`meeting_booked/showed/no_show`, 24h/1h reminders, auto no-show detection + rebooking sequence,
manual sales overrides, funnel metrics API, Metabase dashboard, both CRM adapters
(GoHighLevel + HubSpot), health probes.

**Acceptance criteria**
- Booking from any active status lands in `meeting_booked` and stops sequences.
- Missed meetings trigger rebooking automatically; attendance records `showed`.
- Metrics match a hand-computed scenario (rates, response time, per-source splits).
- CRM sync works against both providers' real API shapes and never breaks the funnel on failure.

**Test evidence** (`test_appointments.py`, `test_metrics.py`, `test_crm_adapters.py`,
`test_internal_and_health.py` — 33 tests): booking/no-show/rebooking/reminders (incl. auto
no-show), exact funnel math + empty windows, GHL/HubSpot request shapes + retries + failure
isolation, health/ready probes, unsubscribe flow.

---

## Demo script (client-facing, 10 minutes)

1. Submit the lead form test lead → narrate the instant SMS/email arriving + CRM contact.
2. Reply "I have around $250k, next 3 months" → show the AI reply, score/tier change, Slack alert.
3. Let a seeded lead hit step 1 → show the follow-up.
4. Book a meeting → show confirmation, reminder scheduling, sales alert.
5. Open Metabase funnel dashboard → response time, reply rate, qualification rate.
6. Text STOP → show instant opt-out + blocklist + no further sends.
