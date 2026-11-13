# Deploy guide (M9.3 / WP8)

## VPS deployment

1. Provision any small host with Docker + Compose v2, clone the repo, put secrets in `.env`
   (never commit it):
   ```
   GROQ_API_KEY=gsk_...
   API_BEARER_TOKEN=<long-random>        # enables auth on POST endpoints
   CADDY_DOMAIN=qo.example.com           # Caddy obtains TLS automatically
   GRAFANA_PUBLIC_URL=https://grafana.example.com
   SPEND_CAP_USD_PER_DAY=10
   ```
2. `make up && make migrate` — api, worker ×2, mcp, postgres, prometheus, grafana, caddy.
3. Caddy terminates TLS for the API on your domain (`Caddyfile`: `{$CADDY_DOMAIN} -> api:8000`).
4. Smoke: `BASE_URL=https://qo.example.com API_BEARER_TOKEN=<token> ./scripts/smoke_deploy.sh`
   (hits `/healthz`, creates a session, makes a proxy call, checks `/stats`).

## Web UI

Served by the API itself at `/ui` — no build step, no CDN, no external fonts.
`https://qo.example.com/ui` shows the Overview dashboard (KPIs, ledger charts, recent
requests), the Specs viewer, a New-spec form with live SSE progress, and Connect snippets.

## Grafana embedding

Set in `.env` before `make up`:
```
GF_SECURITY_ALLOW_EMBEDDING=true
GRAFANA_PUBLIC_URL=https://grafana.example.com
```
The dashboard is provisioned automatically from `observability/grafana/`. The UI top bar
links to it; to embed panels in the UI instead, use Grafana's render URLs with
`&kiosk` mode against `GRAFANA_PUBLIC_URL`.

## Rate limiting / spend cap

`RATE_LIMIT_PER_MIN` (default 30/min per IP on `POST /sessions`) and
`SPEND_CAP_USD_PER_DAY` (rejects new sessions with 402 once exceeded) are env-driven
and tested.

## Demo traffic (never measured)

`UI_DEMO=1 python -m scripts.seed_demo_traffic` seeds clearly-flagged rows into the
separate `proxy_demo_requests` table. The UI shows a "Demo data" banner when serving
them; `/stats`, REPORT.md and the measured-benchmark card exclude them by construction.
