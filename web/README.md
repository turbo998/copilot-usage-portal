# Web (Frontend)

React + Vite + TypeScript + Tailwind + Recharts dashboard for the Copilot Usage Portal.

## Local dev

```pwsh
cd web
npm install
npm run dev
# http://localhost:5173
```

API requests under `/api/*` are proxied to `http://localhost:7071` (Functions Core Tools default).

## Build

```pwsh
npm run build
# dist/ is what gets uploaded to Static Web Apps
```

## Pages

| Route | File | Purpose |
|-------|------|---------|
| `/` | `pages/Overview.tsx` | KPI cards + daily area + client share + top models + credits |
| `/by-client` | `pages/ByClient.tsx` | Per-client share & stacked daily bars |
| `/by-model` | `pages/ByModel.tsx` | Family share + top model bars + table |
| `/heatmap` | `pages/Heatmap.tsx` | Client × Model matrix (tokens/credits/calls) |
| `/sessions` | `pages/Sessions.tsx` | Top-N sessions table |
| `/cost` | `pages/Cost.tsx` | AI Credits estimate + plan quota |

## Auth

In production, Static Web Apps Easy Auth (`staticwebapp.config.json`) requires AAD sign-in for all routes.
Locally, requests bypass auth (Functions runs anonymous unless wired up).

## Edit `staticwebapp.config.json`

Replace `__TENANT_ID__` with your Entra ID tenant id during deploy. The script `scripts/deploy.ps1`
performs this substitution automatically before uploading.
