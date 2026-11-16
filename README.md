# AgentCo

Chat-first PWA where AI agents run operations for **AdSpot Media** and **Sidadiya**, and you approve only the key actions.
Phase 1 (this repo state): core platform, agent loop, audit, budgets, tool tiers, hard company isolation, PWA with chat + approvals, Command agent, read-only daily reports. Mail, payments, publishing, Postiz and engineering agents come in later phases (see `docs/`).

Read `docs/systems-map.md` first: it records what your two codebases actually contain.

## Architecture
```
PWA (React/Vite)  ->  FastAPI (api)  ->  Postgres (agentco)  <-  worker  <-  scheduler (cron)
                                              |
            read-only SELECT roles ->  AdSpot Postgres, Sidadiya Postgres   (connectors/)
```
* `agentco/scope.py` + RLS: every query is confined to one company (`SET LOCAL ROLE agentco_app` + `app.company_id`).
* `agentco/tools/registry.py`: only path to side effects. Department allowlists, company scopes, tiers (READ, DRAFT, WRITE_LOW, HIGH, OWNER_COMMAND). HIGH tools can only create an approval.
* `agentco/llm/router.py`: `llm.complete(task, data_class, ...)`. INTERNAL/CONFIDENTIAL -> paid Gemini only.
* `agentco/agents/`: budget guard, agent loop, runner (daily reports), Command agent (LLM emits a validated intent, code executes it).
* `agentco/orchestrator/queue.py`: Postgres job queue (`FOR UPDATE SKIP LOCKED`).

## Run locally
```bash
uv sync
(cd web && npm install && npm run build)
cp .env.example .env            # fill in values; use LLM_PROVIDER=fake for offline
uv run python -m agentco.db     # migrations
uv run python -m agentco.seed   # companies + agents
uv run uvicorn agentco.api.main:app --port 8000
uv run python -m agentco.worker      # in another terminal
uv run python -m agentco.scheduler   # enqueue due daily reports (Railway runs it every 5 min)
```
Tests need a local Postgres (`pg_ctlcluster 16 main start`) and databases `agentco_test`, `fake_adspot`, `fake_sidadiya` owned by role `agentco`:
```bash
uv run pytest
```
`scripts/dev_server.py` is a DEV-ONLY server with the login bypassed and fake data. Never deploy it.

## Deploy on Railway
1. New project -> add **Postgres** plugin (this is AgentCo's own DB).
2. Three services from this repo (Dockerfile build): `api` (default start command, healthcheck `/api/health`), `worker` (`python -m agentco.worker`), `scheduler` (`python -m agentco.scheduler`, cron `*/5 * * * *`). Share the env vars from `.env.example`.
3. Mount a volume at `UPLOAD_DIR` for chat uploads.
4. **Read-only roles** (run on each *live app's* Postgres, then put the URLs in `ADSPOT_RO_DATABASE_URL` / `SIDADIYA_RO_DATABASE_URL`):
```sql
CREATE ROLE agentco_ro LOGIN PASSWORD '<strong password>';
GRANT CONNECT ON DATABASE railway TO agentco_ro;
GRANT USAGE ON SCHEMA public TO agentco_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO agentco_ro;
ALTER ROLE agentco_ro SET default_transaction_read_only = on;
```
   Better still: grant SELECT only on the tables listed in `docs/systems-map.md` §2 and §3.
5. Google sign-in: create an OAuth Web client, add your Railway URL as an authorised JavaScript origin, set `GOOGLE_CLIENT_ID` and `OWNER_EMAIL`. First login shows a TOTP secret to add to your authenticator app.
6. Push: generate VAPID keys and set the three `VAPID_*` vars.

## Posts and ads (manual publish, no Facebook connection)
The **Posts** tab (or chat: "make a post for AdSpot about booking classified ads online") makes the Marketing agent write a ready-to-post package: copy for Facebook, Instagram, X and LinkedIn, hashtags, an image brief with an exact prompt, and recommended sizes. Choose **Ad** to also get a setup sheet (objective, audience, placements, headline, text, call to action) to type into Ads Manager yourself.
* Copy buttons for every block. Image: either make it in Google Flow from the prompt and upload it, or pick "Gemini (auto)" (needs `GEMINI_IMAGE_MODEL`).
* A second model pass checks the draft against your **brand facts** and flags unsupported claims ("needs review"). Edit the facts at the bottom of the Posts tab. Seeded facts are generic: no prices or discounts are used unless you add them.
* Customer data can never enter public content: emails, phone numbers and booking references are blocked in topics, facts and prompts, and flagged if a model leaks one.
* AgentCo never posts anywhere and never touches Meta. You post by hand, then tap "I posted it".

## Phases
1. Core + chat + approvals + daily reports (done).
1b. Manual-publish Posts and ad sheets (done). Postiz and Meta API are dropped.
2. Gmail, Customer Desk, payment flow, publication draft, receipt flow, booking state machine, payout batch (prepare only).
3. Sidadiya slip checker, optional OmniRoute for PUBLIC copy with cost comparison.
4. Engineering agent (PRs), weekly Insights report.
