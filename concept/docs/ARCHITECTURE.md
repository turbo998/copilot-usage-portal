# Copilot Usage Portal — Architecture

## 1. Solution overview

A single-tenant, single-user Azure portal that aggregates **GitHub Copilot token consumption** from three local/edge clients (**Copilot CLI**, **OpenClaw**, **Hermes**) and visualises usage along multiple dimensions: client, model, host, day/week/month, cached-vs-uncached, estimated AI Credits cost, and top sessions.

The product is owned and used exclusively by one developer (a GitHub Copilot Pro/Pro+ subscriber). Because GitHub does not yet expose a per-user usage API for individual subscribers, the portal collects data by **parsing each client's local logs** with a small Python collector that emits unified events to an Azure ingest endpoint.

## 2. Topology

```
+-----------------------+   +---------------------------+   +------------------------------+
|  Local Windows host   |   |  openclaw-sg-vm (SEA)     |   |  vm-hermes-agent-sg (SEA)    |
|  Copilot CLI logs     |   |  OpenClaw trajectory.jsonl|   |  Hermes usage.jsonl (NEW)    |
|  ~/.copilot/logs/     |   |  ~/.openclaw/.../*.jsonl  |   |  ~/.hermes/usage.jsonl       |
|                       |   |                           |   |                              |
|  collector.py         |   |  collector.py             |   |  collector.py                |
|  (Task Scheduler 5m)  |   |  (systemd timer 5m)       |   |  (systemd timer 5m)          |
+-----------+-----------+   +-------------+-------------+   +---------------+--------------+
            |                             |                                 |
            |       HTTPS POST /ingest (Function key + X-Collector-Key)     |
            +--------------------------+--+---------------------------------+
                                       v
                          +----------------------------+
                          |  Azure Functions (Python)  |
                          |  - POST /ingest            |  Function key auth
                          |  - GET  /api/metrics/*     |  Easy Auth (Entra) auth
                          +-------------+--------------+
                                        |
                          +-------------v--------------+
                          |  Azure Cosmos DB (Free)    |
                          |  account: NoSQL, single    |
                          |  region (southeastasia)    |
                          |  db: copilot_usage         |
                          |  container: usage_events   |
                          |  partition key: /client    |
                          |  ttl: 365 days             |
                          +-------------+--------------+
                                        |
                          +-------------v--------------+
                          |  Azure Static Web Apps     |
                          |  (Standard plan)           |
                          |  React + Vite + TS         |
                          |  Tailwind + Recharts       |
                          |  Linked to Functions /api  |
                          |  Easy Auth (Entra ID)      |
                          +----------------------------+

                  + Azure Application Insights (Functions + SWA)
                  + Azure Key Vault (collector shared secret, optional)
```

All three clients sit outside Azure; only their collectors talk to Azure. The portal is single-user and lightweight — Cosmos DB Free Tier (1000 RU/s, 25 GB), Linux Consumption Functions, and SWA Standard fit comfortably.

## 3. Confirmed architectural decisions (DO NOT change)

| # | Decision | Value | Reason |
|---|----------|-------|--------|
| 1 | Region | **southeastasia** | Co-located with both source VMs to minimise egress + latency |
| 2 | IaC | **Bicep** | Terraform unavailable on owner's Windows ARM64 dev box |
| 3 | Frontend hosting | **Azure Static Web Apps (Standard)** | Native Easy Auth + linked Functions in one resource |
| 4 | Backend | **Azure Functions, Linux Consumption, Python 3.11** | Cheap, scale-to-zero, native Cosmos SDK |
| 5 | Storage | **Cosmos DB NoSQL, Free Tier**, single region | Free 1000 RU/s and 25 GB; serverless not needed |
| 6 | Frontend stack | React + Vite + TypeScript + Tailwind + Recharts | Lean, no SSR needed |
| 7 | Backend SDK | `azure-functions`, `azure-cosmos`, `azure-identity` | Official, tested |
| 8 | Auth (portal) | **Microsoft Entra ID via SWA Easy Auth** | Single-user, owner-only access |
| 9 | Auth (collectors → ingest) | Function-key + `X-Collector-Key` header | Simple, rotatable |
| 10 | Naming strategy | `microsoft-alz` zone `zd` (dev) | az prototype default that matches our scale |
| 11 | Identity | **System-assigned managed identity** on Functions, granted Cosmos DB Built-in Data Contributor | No keys in app settings |
| 12 | Telemetry | Application Insights, connected to both Functions and SWA | Single workspace |
| 13 | Deploy | `az prototype deploy` (azd-style under the hood) | Aligned with workflow |

