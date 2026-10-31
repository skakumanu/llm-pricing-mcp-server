# Subprocessors

**Last updated:** 2026-10-31

[ENTITY NAME] uses the following third parties to run the Service. This document is a template awaiting legal review and is not in force until the notice at the top of the page is gone.

| Subprocessor | Role | Location | Data that reaches it |
|---|---|---|---|
| Fly.io | Hosting: runs the application and holds the databases and encrypted backups volume | United States | All data the Service holds (see the [Privacy Policy](/privacy)) |
| Stripe | Payments: checkout, subscriptions, invoices, customer portal | United States | Email, plan and payment details you enter at Stripe; card numbers go to Stripe only and never reach us |
| Anthropic or OpenAI (the language-model provider the assistants run on, chosen by the operator) | Language model for the support and pricing assistants | United States | Messages you send to the assistants, after keys and secrets are redacted |
| ip-api.com | IP geolocation: estimates the visitor's country and city from the network address | United States | The visitor's network address only (sent unencrypted over HTTP today) |
| Backup storage (S3-compatible object storage, such as Cloudflare R2 or Amazon S3) | Off-site replication of the databases for disaster recovery | Chosen by the operator | Copies of the databases, including encrypted provider keys |
| LiteLLM pricing registry | Data source: public model price data we read | Public repository | None: we fetch public price data and send no customer data |

If you use dispatch, requests go to the model provider you choose, using your own provider key, under your agreement with that provider. That provider is not our subprocessor.

We will update this page, and its "Last updated" date, before adding or replacing a subprocessor. Questions: [CONTACT EMAIL].
