# n8n — LeadHarbor workflow pack

Six importable workflows. LeadHarbor's FastAPI owns all business logic; n8n owns
triggers, scheduling and delivery — which keeps every workflow auditable and
editable by the client's team.

## Import

n8n UI → Workflows → **Import from file** → select each JSON in `workflows/`.

## Environment variables (n8n → Settings → Variables, or docker env)

| Variable | Example | Used by |
|---|---|---|
| `LEADHARBOR_API_URL` | `http://api:8000` (compose) or your public URL | all |
| `LEADHARBOR_API_KEY` | value of `LEADHARBOR_API_KEY` in `.env` | all |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook | 06 |
| `RETELL_API_URL` | `https://api.retellai.com` | 05 |
| `RETELL_API_KEY` | Retell API key | 05 |
| `RETELL_AGENT_ID` | Retell agent configured with the Dubai RE script | 05 |
| `RETELL_FROM_NUMBER` | your Retell/Twilio caller ID | 05 |

## Workflows

| # | File | Trigger | What it does |
|---|------|---------|--------------|
| 01 | `01_lead_capture.json` | Webhook (point the Meta app / n8n FB Lead Ads trigger here) | Relays lead fields to `POST /api/v1/webhooks/facebook/relay` → instant response pipeline |
| 02 | `02_inbound_sms.json` | Twilio Trigger (message received) | Forwards inbound SMS to the AI conversation endpoint |
| 03 | `03_cadence_scheduler.json` | Schedule, every 15 min | Runs the follow-up cadence engine (follow-ups, deferred sends, reminders, no-shows) |
| 04 | `04_appointments.json` | Webhook (Cal.com / GHL calendar) | Records booked meetings, notifies sales, stops sequences |
| 05 | `05_ai_voice_calls.json` | Schedule, every 5 min | Polls the call queue, triggers outbound AI calls via Retell (Vapi: swap node URL) |
| 06 | `06_error_alerts.json` | Error trigger (set as **Settings → Error workflow** on every workflow) | Alerts Slack when any workflow fails |

## Activation checklist

1. Import all six workflows; in workflow 02 select your Twilio credential.
2. Set the environment variables above.
3. Set workflow 06 as the **Error workflow** in the settings of workflows 01–05.
4. Activate all workflows.
5. Point Meta → Webhooks → `leadgen` field to the workflow 01 production webhook URL
   (or subscribe the n8n Facebook Lead Ads Trigger node and connect it to the HTTP node).
6. Configure Cal.com/GHL to POST bookings to the workflow 04 production URL.
