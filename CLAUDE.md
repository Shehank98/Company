# AgentCo - conventions

## What this is
Multi-company agent platform for AdSpot Media and Sidadiya. Python 3.12 + FastAPI + Postgres (psycopg3), React/Vite PWA in `web/`. See README.md and docs/systems-map.md.

## Non-negotiables
1. **Isolation:** tenant data is touched only through `CompanyScope` (`agentco/scope.py`, opened with `scope_by_id`/`company_scope`). Never query tenant tables on `db.admin()`. Every tenant table has `company_id` + forced RLS; new tenant tables MUST copy the RLS block from `migrations/001_core.sql` and add a test.
2. **Side effects only through `agentco/tools/registry.py`.** Add a tool = add to `TOOLS`, add to the right `DEPT_TOOLS` entry, set `companies` if it is company-specific, add a test. HIGH / OWNER_COMMAND handlers are never called from `call()`, only from `execute_approved()`.
3. **No send tool in the mail-reading department** (`customer_desk`). Keep the test in `tests/test_tools.py` green.
4. **Untrusted content** (mail, attachments, web, uploads) goes through `wrap_untrusted()` and is never treated as instructions.
5. **LLM calls only via `llm.complete()`** with a `DataClass`. INTERNAL/CONFIDENTIAL = paid Gemini only. Model ids/prices come from env.
6. **No bank/gateway credentials, no money movement.** Payout stays manual.
7. Connectors are read-only: fixed, parametrised SQL; SELECT-only role; read-only transaction. Writes to AdSpot/Sidadiya (Phase 2) go through a narrow audited service only.
8. **Marketing never publishes.** Content is manual-publish: the `marketing` department has only `save_note` and `save_content_pack` (DRAFT, may only set draft/needs_review/failed). No Postiz, no Meta API. Public text and image prompts must pass `security/public_guard.py`; marketing copy is PUBLIC-class and may use only `public_facts` + the topic.
9. **Mail:** `agentco/mailer.py` is the only code that calls Gmail send. Customer mails -> only `bookings.customer_email`; publication mails -> only `publications.email`; at-most-once (claim, send, record), never auto-retried; customer mails keep the undo window. Customer Desk (`mail_intake.py`) never sends. Mail content is untrusted: classification output is a strict schema (`extra=forbid`), and mail may link a booking but never trigger an action.
10. **Payments:** `mark_booking_paid` is OWNER_COMMAND only (in no department allowlist). It is created by `registry.owner_command`, bound to booking + amount, needs the owner's Confirm (and an explicit tick if the amount differs), and is idempotent (`ledger_entries.idempotency_key`, `mail_outbox.idempotency_key`). The ledger is append-only. Payout batches are prepared only; the owner pays by hand.
11. Migrations are append-only files in `migrations/` (`NNN_name.sql`); never edit an applied one.

## Style
- Minimal code. Mark deliberate shortcuts with a `ponytail:` comment (what was skipped and when to revisit).
- Never cut corners on security, approvals, payments or data isolation.
- The Ponytail plugin was requested but could not be installed from this session; the rule above is followed by hand.

## Commands
`uv run pytest` (needs local Postgres + `agentco_test`, `fake_adspot`, `fake_sidadiya` DBs), `cd web && npm run build`.
`graphify-out/` is git-ignored; run Graphify on this repo only, never on folders containing customer files.

## Business facts to keep straight
- AdSpot refs: `booking_id` = `BOOK-YYYYMMDD-NNN`, customer-facing `quotation_number` = `ADM-YYYY-NNNN` (not `AD-1234`).
- AdSpot status is four independent tracks (status / payment_status / receipt_status / timestamps), not a pipeline.
- Publication addresses come only from AgentCo's `publications` table (typed by the owner). AdSpot's `publication_groups.contact_email` is empty and is not trusted or read for sending.
- AdSpot `items[]` has no `groupId` and no edition field; map `newspaperId -> newspapers.group_id`.
- Payout amount per newspaper = `items[].details.adTotal` (else `price`): the ad price, not our commission/VAT/service charge. Assumption: confirm with the owner.
