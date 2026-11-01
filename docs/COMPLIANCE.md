# LeadHarbor — US Compliance Playbook

This system messages US consumers on behalf of a UAE business. The controls below are
implemented in code; this document also tells the client what they must configure outside the
system. **Not legal advice** — the client should have counsel review final form language.

## 1. Consent capture (TCPA / FCC AI-voice rule)

The Facebook/Instagram lead form **must** include a consent checkbox with disclosure language.
LeadHarbor's parser (`domain/consent.py`) maps per-channel checkboxes; a single generic consent
field grants all three channels. Exact language to put in the lead form (customize the company
name):

> By submitting, I agree that Meridian Realty Dubai and its agents may contact me by phone call
> (including AI/prerecorded voice), text message (SMS), email, and WhatsApp at the number and
> email provided, with offers and information about Dubai real estate. Consent is not a condition
> of purchase. Message frequency varies; msg&data rates may apply. Reply STOP to opt out, HELP
> for help.

Why each element matters:
- **AI/prerecorded voice** — the FCC ruled in 2024 that AI-generated voices are "artificial
  voices" under the TCPA: outbound AI calls to consumers require prior express written consent.
  Without this language, the voice module must stay off (`LEADHARBOR_*` call consent gating
  enforces this: no `consent_call` → no call is queued).
- **"Not a condition of purchase"** — required so consent is valid.
- **STOP/HELP instructions** — required CTOR disclosure.

System behavior: consent flags are stored per channel on the lead **and** as immutable
`consent_records` rows (audit evidence with source + timestamp). With
`DEFAULT_CONSENT_WHEN_MISSING=false` (default), a lead without a consent field receives **no**
automated outreach — the sales team still gets the alert and can call manually per the form's
language.

## 2. A2P 10DLC (carrier registration for US SMS)

Before any live SMS: register a Twilio brand + campaign (A2P 10DLC). Expect 2–7+ business days.
Use cases to declare: "Mixed" / "Customer care & marketing" with sample messages (the templates
in `services/agent.py` serve as samples). Until approved, the system runs in sandbox mode.

## 3. Quiet hours (state + federal norms)

Marketing SMS/calls are blocked 9:00 pm → 8:00 am in the **lead's local timezone**
(`QUIET_HOURS_START=21`, `QUIET_HOURS_END=8`, fully customizable). Timezone comes from the
lead's US state (form field) with safe fallback to Eastern. Blocked sends are **deferred, not
dropped**. Replies to an inbound message are answered immediately (user-initiated
conversation), which is the standard industry reading; marketing sequences never bypass the
window.

## 4. STOP / HELP / opt-out

- Inbound `STOP, STOPALL, UNSUBSCRIBE, CANCEL, END, QUIT` (case-insensitive, first word) →
  immediate global opt-out: lead flagged, `optouts` blocklist row created, follow-up schedule
  cleared, confirmation SMS sent, `opt_out` event logged. Opt-out wins from any lifecycle state
  (deliberately bypasses the state machine).
- `HELP, INFO` → informational reply, no marketing.
- Email footer contains a physical mailing address (CAN-SPAM) and a one-click unsubscribe link
  (`GET /unsubscribe/{lead_id}`) — both enforced on every outbound email template.
- The blocklist is checked before every send and survives lead deletion.

## 5. AI voice call disclosure

Retell/Vapi agents must disclose at call start: identify the company and, where two-party
consent states are involved, state that the call may be recorded. Configure this in the agent's
first spoken line; the caller is only triggered for leads with `consent_call` (FCC AI-voice
rule, section 1).

## 6. Data handling

- Personal data (name/phone/email + conversation bodies) is stored in the client's own Postgres
  instance; no third-party sink beyond Twilio/SendGrid/CRM/LLM providers configured by the
  client.
- LLM calls: only conversation text + lead first name are sent to the provider. Disable by
  running `LLM_PROVIDER=mock` (deterministic agent) if the client requires zero LLM data share.
- All sends carry a request-id and are reconstructable from `messages` + `events` (audit trail).

## 7. Client-side checklist (outside the code)

1. Add the consent checkbox language to every lead form (FB + Instagram).
2. Register A2P 10DLC brand + campaign; use a dedicated number.
3. Set up SendGrid domain authentication (SPF/DKIM/DMARC) and warm the sending domain.
4. Publish a privacy policy URL on the landing page/funnel.
5. Have counsel confirm the consent language + recording disclosure for their entity.
6. Disable Twilio's default auto-responses (LeadHarbor sends STOP/HELP confirmations itself).