## 4. Data model

### Cosmos DB container: `usage_events`

```json
{
  "id": "<event_id>",
  "client": "copilot-cli | openclaw | hermes",
  "host": "<machine_or_vm_name>",
  "ts": "2026-05-07T10:19:53Z",
  "date": "2026-05-07",
  "model": "claude-opus-4-6",
  "model_family": "claude | gpt | other",
  "prompt_tokens": 20378,
  "completion_tokens": 82,
  "cached_tokens": 19981,
  "reasoning_tokens": 0,
  "total_tokens": 20460,
  "session_id": "ba9b2f73-...",
  "estimated_credits": 0.034
}
```

- **Partition key**: `/client` (3 logical partitions, naturally balanced).
- **Unique key (per partition)**: `/id` — guarantees idempotent ingest.
- **Indexed paths**: `/date`, `/model`, `/host`, `/ts`, `/model_family`.
- **TTL**: 31 536 000 s (365 days). Owner can override via Cosmos Portal.

### Container: `cost_table` (small, ~30 docs)

Holds GitHub Copilot per-model AI-Credits-per-million-token rates. Looked up server-side at ingest time to compute `estimated_credits`. Format:

```json
{
  "id": "claude-opus-4-6",
  "model": "claude-opus-4-6",
  "credits_per_million_input": 7.5,
  "credits_per_million_output": 30.0,
  "credits_per_million_cached_input": 0.75,
  "source": "https://docs.github.com/en/copilot/concepts/billing/individual-plans",
  "captured_at": "2026-05-07"
}
```

## 5. APIs

All HTTP endpoints are served from one Linux Consumption Function App and consumed by the Static Web App via a linked-API mapping. The SWA layer enforces Easy Auth on every `/api/*` call before forwarding.

### `POST /ingest`
- **Auth**: Function key (`code=` query or header) **plus** `X-Collector-Key` header (validated against Function App setting `INGEST_KEY`).
- **Body**: `{ "events": [ <UsageEvent>, ... ] }`, max 100 per batch.
- **Behaviour**: Validates schema, enriches with `model_family` and `estimated_credits`, performs idempotent upsert into Cosmos.
- **Response**: `200 { "accepted": N, "deduped": M, "rejected": [...] }`.

### `GET /api/metrics/daily?from=YYYY-MM-DD&to=YYYY-MM-DD&client=&model=&host=`
- **Auth**: Easy Auth — Functions reads `x-ms-client-principal` header, requires non-empty principal.
- **Returns**: `[ { date, prompt, completion, cached, reasoning, total, credits } ]` ordered by `date` ascending.

### `GET /api/metrics/weekly?from=&to=&client=&model=`
ISO-week buckets, same record shape as daily plus `iso_year` + `iso_week`.

### `GET /api/metrics/breakdown?dim={client|model|host|model_family}&from=&to=`
Returns `[ { key, total, credits, share } ]` sorted by `total` desc.

### `GET /api/metrics/heatmap?from=&to=`
Two-dimensional aggregation over `client × model`:
```json
{
  "rows": ["copilot-cli", "openclaw", "hermes"],
  "cols": ["claude-opus-4-6", "claude-sonnet-4-5", "gpt-5-mini"],
  "matrix": [[12345, 0, 678], ...]
}
```

### `GET /api/metrics/top-sessions?n=10&from=&to=&client=`
Returns top-N sessions by total tokens, projecting `session_id, client, model, total_tokens, credits, first_ts, last_ts, calls`.

### `GET /api/metrics/cost?from=&to=`
Returns `{ daily: [...], total_credits, projected_month_credits, breakdown_by_model: [...] }`.

## 6. Front-end pages

1. **Overview** — KPI cards: today / WTD / MTD / projected month-end credits. 30-day stacked area (input vs cached vs completion vs reasoning).
2. **By Client** — donut + 30-day stacked line per client; toggle absolute / share.
3. **By Model** — donut + per-model 30-day line + sortable model table (calls, tokens, credits).
4. **Heatmap** — Client × Model intensity grid; click a cell drills into Sessions filtered.
5. **Sessions** — sortable / filterable table of top sessions; detail drawer shows event timeline.
6. **Cost** — daily credits column chart + breakdown-by-model bar + month-end projection (linear regression on last 14 days).

