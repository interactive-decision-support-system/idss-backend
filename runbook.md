# IDSS Deployment Runbook

How to deploy, configure, and operate the IDSS backend (Railway) and frontend (Vercel).

---

## Services at a glance

| Service | Platform | What it does |
|---------|----------|--------------|
| Backend API | Railway | FastAPI, `/chat`, `/ucp/*`, `/channels/*` |
| Frontend | Vercel | Next.js chat UI |
| Database | Supabase | PostgreSQL — 28k+ products |
| Cache | Upstash | Redis — session state, search cache |
| Neo4j | Docker (local only) | Knowledge graph — optional |

---

## 1. Backend — Railway

### First-time deploy

1. Go to <https://railway.app> → New Project → Deploy from GitHub repo → select `idss-backend`
2. Railway auto-detects `railway.toml` and uses the `Dockerfile`
3. Set environment variables (Railway dashboard → your service → Variables):

```
OPENAI_API_KEY        = sk-...
DATABASE_URL          = postgresql://...   # Supabase direct connection string
SUPABASE_URL          = https://xxx.supabase.co
SUPABASE_KEY          = ...
UPSTASH_REDIS_URL     = rediss://default:PASSWORD@endpoint.upstash.io:6379
LOG_LEVEL             = INFO
```

4. Click **Deploy** — Railway builds the Docker image and starts the container
5. Your public URL appears in the dashboard (e.g. `https://idss-backend-production.up.railway.app`)

### Re-deploying after code changes

```bash
git push origin main
# Railway auto-deploys on push to main if GitHub integration is connected.
# Otherwise: Railway dashboard → your service → Deploy → Redeploy
```

### Checking logs

Railway dashboard → your service → Deployments → click a deployment → Logs tab.

Or stream live:

```bash
railway logs --tail
# (requires `npm install -g @railway/cli` and `railway login`)
```

### Environment variables that must be set on Railway

| Variable | Where to get it |
|----------|----------------|
| `OPENAI_API_KEY` | <https://platform.openai.com/api-keys> |
| `DATABASE_URL` | Supabase → Project Settings → Database → Connection string → URI |
| `SUPABASE_URL` | Supabase → Project Settings → API → Project URL |
| `SUPABASE_KEY` | Supabase → Project Settings → API → `service_role` key (server-side) |
| `UPSTASH_REDIS_URL` | Upstash console → Redis → your DB → Details → REST URL (use `rediss://` format) |

---

## 2. Frontend — Vercel

### First-time deploy

1. Go to <https://vercel.com> → Add New → Project → Import from GitHub → select `idss-web`
2. Framework preset: **Next.js** (auto-detected)
3. Set environment variables (Vercel → your project → Settings → Environment Variables):

```
NEXT_PUBLIC_SUPABASE_URL      = https://xxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY = ...           # anon key (public-safe)
NEXT_PUBLIC_API_BASE_URL      = https://your-railway-backend-url.up.railway.app
NEXT_PUBLIC_MCP_BASE_URL      = https://your-railway-backend-url.up.railway.app
```

4. Click **Deploy**

### Re-deploying after code changes

```bash
git push origin main
# Vercel auto-deploys on push to main.
```

### Checking Vercel deployment

Vercel dashboard → your project → Deployments → click a deployment → Functions / Build logs.

### Pointing frontend to a new backend URL

If you re-deploy the backend and the Railway URL changes:

1. Vercel → your project → Settings → Environment Variables
2. Update `NEXT_PUBLIC_API_BASE_URL` and `NEXT_PUBLIC_MCP_BASE_URL`
3. Trigger a redeploy (Vercel → Deployments → Redeploy latest)

---

## 3. Upstash Redis — activation (currently pending)

**Status:** Code is complete and tested. Account setup not yet done.

Steps:

1. Go to <https://console.upstash.com> → Create Database → Redis
2. Region: pick closest to your Railway region (e.g. `us-east-1`)
3. Copy the **Redis URL** from the database details page
   - Format: `rediss://default:PASSWORD@endpoint.upstash.io:6379`
   - Make sure it starts with `rediss://` (TLS), not `redis://`
4. Add to Railway environment variables:
   ```
   UPSTASH_REDIS_URL = rediss://default:PASSWORD@endpoint.upstash.io:6379
   ```
5. Redeploy the backend
6. Confirm in logs: look for a line containing `"Upstash"` or `"cache_init"`

**What it unlocks:**

