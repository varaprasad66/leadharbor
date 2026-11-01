# LeadHarbor — Deployment & Operations

## 1. Accounts to create (day 1 — some take days)

| Account | Why | Lead time |
|---|---|---|
| Twilio + A2P 10DLC brand/campaign | US SMS | 2–7+ business days |
| SendGrid + domain auth (SPF/DKIM/DMARC) | email deliverability | hours + warmup days |
| Meta Developer App + `leadgen` webhook subscription | instant lead push | ~1 day |
| OpenAI/Groq/OpenRouter key (or client's) | LLM | minutes |
| Retell **or** Vapi account + agent | AI voice calls | ~1 day incl. script |
| GoHighLevel or HubSpot token | CRM sync | minutes |
| Cal.com (or GHL calendar) + Zoom/Meet | booking | minutes |
| Slack incoming webhook | team alerts | minutes |

## 2. Hosting

`docker compose up -d` runs: `postgres`, `api`, `n8n`, `metabase`. Minimum: 2 vCPU / 4 GB RAM.
Recommended production hardening:

- Put the API behind a reverse proxy with TLS (Caddy/Traefik/nginx) — webhooks require HTTPS.
- Set a strong `LEADHARBOR_API_KEY`; set `LEADHARBOR_ENVIRONMENT=production`.
- Twilio signature validation stays **on** (`LEADHARBOR_TWILIO_VALIDATE_SIGNATURES=true`,
  default).
- Back up the Postgres volume; the whole funnel state is in there.
- Point `LEADHARBOR_PUBLIC_BASE_URL` at the public URL (used in unsubscribe links).

Managed alternative: Postgres (Neon/RDS) via `LEADHARBOR_DATABASE_URL`, API on any container
host, n8n cloud. The API is stateless apart from the DB.

## 3. Go-live checklist

1. `cp .env.example .env`, fill every key; `docker compose up -d`.
2. Import n8n workflows (`n8n/README.md`), set env vars, set error workflow (06), activate.
3. Meta webhooks: subscribe to `leadgen`, point to workflow 01 URL, verify token, **submit the
   lead form test lead** and confirm: lead row + instant SMS + email + CRM contact.
4. Twilio: configure inbound message webhook → workflow 02 URL; text "hi" from a real phone.
5. Cal.com/GHL: booking webhook → workflow 04 URL; book a test slot.
6. Retell: agent with Dubai RE script + booking tool; workflow 05 triggers calls; test post-call
   webhook.
7. Metabase: first login → connect to Postgres (host `postgres`, db `leadharbor`) → build the
   funnel dashboard from `events`/`leads`/`appointments` (the metrics SQL in
   `api/routes/metrics.py` is the reference).
8. Seed demo data **only** on staging (`scripts/seed_demo.py` refuses non-empty databases).

## 4. Operations runbook

| Symptom | Where to look | Action |
|---|---|---|
| No leads arriving | n8n executions (workflow 01), Meta webhook logs | check webhook URL/token; test lead |
| SMS not sent | `events` rows: `sms_skipped` reasons (`no_consent`, `opted_out`, `quiet_hours`) | expected guard behavior, or missing consent on the form |
| `sms_queued` stuck | cadence workflow (03) active? | check n8n schedule + API health |
| CRM not updating | `crm_sync_failed` events (payload has error) | token expiry / property missing (HubSpot custom props) |
| LLM replies stop | `llm_error` events | system falls back automatically; fix provider key/quota |
| Voice calls not dialing | `/internal/voice/calls-due` empty | check `consent_call` on leads + workflow 05 schedule |
| Anything failing | n8n error workflow (06) → Slack | inspect execution + `error` events |

Useful queries:

```sql
-- last 10 failures
SELECT type, payload, created_at FROM events
WHERE type IN ('error','crm_sync_failed','llm_error') ORDER BY id DESC LIMIT 10;
-- funnel today
SELECT status, count(*) FROM leads WHERE created_at > now() - interval '1 day' GROUP BY status;
```

## 5. Upgrades & data

- Schema: `Base.metadata.create_all` on boot (additive). For destructive changes use Alembic
  (recommended once schema churn starts).
- Zero-downtime: single API container is fine at this scale (10–25 leads/day ≈ trivial load);
  scale out by adding API replicas behind the proxy — all state is in Postgres.
