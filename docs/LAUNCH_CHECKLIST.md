# Launch checklist

What must be true before paid plans are sold to customers (pilot: Pro first, invited users, one month, no SLA, best effort support). This file is machine-read: `scripts/launch_checklist.py` parses the table below and `tests/test_launch_checklist.py` validates it; the audit agent uses the same parser. Update a row's Status and Completed date when you finish it; never renumber or delete an ID.

Status is one of `todo`, `in_progress`, `done`, `n/a`. Completed is a `YYYY-MM-DD` date or `-`. Owner is a role (`owner`, `lawyer`, `engineering`). Verification says how anyone can confirm the item without asking you.

| ID | Item | Status | Owner | Verification | Completed |
|---|---|---|---|---|---|
| LC-01 | Legal entity formed | todo | owner | Formation certificate stored with company records; entity name replaces the entity placeholder in docs/legal | - |
| LC-02 | Stripe account opened under the entity | todo | owner | Stripe dashboard shows the entity name and an activated account | - |
| LC-03 | Bank payout account connected to Stripe | todo | owner | Stripe dashboard Balance > Payouts shows a verified bank account | - |
| LC-04 | Stripe Tax enabled | todo | owner | Stripe dashboard Tax shows registrations and Stripe Tax active | - |
| LC-05 | Stripe price ids created and set (Pro first) | todo | owner | `GET /billing/config` reports checkout_available true for pro once legal pages are live | - |
| LC-06 | Stripe webhook endpoint registered for the seven event types | todo | owner | Stripe dashboard webhook lists exactly: checkout.session.completed, customer.subscription.updated, customer.subscription.deleted, invoice.paid, invoice.payment_failed, charge.refunded, charge.dispute.created | - |
| LC-07 | Production secrets set on Fly | todo | owner | `flyctl secrets list` shows the Stripe, webhook, operator, encryption, LITESTREAM and STRIPE_AUDIT_KEY (restricted read-only key for the compliance audit) secret names | - |
| LC-08 | Secrets backed up outside Fly | todo | owner | Password manager entry exists for each Fly secret; the encryption key is recoverable without Fly | - |
| LC-09 | Replication running | todo | engineering | `flyctl logs` shows litestream replicating billing.db, pricing_history.db, orchestrator.db and conversations.db; the bucket is private with server-side encryption and a key scoped to that bucket only (DEPLOYMENT.md "Harden the bucket") | - |
| LC-10 | Restore drill completed from an empty volume | todo | engineering | Followed docs/DEPLOYMENT.md#restore-from-the-litestream-replica end to end and its verification step passed | - |
| LC-11 | Lawyer review of the legal pages | todo | lawyer | Written sign-off from counsel on terms, privacy, refunds, dpa and subprocessors | - |
| LC-12 | Placeholders replaced in docs/legal | todo | owner | `python scripts/sync_legal.py --placeholders` exits 0 and the pages are regenerated | - |
| LC-13 | LEGAL_PAGES_LIVE turned on | todo | owner | `GET /billing/config` shows legal_pages_live true and checkout_disabled_reason null | - |
| LC-14 | Pilot cohort defined | todo | owner | Pro only, invited users, one month, no SLA; list of invitees kept by the owner | - |
| LC-15 | Support expectations wording agreed | todo | owner | Terms, FAQ and invitation say support is best effort with no response time | - |
| LC-16 | Audit report green | todo | engineering | Latest audit report (`GET /audit/reports/latest` or the /admin Compliance audit panel) has status pass or warn and no failing launch item | - |
| LC-17 | GitHub secret-scanning push protection enabled (manual) | todo | owner | Manual GitHub UI setting: repository Settings > Code security > Push protection on | - |
| LC-18 | Dependabot active | todo | engineering | .github/dependabot.yml on the default branch; GitHub Insights > Dependency graph > Dependabot lists pip and github-actions | - |
| LC-19 | CodeQL active | todo | engineering | .github/workflows/codeql.yml ran green on a pull request; Security tab shows code scanning | - |
| LC-20 | Security statement reviewed | todo | owner | /security reviewed against reality; no claim beyond what is true | - |
| LC-21 | Service key hashing migration verified | todo | engineering | After the v1.87.0 deploy `flyctl logs` shows `service keys hashed in place: N migrated` once and `nothing to migrate` on later starts; `sqlite3 /app/data/billing.db "SELECT COUNT(*) FROM customers WHERE api_key_hash IS NULL OR api_key NOT LIKE 'hashed:%'"` returns 0; a customer key still signs in. Do this before enabling replication (LC-09) | - |
| LC-22 | conversations.db replicated | todo | engineering | `flyctl logs` shows litestream replicating /app/data/conversations.db; `python scripts/check_replication.py` exits 0; the restore drill (LC-10) restores it | - |
