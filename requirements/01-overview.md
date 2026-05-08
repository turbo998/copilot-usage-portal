# 01 — Overview

## Product
A private personal portal hosted on Azure that visualizes the GitHub Copilot **token usage** of a single user (a Copilot Pro/Pro+ individual) across multiple Copilot client applications. The intent is to give the user transparency into how their AI Credits are spent across daily/weekly trends, clients, and models.

## Primary user
One person (the owner). The portal is **not** multi-tenant. Authentication only needs to admit the owner. No collaboration, sharing, or audit features are required for MVP.

## Scope of "client" tracking
Three Copilot-consuming clients must be supported:

1. **GitHub Copilot CLI** running on the owner's local Windows machine (`qichen2`).
2. **OpenClaw** running on Azure VM `openclaw-sg-vm` in `OPENCLAW-RG` (southeastasia) — a WeChat-to-Copilot gateway.
3. **Hermes** running on Azure VM `vm-hermes-agent-sg` in `RG-HERMES-AGENT-SG` (southeastasia) — a Copilot agent runtime.

All three issue Copilot LLM calls. The portal aggregates their token usage so the owner can see per-client distribution.

## Non-goals (explicit out of scope)
- Org/Enterprise Copilot Metrics API integration (owner has no admin role).
- Cost negotiation, billing reconciliation with GitHub invoices.
- Multi-user, sharing, RBAC tiers.
- Realtime streaming below 5-minute granularity (batched ingest is acceptable).
- Mobile-first UI (responsive desktop is sufficient).

## Quality attributes
- Cost: target a few USD/month total Azure spend.
- Privacy: portal is private, only the owner can read; ingested data should not leave Azure.
- Latency: dashboard pages must render within 2 seconds for 30 days of data.
- Resilience: collectors must handle network outages by buffering locally and replaying.

## Reference dimensions to visualize
The portal must support these analytical dimensions (drill-downs and breakdowns):
- **Time**: by hour, day, week, month
- **Client**: copilot-cli vs openclaw vs hermes (and per-host within each)
- **Model**: e.g. `claude-opus-4.6`, `gpt-5-mini`, `claude-sonnet-4.5`
- **Token kind**: prompt vs completion vs cached vs reasoning
- **Cost estimation**: derived using the GitHub published per-model AI Credit rates
