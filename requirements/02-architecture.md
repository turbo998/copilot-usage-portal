# 02 — Architecture (Confirmed)

This document captures the architectural decisions already made. The cloud-architect agent should use these as **fixed constraints**, not options to revisit.

## Topology

```
[Local Windows]              [openclaw-sg-vm]               [vm-hermes-agent-sg]
 Copilot CLI logs              OpenClaw trajectory jsonl      Hermes usage.jsonl (NEW)
       |                              |                              |
       v                              v                              v
 collector.py                  collector.py                    collector.py
 (Task Scheduler 5min)         (systemd timer 5min)           (systemd timer 5min)
       \____________ HTTPS POST batch (X-Collector-Key header) ___________/
                                       |
                                       v
                          +-----------------------------+
                          | Azure Functions (Python)    |
                          | /ingest                     |  Function key
                          | /api/metrics/{daily,weekly, |  Entra ID Easy Auth
                          |   breakdown,heatmap,top}    |
                          +--------------+--------------+
                                         |
                          +--------------v--------------+
                          | Cosmos DB (Free Tier, NoSQL)|
                          | container: usage_events     |
                          | partition key: /client      |
                          | TTL: 365 days               |
                          +--------------+--------------+
                                         |
                          +--------------v--------------+
                          | Azure Static Web App (Std)  |
                          | React + Vite + TS + Tailwind|
                          | Recharts                    |
                          | Linked to Functions /api    |
                          | Easy Auth (Entra ID, owner) |
                          +-----------------------------+

 + Application Insights (Functions + SWA)
```

## Confirmed decisions

| # | Decision | Value |
|---|---|---|
| 1 | Region | `southeastasia` (co-located with both VMs) |
| 2 | IaC | **Bicep** (Terraform unavailable on owner's Windows ARM64 machine) |
| 3 | Frontend hosting | **Azure Static Web Apps** (Standard plan for Easy Auth) |
| 4 | Backend | **Azure Functions** (Linux Consumption plan, Python 3.11) |
| 5 | Storage | **Cosmos DB** (NoSQL API, Free Tier, single region) |
| 6 | Frontend stack | React + Vite + TypeScript + Tailwind CSS + Recharts |
| 7 | Backend SDK | `azure-functions`, `azure-cosmos`, `azure-identity` |
| 8 | Authentication (portal) | Microsoft Entra ID via SWA Easy Auth (single user) |
| 9 | Authentication (collector → ingest) | Function Key + shared `X-Collector-Key` header |
| 10 | Naming strategy | `microsoft-alz`, zone `zd` |
| 11 | Identity | System-assigned managed identity on Functions, with Cosmos DB Data Contributor role |
| 12 | Telemetry | Application Insights connected to both Functions and SWA |
| 13 | Deploy method | `az prototype deploy` (azd-style under the hood) |

## Data model (Cosmos DB container `usage_events`)

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
  "cache_write_tokens": 0,
  "reasoning_tokens": 0,
  "total_tokens": 20460,
  "session_id": "ba9b2f73-...",
  "estimated_credits": 0.034
}
```

- **Partition key**: `/client`
- **Unique key**: `/id` (per partition) → idempotent ingest
- **Indexed paths**: `/date`, `/model`, `/host`, `/ts`
- **TTL**: 31536000 seconds (365 days)

## API contract

### `POST /ingest`
- Auth: Function Key + `X-Collector-Key`
- Body: `{ "events": [ <UsageEvent>, ... ] }` (max 100 per batch)
- Response: `{ "accepted": N, "deduped": M }`

### `GET /api/metrics/daily?from=&to=&client=&model=`
- Auth: Easy Auth (`x-ms-client-principal` required)
- Returns: array of `{ date, prompt, completion, cached, reasoning, total, credits }`

### `GET /api/metrics/weekly?from=&to=&...`
Same shape, weekly buckets (ISO week).

### `GET /api/metrics/breakdown?dim={client|model|host|model_family}&from=&to=`
Returns: array of `{ key, total, credits, share }`

### `GET /api/metrics/heatmap?from=&to=`
Matrix `client × model` with totals.

### `GET /api/metrics/top-sessions?n=10&from=&to=`
Top N sessions by total tokens.

## Frontend pages
1. **Overview** — KPI cards (today / WTD / MTD / quota burn) + 30-day stacked area
2. **By Client** — donut + 30-day stacked line per client
3. **By Model** — donut + per-model time series + table
4. **Heatmap** — Client × Model intensity grid
5. **Sessions** — table of top sessions, filter by date/client/model
6. **Cost** — estimated AI Credits + projected month-end usage