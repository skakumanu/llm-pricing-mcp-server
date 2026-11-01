# Frequently asked questions

Published answers about the LLM Pricing API service: plans, billing, service keys, rate limits, data accuracy and how to get help. The support assistant on [/chat](/chat) (Support mode) and the `support_chat` MCP tool answer only from this page, the plan catalogue and your own account; if something is not here, they will say so and offer to escalate to the owner.

## What is this service?

- Verified LLM pricing for 600+ models across 30 providers, served over REST, MCP (STDIO and `POST /mcp`) and browser tools (calculator, compare, trends, chat).
- Every curated price is checked against an external registry; a price that drifts more than 5% is withheld rather than served. [/data-quality](/data-quality) shows the current confirmed percentage.
- A routing engine recommends a model by cost and quality, and the dispatch gateway can run the best eligible model on your own provider keys.

## Plans and prices

| Plan | Price | Rate limit | Support assistant allowance | Escalation priority |
|---|---|---|---|---|
| Free | $0 / month | 30 requests / minute | $0.50 / day | standard |
| Pro | $29 / month | 120 requests / minute | $3.00 / day | pro (ahead of free) |
| Enterprise | $299 / month | 600 requests / minute | $10.00 / day | enterprise (first in the queue) |

- The rate limit applies per service key across REST, `POST /mcp` and dispatch.
- The support assistant allowance is how much LLM-backed support you can use per day (UTC). Anonymous visitors share a stricter allowance ($0.10 / day per address). When it is used up you still get FAQ links and can ask to be escalated to the owner.
- Escalation priority decides the order of the owner's queue, not a response time. Response time is best effort on every plan.
- Nothing else differs between plans. Pricing data, the router, the calculators and every MCP tool are the same for everyone.

## Signing up and your service key

- Sign up with your email on [/billing](/billing) (or `POST /billing/signup`). You get a free-tier service key immediately. No credit card.
- The key is shown exactly once. Save it. Nobody, including the owner, can display it again.
- Send it as the `x-api-key` header (REST and `POST /mcp`) or `Authorization: Bearer <key>`; the npx bridge reads `LLM_PRICING_API_KEY`.
- Signing up again with the same email returns an error and never a key.

## I lost my key or think it leaked

- If you still have the current key: open the Dashboard tab on [/billing](/billing), load it, and press **Rotate key** (or `POST /billing/key/rotate` with the key). The old key stops working immediately and the new one is shown once.
- If you no longer have the key: there is no self-serve recovery in this phase. Ask the support assistant to escalate a key-recovery request; the owner verifies you and relays a new key by a channel they can verify. Never paste a key into chat.
- If you pasted a key or other secret anywhere by mistake, rotate it.

## Upgrading, cancelling, invoices and cards

- Upgrade: Upgrade tab on [/billing](/billing) with your key. Checkout is handled by Stripe. Paid plans are a pilot. Until the [Terms](/terms), [Privacy](/privacy) and [Refund policy](/refunds) are in force, or when Stripe is not configured on a server, the buttons say so and nothing can be bought.
- Cancel, change your card, download invoices: **Manage Billing** on [/billing](/billing) opens the Stripe customer portal for your account. Cancelling returns you to Free at the end of the paid period.
- The support assistant explains and points you to these flows. It never changes your plan, card or invoices itself.

## A payment failed

- When a renewal payment fails you keep your paid tier for a 14-day grace period while your billing status is `past_due`. Update your card in Manage Billing and Stripe retries. Once the invoice is paid your status returns to `active`.
- After the grace period your account is served at the Free rate limit until the invoice is paid. Your stored plan is not deleted; it comes back when Stripe reports the payment.
- Every failed renewal, refund and dispute is also placed in the owner's queue so they can follow up.

## Refunds, double charges and disputes

- Full refund of your first payment if you ask within 14 days of paying; pro-rated for the unused period if you ask after that. The full policy is at [/refunds](/refunds).
- The owner reviews every request personally. Refunds are never granted by software, and the support assistant cannot refund, credit, discount or change a tier. Ask it anyway: it escalates the request to the owner with a reference and tells you so. Disputes and double charges are handled the same way.
- Keep the reference. You can check its status on [/chat](/chat) (Support mode) with "what is the status of escalation orc_...".

## Legal, privacy and your data

- Published policies: [Terms](/terms), [Privacy](/privacy), [Refunds](/refunds), [Data Processing Addendum](/dpa), [Subprocessors](/subprocessors) and the [Security statement](/security). They are templates awaiting legal review and show a draft notice until the owner marks them in force; paid checkout stays disabled until then.
- To see, correct or delete the data held about you, ask the support assistant to escalate a data request, or write to the contact address in the Privacy policy. The owner verifies you first.
- Security reports are escalated privately through the support assistant and never posted publicly. The Security statement lists what the service does and does not have.

## Rate limits

- Free 30, Pro 120, Enterprise 600 requests per minute per service key. Anonymous requests share a per-address limit. The current numbers are also at `GET /rate-limits/tiers`.
- Over the limit you get HTTP 429; wait a minute and retry.

## How accurate is the pricing data?

- [/data-quality](/data-quality) and the `get_data_quality` tool report the share of models with a confirmed, fresh price, how many are withheld for drift, how many are unpriced and how many are stale. `check_price_drift` lists individual disputes.
- Every price carries its source (`provider` or `registry`). Providers bill in USD; local-currency figures are additive conversions.

## Connecting a client

- MCP: [/mcp-setup](/mcp-setup) has copy-paste configs for Claude Desktop, Cursor, VS Code and Perplexity. The hosted endpoint is `https://llm-pricing-api.fly.dev/mcp`; `npx -y llm-pricing-mcp` bridges STDIO-only clients.
- REST: [/api-docs](/api-docs) lists every endpoint with its access level; [/docs](/docs) is the OpenAPI UI.

## What can the support assistant do, and what can't it?

- It can: explain plans, prices and limits; describe your own account when you send your key (plan, payment status, rate limit, key preview, 30-day savings); point you to sign-up, upgrade, Manage Billing and key rotation; answer how-to questions from the setup guides; answer general LLM pricing questions; escalate to the owner.
- It cannot: see or change anyone else's account; refund, credit, discount or change a plan; reveal a key; promise a response time; recover a key without the current one.
- Your messages are treated as data. Text that asks the assistant to ignore its rules, grant access or reveal other customers' data has no effect and is flagged in the owner's audit record.
- Keys, tokens and other secrets you paste are redacted before the assistant reads them and are never repeated back.

## How do I reach a human?

- Tell the support assistant "talk to a human" (or describe a bug, a feature request or a security problem). It opens an escalation in the owner's queue and gives you a reference. Security reports are never posted publicly.
- The owner replies as soon as possible; response time is best effort and is not a plan feature. Check the status any time with the reference.
