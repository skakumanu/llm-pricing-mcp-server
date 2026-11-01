# Privacy Policy

**Last updated:** 2026-11-01

This policy explains what personal data [ENTITY NAME] ("we", "us") holds when you use the LLM Pricing API service, why, for how long, and what rights you have. It is written for customers in the United States (including California residents under the CCPA) and, where it applies, the EU and UK (GDPR and UK GDPR). This document is a template awaiting legal review and is not in force until the notice at the top of the page is gone.

## 1. What we hold and why

| Data | Purpose | Retention |
|---|---|---|
| Account email, organisation id, plan, billing status and Stripe customer and subscription references | Create and run your account, bill you, contact you about your account | Until you delete the account |
| Service key (stored only as a SHA-256 hash and an 8-character prefix; the owner sees only the prefix) | Authenticate your requests | Until the key is rotated or the account is deleted |
| Provider keys you store for dispatch (encrypted at rest) | Run your dispatch requests on your own provider account | Until you remove them or the account is deleted |
| Usage events (endpoint, model, token counts, cost, time) | Rate limits, budgets, usage and savings reports | Until the account is deleted |
| Dispatch receipt metadata (model, provider, token counts, cost and status, never the prompt or the response) | Audit and cost reporting for dispatch | 90 days |
| Support conversations and escalations (the messages you send the support assistant, with keys and secrets redacted before the assistant reads them) | Answer questions and, on request, escalate to the owner | Conversations you hold while signed in with your service key are kept against your organisation for 90 days after the last message, then purged; escalations and their action records for 365 days; redacted summaries of inputs and outputs for 90 days |
| Network address (transient) | Per-address rate limiting and choosing a display currency; kept in memory and not written to the databases. To estimate your country for currency and usage analytics, the address is sent to a third-party IP geolocation service (ip-api.com) | Cleared on restart; the geolocation service applies its own retention |

We do not sell personal data and we do not use it for advertising.

## 2. Who receives it

We use the service providers listed on the [Subprocessors](/subprocessors) page: a hosting provider, a payment processor, an IP geolocation service and the language-model provider that runs the support and pricing assistants. Support messages reach that language-model provider after redaction of keys and secrets. We disclose data where the law requires it.

## 3. Your rights

You can ask us to: tell you what we hold about you, give you a copy of it, correct it, delete it, or stop processing it. California residents have the right to know, delete and correct, and to not be discriminated against for exercising these rights. Residents of the EU and UK also have the rights of access, rectification, erasure, restriction, portability and objection, and may complain to their data protection authority.

You can exercise the rights of access, portability and deletion yourself, without asking anyone, with your service key:

- **Export.** `GET /billing/account/export` returns one JSON file with everything we hold about your organisation: your account record, metadata of the provider keys you stored (never the keys), usage events, dispatch receipts, budget alerts, routing history and the support conversations held under your key. It never contains your service key or another organisation's data.
- **Delete.** `POST /billing/account/delete` with `{"confirm": "DELETE", "org_id": "<your org id>"}` permanently erases all of that and stops your key working at once. It is irreversible. If you have a paid subscription it is ended immediately, so no further charge occurs; if you delete within the refund window the owner is asked to review a refund and you receive a reference (see [Refunds](/refunds)).
- **What remains.** After deletion we keep a dated marker: a keyed hash of your email address and the time of deletion. The hash lets us recognise an address we have already erased, so someone holding our database could test whether a given address was deleted, but it does not reveal addresses on its own, and it does not stop you signing up again. Support escalation records (kept for up to 365 days) may still contain what you wrote in an escalation, including an email address you typed there; write to [CONTACT EMAIL] to have them removed sooner. Other records about the service itself (owner action records and the orchestrator log) do not name you, and Stripe keeps its own records under its own policy.

To correct your data, or for any request the endpoints do not cover, ask the support assistant on [the chat page](/chat?mode=support) to escalate your request, or write to [CONTACT EMAIL]. We verify your identity before acting, normally by asking you to prove control of the account, and respond within 30 days.

## 4. Legal bases (EU and UK)

We process account and billing data to perform our contract with you, security and usage data for our legitimate interest in running a secure service, and any other data with your consent, which you can withdraw.

## 5. International transfers

The Service is hosted in the United States. If you are in the EU or UK, your data is transferred to the United States under the safeguards described in the [Data Processing Addendum](/dpa).

## 6. Security

Stored provider keys are encrypted, service keys are stored only as a hash, the Service is reachable only over TLS, and operator functions are restricted. See the [Security](/security) statement for plain facts, including what we do not have.

## 7. Children

The Service is not directed to anyone under 16 and we do not knowingly collect their data.

## 8. Changes and contact

We will update the "Last updated" date when this policy changes. Controller: [ENTITY NAME], [POSTAL ADDRESS]. Contact: [CONTACT EMAIL].