## 7. Collector (data-plane component, runs OFF-Azure)

`collector.py` is a single-file Python script with three pluggable adapters selected at startup via `--client`. Common features:
- Reads the appropriate log directory, maintains a per-source byte-offset / line-number cursor in `~/.copilot-usage-collector/cursor.json`.
- Emits unified `UsageEvent` records that match the Cosmos schema.
- POSTs in batches of up to 100 events to `/ingest` with both auth headers.
- On HTTP 4xx/5xx, persists the batch to a local `.spool/` directory and retries on the next tick.

Adapter behaviour:
- `--client copilot-cli` (Windows) — scans `%USERPROFILE%\.copilot\logs\process-*.log` for `[DEBUG] data:` JSON blocks, extracts `usage` + `model` + `id` + leading timestamp.
- `--client openclaw` (Linux VM) — tails every `~/.openclaw/agents/main/sessions/*.trajectory.jsonl`, filters `type == "model.completed"`, maps `data.usage.{input,output,cacheRead,total}` to unified field names, dedup key `sessionId + ":" + seq`.
- `--client hermes` (Linux VM) — tails `~/.hermes/usage.jsonl` written by an in-source instrumentation hook added to the `hermes-agent` repo on the VM. The hook wraps the Copilot client response handler and writes one JSON line per call.

Scheduling:
- Windows: Task Scheduler trigger — every 5 minutes.
- Linux: systemd `copilot-usage-collector.timer` — every 5 minutes.

## 8. Security & access

- The portal SWA enforces **Microsoft Entra ID Easy Auth** in `easyauth_v2` mode; only the owner's UPN is allowed (allow-list).
- `/api/metrics/*` Functions reject requests without a valid `x-ms-client-principal` (defence in depth).
- `/ingest` Function uses anonymous auth at the Functions level (so collectors don't need the SWA principal) but requires both:
  - Function key (`code=`) — rotated on a schedule.
  - `X-Collector-Key` matching `INGEST_KEY` app setting.
- Functions has a system-assigned managed identity with `Cosmos DB Built-in Data Contributor` role-assignment scoped to the Cosmos account. **No connection strings in app settings.**
- Application Insights ingestion-key is supplied via Function-app setting and SWA env var.
- `INGEST_KEY` is generated as a 256-bit random value by the deploy script and stored only in App Settings (Key Vault optional follow-up).

## 9. Observability

- Application Insights captures Function logs, traces, and exceptions for `/ingest` and `/api/metrics/*`.
- Functions emit a custom event `usage.ingest.batch` with dimensions `accepted`, `deduped`, `rejected_count`, `client`.
- A single dashboard alert: HTTP 5xx rate on `/ingest` > 5% over 15 minutes.

## 10. Deployment plan (consumed by the build stage)

Stages (each gets its own folder under `concept/`; build stage will scaffold them):

1. **Stage `core-storage`** — Cosmos DB account + database + `usage_events` and `cost_table` containers.
2. **Stage `core-monitoring`** — Application Insights + Log Analytics workspace.
3. **Stage `core-functions-host`** — Storage account (Functions backend) + Linux Consumption plan + Function App with system-assigned managed identity + RBAC role assignment to Cosmos.
4. **Stage `apps-functions-code`** — Python Functions code: `ingest_function`, `metrics_daily`, `metrics_weekly`, `metrics_breakdown`, `metrics_heatmap`, `metrics_top_sessions`, `metrics_cost`. Plus shared `cosmos_repo.py`, `cost_table.py`, `auth.py`.
5. **Stage `apps-webportal`** — Static Web App + linked Function App. React/Vite project under `apps/web` with Recharts dashboards.
6. **Stage `db-cost-table-seed`** — JSON file seeded into `cost_table` container as part of post-deploy hook (one-shot script).
7. **Stage `apps-collectors`** — `collector.py` + per-OS deployment scripts (Windows Task Scheduler XML, Linux systemd unit + timer).
8. **Stage `docs`** — `concept/docs/architecture.md` + `deployment-guide.md`.

## 11. Out of scope (explicit non-goals)

- Multi-user / multi-tenant access.
- Fine-grained per-prompt content storage (only token counts, never message bodies).
- Slack / WeChat notifications (deferred to post-MVP).
- Auto-rotating ingest keys (manual rotation only for MVP).
