# Copilot Usage Portal

A private Azure dashboard that tracks GitHub Copilot token usage across three clients
— **Copilot CLI**, **OpenClaw**, **Hermes** — and shows daily/weekly trends, model and
client breakdowns, a client × model heatmap, top sessions, and an AI Credits cost
estimate.

> Built on top of [`Azure/az-prototype`](https://github.com/Azure/az-prototype). The
> design phase produced [`concept/docs/ARCHITECTURE.md`](concept/docs/ARCHITECTURE.md);
> the build phase produced everything in `infra/`, `api/`, `web/`, and `collector/`.

## Architecture (one-liner)

```
collectors (CLI / OpenClaw / Hermes)  ──HTTPS──▶  Azure Functions /ingest
                                                          │ (VNet integrated)
                                                          ▼
                                              Cosmos DB (via Private Endpoint)
                                                          ▲
                                                          │
                            React on Static Web Apps  ──/api──┘  (Easy Auth via Entra)
```

**Networking (since 2026-05-12):** Function App is regional-VNet-integrated into
`vnet-copilotusage-dev-mdu5wa` (10.30.0.0/16). All outbound calls to Storage and
Cosmos go through Private Endpoints in `snet-pe` (10.30.2.0/27). Storage has
`networkAcls.defaultAction=Deny`, Cosmos has `networkAclBypass=None` — so even
if MCAPS policy flips `publicNetworkAccess` to `Disabled` on either, the runtime
is unaffected. Function App ingress remains public so SWA can call its backend.
See `concept/docs/ARCHITECTURE.md` for the full design.

## Repository layout

| Path | Purpose |
|------|---------|
| `concept/docs/ARCHITECTURE.md` | Design-phase output. Source of truth for decisions. |
| `infra/main.bicep` + `main.parameters.json` | Cosmos + Functions + SWA + AppInsights + Log Analytics + RBAC. |
| `infra/cost_table_seed.json` | Per-model AI Credits rate table (12 rows). |
| `api/function_app.py` | Functions Python v2: `/ingest` + `/api/metrics/*`. |
| `api/shared/` | Cosmos repo, cost-table, auth, time helpers. |
| `web/` | React + Vite + TypeScript + Tailwind + Recharts (6 pages). |
| `collector/collector.py` | Single-file collector with adapters for the 3 clients. |
| `collector/adapters/copilot_cli.py` | **Smoke-tested** on real local data: 3500 events / 12 models. |
| `collector/adapters/openclaw.py` | Tails `~/.openclaw/agents/main/sessions/*.trajectory.jsonl`. |
| `collector/adapters/hermes.py` | Tails `~/.hermes/usage.jsonl` — requires the source-side hook below. |
| `requirements/04-hermes-hook.md` | Patch instructions for adding a usage hook to `hermes-agent`. |
| `scripts/deploy.ps1` | One-shot deploy: rg create → bicep → func publish → swa upload → cost-table seed. |
| `scripts/install-windows-task.ps1` | Register the local CLI collector as a 5-minute Scheduled Task. |
| `scripts/install-linux-systemd.sh` | Install collector as a systemd timer on the OpenClaw / Hermes VMs. |
| `scripts/seed_cost_table.py` | Idempotent seeder for the `cost_table` Cosmos container. |
| `tests/test_normalise.py` | Unit tests for `cost_table.normalise_model_id` (17 cases pass). |

## Quick start (local dev)

```pwsh
# 1. Web
cd web
npm install
npm run build         # validates, ~635 kB JS gzipped 179 kB

# 2. API (needs azure-functions-core-tools v4 + python 3.11)
cd ..\api
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# Copy local.settings.json.template -> local.settings.json and fill in values
func start

# 3. Front-end + API together
cd ..\web
npm run dev           # http://localhost:5173, /api proxied to :7071

# 4. Validate the collector against your real CLI logs (no network calls)
cd ..
python collector\smoke_test_cli.py
```

## Deploy to Azure

Pre-reqs: `az` (logged in), `python ≥ 3.11`, `node ≥ 18`, `func` (Core Tools v4),
`@azure/static-web-apps-cli` (the script will `npm i -g` if missing).

```pwsh
.\scripts\deploy.ps1 `
    -ResourceGroup rg-copilot-usage-portal `
    -Location southeastasia `
    -SwaLocation eastasia `
    -TenantId '<your Entra tenant id>'
```

The script:
1. Creates the resource group (idempotent).
2. Generates a 64-hex `INGEST_KEY` if `infra/main.parameters.json` still has the placeholder.
3. Runs the Bicep deployment (incl. VNet, subnets, private DNS zones, 3 Private Endpoints).
4. **Temporarily** adds the deployer's public IP to the Storage firewall, publishes Functions
   code (`func azure functionapp publish`), then removes the IP rule (try/finally — even on
   failure).
5. Builds `web/` and uploads `dist/` to Static Web Apps.
6. Seeds the cost table by POSTing to the Function App's `/api/metrics/seed-cost-table`
   endpoint (Function App writes through the Cosmos Private Endpoint using its managed
   identity). No direct Cosmos access from the deployer machine is needed.

When it's done it prints the portal URL, the ingest URL, and the X-Collector-Key.

### MCAPS / public-network-access drift

On MCAPS subscriptions, an external policy periodically resets the Storage
account's `publicNetworkAccess` to `Disabled`. Because the Function App reaches
Storage and Cosmos through Private Endpoints, **this no longer breaks the
runtime** — only deploys are affected. If a deploy fails at step 4, re-run the
script; it flips `publicNetworkAccess` back to `Enabled` and adds the deployer
IP rule before calling `func publish`.

## Wire up collectors

### Local Windows machine (Copilot CLI)

```pwsh
.\scripts\install-windows-task.ps1 `
    -IngestUrl 'https://func-xxx.azurewebsites.net/ingest' `
    -IngestKey 'paste-from-deploy-output' `
    -FunctionKey (az functionapp keys list -g rg-copilot-usage-portal -n func-xxx --query 'masterKey' -o tsv)
```

### OpenClaw VM

```bash
INGEST_URL='https://func-xxx.azurewebsites.net/ingest' \
INGEST_KEY='...' FUNCTION_KEY='...' CLIENT_ID=openclaw \
sudo -E bash scripts/install-linux-systemd.sh
```

### Hermes VM

1. Apply the source-side hook from `requirements/04-hermes-hook.md` to your
   `hermes-agent` checkout (≈ 8 lines of code), restart the agent.
2. Run the same systemd installer with `CLIENT_ID=hermes`.

## Validation

The smoke test below was run against this machine's existing Copilot CLI logs (no
network calls, no deploy required) and produced realistic numbers:

```
parsed 3500 events from C:\Users\qichen2\.copilot\logs
totals: prompt=340,310,884  completion=2,135,613  cached=304,591,024
est credits: total=558.52  cache_hit=89.5%
top model:   claude-opus-4-6-1m  →  381 credits (68% of spend)
```

End-to-end check after deploy:

```pwsh
# 1. Trigger one Copilot CLI call.
copilot --help
# 2. Force the collector to run now.
Start-ScheduledTask -TaskName CopilotUsageCollector
# 3. Check the API saw it.
curl 'https://func-xxx.azurewebsites.net/api/metrics/daily?from=2026-05-01&to=2026-05-31' \
     -H "Cookie: <SWA easy-auth session>"
# 4. Open the portal and confirm Today's bar grew.
```

## Cost (Azure)

| Resource | SKU | Approx monthly cost (BASIC self-use) |
|----------|-----|--------------------------------------|
| Cosmos DB | NoSQL Serverless | < $1 (self-only RU usage) |
| Functions | Flex Consumption (FC1) | < $1 |
| Static Web App | Standard | $9 |
| App Insights + Log Analytics | Pay-as-you-go | < $1 (self-only traffic) |
| Private Endpoints | 3 × Standard | ≈ $21 (3 × $7.20) |
| VNet / Private DNS zones | — | $0 |
| **Total** | | **≈ $32/mo** |

## Caveats

- The **AI Credits** rate table in `infra/cost_table_seed.json` is a **best-effort
  estimate** based on the public GitHub Copilot Pro/Pro+ documentation and Anthropic
  pricing page. Verify before relying on the dollar figures.
- Hermes does not record token usage natively; the in-source hook is required to feed
  the portal.
- The internal Microsoft model id `capi-eus-ptuc-gb300-oswe-vscode-large` (used for
  inline completions) is intentionally unmapped — it represents trivial tokens but
  has no public rate.
- `az prototype` v0.2.1b7 cannot run its TUI in non-Win32-console environments (e.g.
  agent PTY). This project's design output was therefore hand-authored as
  `concept/docs/ARCHITECTURE.md` and the build artefacts were generated directly.
