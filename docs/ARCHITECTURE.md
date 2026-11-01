# LeadHarbor — Architecture

## Components

| Component | Tech | Responsibility |
|---|---|---|
| **API** | FastAPI + SQLAlchemy 2 | All business logic: intake, agent, cadence, messaging, analytics |
| **Postgres** | Postgres 16 | Leads, messages, events, appointments, consents, opt-outs |
| **n8n** | n8n latest | Triggers & scheduling only — 6 thin workflows calling the API |
| **Metabase** | Metabase latest | Dashboard over the event log (SQL, no custom code) |
| **Twilio** | REST | SMS + WhatsApp sends, inbound SMS webhooks |
| **SendGrid** | REST | Transactional email, inbound parse |
| **LLM** | any OpenAI-compatible API | Conversation replies + follow-up personalization |
| **Voice AI** | Retell/Vapi | Outbound qualification calls; transcripts POSTed back |

**Design rule:** n8n owns *when*, LeadHarbor owns *what*. Every workflow is a trigger plus one
HTTP call, so the client's team can edit schedules in n8n without touching business logic, and
the business logic stays fully unit-testable.

## Data flow (new lead)

```
Meta leadgen webhook
  → n8n 01 relay → POST /webhooks/facebook/relay
    → map fields (full_name/email/phone/state/consent checkbox)
    → normalize phone (E.164), enrich timezone (state → IANA)
    → dedupe (fb_lead_id unique; phone/email within window)
    → Lead row + ConsentRecords + lead_created event
    → CRM upsert (GHL contact / HubSpot contact)          [best-effort, events on failure]
    → instant email (consent-gated)                        [immediate]
    → instant SMS (consent-gated, quiet-hours aware)       [sent OR deferred Message]
    → status: new → contacted
    → CALL_SCHEDULED event (if call consent)
    → team alert (Slack/WhatsApp/Email per config)
    → next_followup_at = now + step 1
```

## Inbound reply (SMS/email/voice)

```
Twilio/SendGrid/voice webhook
  → signature verify (Twilio HMAC-SHA1) / email match
  → STOP family? → global opt-out blocklist + confirmation → done
  → HELP?  → info reply → done
  → AgentService.handle_inbound:
      store inbound Message + sms_received/email_received event
      compose reply: LLM (JSON mode, FAQ context) → fallback deterministic agent
      extract: budget_usd, timeline_months, readiness
      transition: → engaged (audited)
      rescore → tier update → HOT? → Slack/WhatsApp/Email escalation + CRM note
      send reply (consent-gated; user-initiated → quiet hours bypassed)
```

## Lead state machine

```
new → contacted → engaged → qualified → meeting_booked → showed → won
         │            │          │              │            │
         │            │          │              ├─ no_show ──┘ (→ engaged, rebooking)
         │            ├─ nurture ┘              └─ lost
         ├─ disqualified ── (requalification allowed)
         └─ opted_out (terminal, from every active status — compliance bypass)
```

Every transition is validated against `domain/state_machine.py` and recorded as a
`stage_changed` event. Opt-outs bypass the machine on purpose (compliance wins from any state).

## Scoring rubric (configurable via `SCORING_RULES_JSON`)

| Signal | Points |
|---|---|
| Budget ≥ $200K / ≥ $100K / ≥ $50K / < $50K | 40 / 25 / 10 / 5 |
| Timeline ≤ 3 mo / ≤ 6 mo / ≤ 12 mo / later | 30 / 20 / 10 / 5 |
| Readiness: meeting / call / pricing Q / general | 30 / 25 / 15 / 5 |
| Engagement | +5 per reply, max 15 |
| **Tiers** | hot ≥ 70, warm ≥ 40, nurture ≥ 20, else unqualified |

## Quiet hours & deferral

`domain/quiet_hours.py` computes quiet windows in the *lead's* timezone (state-derived, DST-safe
via `zoneinfo`). Blocked sends become **deferred Message rows** with `scheduled_at` at the next
8am local; the cadence runner flushes due deferred messages and re-checks only consent/opt-out
(the quiet decision was already made at schedule time). Conversational replies to an inbound
message bypass quiet hours (user-initiated), marketing follow-ups never do.

## Reliability model

- CRM sync, team alerts, and messaging wrap failures into `crm_sync_failed` / `error` events —
  the funnel never stops because a third party hiccupped.
- LLM failures fall back to the deterministic agent (with an `llm_error` event), so
  qualification continues during outages.
- HTTP providers retry 5xx/network errors (3 attempts, exponential backoff).
- The cadence runner is idempotent; n8n schedule duplication is harmless.
- n8n workflow 06 (error trigger) alerts Slack for any workflow failure.

## Storage conventions

- All datetimes are **naive UTC** in the DB; conversion happens only at edges
  (`core/timeutils.py`).
- Every observable action is an `Event` row (event-sourcing) — the metrics endpoint and the
  Metabase dashboard are pure reads over it.
- `messages` rows keep full bodies for audit; `optouts` is a global blocklist independent of
  leads (survives deletions).

## Extensibility points

- **CRM**: implement `upsert_lead / update_stage / log_note` (see `services/crm/base.py`).
- **Voice provider**: anything that POSTs `{phone|lead_id, transcript, summary}` to
  `/webhooks/voice/postcall`.
- **RAG**: replace `FAQKnowledge` with pgvector retrieval — same `answer(question)` signature.
- **Scoring**: `SCORING_RULES_JSON` — no code changes needed for client-specific rubrics.
