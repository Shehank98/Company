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
# the worker also runs the scheduler every minute (idempotent); `python -m agentco.scheduler` runs one tick by hand
```
Tests need a local Postgres (`pg_ctlcluster 16 main start`) and databases `agentco_test`, `fake_adspot`, `fake_sidadiya` owned by role `agentco`:
```bash
uv run pytest
```
`scripts/dev_server.py` is a DEV-ONLY server with the login bypassed and fake data. Never deploy it.

## Deploy on Railway
1. New project -> add **Postgres** plugin (this is AgentCo's own DB).
2. Two services from this repo (Dockerfile build): `api` (default start command, healthcheck `/api/health`) and `worker` (`python -m agentco.worker`; it runs jobs, sends due mail and drives the scheduler, so no cron service is needed). Share the env vars: full list with where each value comes from in `docs/env-variables.md`.
3. Mount a volume at `UPLOAD_DIR` for chat uploads.
4. **Read-only roles** (run on each *live app's* Postgres, then put the URLs in `ADSPOT_RO_DATABASE_URL` / `SIDADIYA_RO_DATABASE_URL`):
```sql
CREATE ROLE agentco_ro LOGIN PASSWORD '<strong password>';
GRANT CONNECT ON DATABASE railway TO agentco_ro;
GRANT USAGE ON SCHEMA public TO agentco_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO agentco_ro;   -- Phase 2 also reads bookings, newspapers, publication_groups
ALTER ROLE agentco_ro SET default_transaction_read_only = on;
```
   Better still: grant SELECT only on the tables listed in `docs/systems-map.md` §2 and §3.
5. **AdSpot write role (optional, Phase 2).** Lets AgentCo set `payment_status`/`status` on a booking when you confirm a payment, and `sent_to_publication_at` when you send the publication mail. Column-level grants, nothing else:
```sql
CREATE ROLE agentco_status LOGIN PASSWORD '<strong password>';
GRANT CONNECT ON DATABASE railway TO agentco_status;  GRANT USAGE ON SCHEMA public TO agentco_status;
GRANT SELECT (booking_id, payment_status) ON bookings TO agentco_status;
GRANT UPDATE (payment_status, status, payment_reference, sent_to_publication_at, updated_at) ON bookings TO agentco_status;
```
   Put its URL in `ADSPOT_WRITE_DATABASE_URL`. Without it, AgentCo keeps its own record and reminds you to mark the booking paid in the admin panel.
6. Gmail: follow `docs/gmail-setup.md`.
7. Google sign-in: create an OAuth Web client, add your Railway URL as an authorised JavaScript origin, set `GOOGLE_CLIENT_ID` and `OWNER_EMAIL`. First login shows a TOTP secret to add to your authenticator app.
8. Push: generate VAPID keys and set the three `VAPID_*` vars.

## AdSpot mail, payments and receipts (Phase 2)
```
mail arrives -> Customer Desk (read/label/draft only) -> summary in Chat + Mail tab + push
you: "payment received for BOOK-20260101-001"  (or Bookings > Mark paid)
  -> CONFIRM CARD in Approvals (exact booking, customer, amount; red tick-box if the amount differs)
  -> you Confirm -> ledger entry, booking = paid, customer confirmation mail (30 s Undo),
     publication DRAFT(s) in Mail, booking added to the pending payout batch
you edit the draft, then press Send / say "send BOOK-..." -> mail to the publication (address from the publications table only)
you upload the publication's receipt (Chat receipt button or Bookings) -> customer mail DRAFT with the receipt attached
  -> Send (or turn on auto-send receipts) -> booking = customer_receipt_sent
Bookings > Payouts: a CSV of what each publication is owed. You pay by bank transfer, then press "I paid them".
```
Customer "payment received" mail from AgentCo is OFF by default (AdSpot's own Apps Script mail already covers it; switch it on in Mail > settings). The receipt mail is separate and stays.
Safety rules, all enforced in code and tests: customer mails go only to the email on the booking; publication mails only to addresses you typed into the publications table; every mail is sent at most once; nothing is ever auto-retried; an email can never trigger an action, add a recipient or mark anything paid; AgentCo never moves money.
Default mail wording: `docs/mail-templates.md`.

## Posts and ads (manual publish, no Facebook connection)
The **Posts** tab (or chat: "make a post for AdSpot about booking classified ads online") makes the Marketing agent write a ready-to-post package: copy for Facebook, Instagram, X and LinkedIn, hashtags, an image brief with an exact prompt, and recommended sizes. Choose **Ad** to also get a setup sheet (objective, audience, placements, headline, text, call to action) to type into Ads Manager yourself.
* Copy buttons for every block. Image: either make it in Google Flow from the prompt and upload it, or pick "Gemini (auto)" (needs `GEMINI_IMAGE_MODEL`).
* A second model pass checks the draft against your **brand facts** and flags unsupported claims ("needs review"). Edit the facts at the bottom of the Posts tab. Seeded facts are generic: no prices or discounts are used unless you add them.
* Customer data can never enter public content: emails, phone numbers and booking references are blocked in topics, facts and prompts, and flagged if a model leaks one.
* AgentCo never posts anywhere and never touches Meta. You post by hand, then tap "I posted it".

## Phases
1. Core + chat + approvals + daily reports (done).
1b. Manual-publish Posts and ad sheets (done). Postiz and Meta API are dropped.
2. Gmail, Customer Desk, payment flow, publication draft, receipt flow, booking state machine, payout batch (prepare only). (done)
3. Sidadiya slip checker, optional OmniRoute for PUBLIC copy with cost comparison.
4. Engineering agent (PRs), weekly Insights report.
