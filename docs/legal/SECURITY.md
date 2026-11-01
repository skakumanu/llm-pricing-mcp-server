# Security

**Last updated:** 2026-10-31

Plain facts about how the LLM Pricing API service is run, and what it does not have. We do not claim any certification.

## What the service does

- TLS only: the hosted service redirects plain HTTP to HTTPS.
- Provider keys you store for dispatch are encrypted at rest with AES-256-GCM; the ciphertext is never returned and the plaintext is used only to run your request.
- Service keys are shown once at signup, are not shown again in full after that (the operator console shows only a short prefix), and can be rotated at any time. They are stored unhashed in the account database on the hosting volume and in its off-site backup copy, so access to either exposes them; rotate a key if you suspect exposure.
- Secrets live in the hosting provider's secret store, not in the repository or the container image. A secret scanner runs on every change.
- The container runs as a non-root user.
- Every change passes automated gates before deploy: tests, lint, dependency vulnerability scan, static analysis and a secret scan. CodeQL code scanning and Dependabot dependency updates run in addition.
- The databases are replicated continuously to off-site object storage, and a step-by-step restore procedure is documented.
- Operator functions are on routes that accept only the operator key. Actions taken by the support agent and the owner are written to an append-only action record.
- The service is hosted in the United States (Fly.io, Ashburn).

## What the service does not have

- No SOC 2 report: the service does not have one.
- No ISO 27001 certification: the service does not have one.
- No third-party penetration test: the service has not had a penetration test by an outside firm.
- No HIPAA compliance: do not send protected health information.
- No PCI attestation of its own: card details are entered at Stripe and never reach this service.
- No high availability or failover: backups allow recovery, not continuous uptime. See the [Terms](/terms): there is no SLA.

## Reporting a vulnerability

Ask the support assistant on [the chat page](/chat?mode=support) to escalate a security report. It opens a private escalation in the owner's queue and gives you a reference. Please do not post vulnerabilities publicly, and do not include working exploit data or other customers' information. We will acknowledge on a best effort basis.

See also the [Privacy Policy](/privacy) and [Subprocessors](/subprocessors).