- Session persistence across backend restarts (currently in-memory only, lost on redeploy)
- Search result caching (5 min TTL) — reduces Supabase query load
- Price/inventory caching (60s / 30s TTL)

**No code changes needed** — `mcp-server/app/cache.py` already handles the `UPSTASH_REDIS_URL` env var automatically.

---

## 4. Neo4j Knowledge Graph — local only (cloud blocked)

**Status:** Works locally via Docker. Cloud (Aura) DNS is failing — not yet fixed.

### Run locally

```bash
# From idss-backend repo root
docker compose -f docker-compose-neo4j.yml up -d

# Neo4j browser UI: http://localhost:7475
# Bolt connection: bolt://localhost:7688
# Default login: neo4j / neo4jpassword
```

Add to `.env` for local dev:

```
NEO4J_URI=bolt://localhost:7688
NEO4J_USER=neo4j
NEO4J_PASSWORD=neo4jpassword
```

Populate the graph from existing Supabase products:

```bash
source .venv/bin/activate
PYTHONPATH=mcp-server python scripts/update_redis_and_kg.py
```

### Fix cloud Neo4j (Aura) — blocked issue

The cloud Neo4j Aura instance DNS is failing. To fix:

1. Go to <https://console.neo4j.io> → your instance → Connection details
2. Copy the **Bolt URI** (format: `neo4j+s://xxxxxxxx.databases.neo4j.io`)
3. Copy the **Username** and **Password** from the instance details
4. Set on Railway:
   ```
   NEO4J_URI=neo4j+s://xxxxxxxx.databases.neo4j.io
   NEO4J_USER=neo4j
   NEO4J_PASSWORD=...
   ```
5. Run `update_redis_and_kg.py` once to populate the graph

**Note:** The system works fully without Neo4j — it falls back to Supabase SQL search. Neo4j is used for graph-based "you might also like" style queries which are not in the current critical path.

---

## 5. Supabase database

No deployment steps needed — Supabase is always-on cloud PostgreSQL.

If you need to re-run the full product import:

```bash
source .venv/bin/activate
PYTHONPATH=mcp-server python mcp-server/scripts/merge_supabase_data.py
```

The script is idempotent (skips existing products).

---

## 6. Slack channel integration

**Status:** Code complete and tested (18 unit tests pass). Slack app not yet created.

Steps to activate:

1. Go to <https://api.slack.com/apps> → Create New App → From scratch
2. Give it a name (e.g. "IDSS Shopping Assistant") and pick your workspace
3. Under **OAuth & Permissions** → add Bot Token Scopes:
   - `chat:write`
   - `channels:history`
   - `im:history`
   - `im:write`
4. Install the app to your workspace → copy the **Bot User OAuth Token** (`xoxb-...`)
5. Under **Basic Information** → copy the **Signing Secret**
6. Under **Event Subscriptions** → enable → set Request URL:
   ```
   https://your-railway-backend-url.up.railway.app/channels/slack/events
   ```
   Slack will send a challenge request — the backend handles this automatically
7. Subscribe to bot events: `message.channels`, `message.im`
8. Add to Railway environment variables:
   ```
   SLACK_BOT_TOKEN     = xoxb-...
   SLACK_SIGNING_SECRET = ...
   ```
9. Redeploy backend

Once live, invite the bot to a channel (`/invite @IDSS`) and send it a message.

---

## 7. Health checks

```bash
# Backend
curl https://your-backend.up.railway.app/health

# Chat smoke test
curl -X POST https://your-backend.up.railway.app/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "I need a laptop for college"}'

# Frontend
curl -I https://idss-web.vercel.app
```

---

## 8. Common deployment issues

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| 502 Bad Gateway on Railway | uvicorn crashed at startup | Check Railway logs for Python import errors |
| `ModuleNotFoundError: agent` | Wrong working directory in start command | Ensure `railway.toml` has `startCommand = "uvicorn app.main:app --app-dir /app/mcp-server ..."` |
| Frontend API calls fail (CORS) | `NEXT_PUBLIC_API_BASE_URL` wrong | Update Vercel env var to current Railway URL |
| Sessions lost on redeploy | No Upstash configured | Set `UPSTASH_REDIS_URL` (section 3 above) |
| Slack events not received | Request URL not verified | Check Railway logs for the `url_verification` challenge response |
| Neo4j connection refused | Wrong port or URI scheme | Use `bolt://` for local Docker, `neo4j+s://` for Aura cloud |
