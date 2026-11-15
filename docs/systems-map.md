# Systems map (AdSpot Media + Sidadiya)

Source: read-only audit of `adspot_media` and `SBWEB` (Sidadiya) on branch `feature/peaceful-thompson-9xn2e8`.
Nothing was run against live systems, so live values (prices, `trial_days`, real publication emails) are unknown.
Where the code and the docs disagree, **the code is recorded here**. Items marked ⚠ need your confirmation.

## 1. Overview

| | AdSpot Media | Sidadiya |
|---|---|---|
| Repo | `adspot_media` | `SBWEB` (package `sidadiya-backend`, formerly "Kade") |
| Runtime | Node/Express 4 on Railway; static HTML/JS front-end | Node 18+/Express 4 on Railway; static HTML/JS front-end (`kade-frontend_V3` is live) |
| Database | Railway **PostgreSQL** (`DATABASE_URL`) | Railway **PostgreSQL** (`DATABASE_URL`) |
| Firebase | Auth (customers + admins) and **Storage** (artwork, receipts, invoices, proofs). No Firestore any more | **Storage only** (photos, slips, verification docs). No Firestore |
| Auth | Firebase ID token; admin = email on a list (`ADMIN_EMAILS`) | Own JWT (HS256) + bcrypt; roles SUPER_ADMIN / BUSINESS_OWNER / BUSINESS_STAFF |
| Email | Google Apps Script `MailApp`, fire-and-forget (`no-cors`), nothing sent from Express | DB outbox table `notifications`, drained by Apps Script (`apps-script/Code.gs`) |
| Payments | Bank transfer (+ receipt upload), HelaPay QR (webhook). PayHere is mentioned in config but has **no server code** | OnePay card (platform account), bank-transfer slip approved by admin |
| Gmail API | **Not used anywhere** | not used |

AgentCo connectors are **read-only Postgres** for both (your choice). Firebase Storage access (artwork/receipts) is a Phase 2 concern.

## 2. AdSpot Media

### Tables (`server/migrations/001…003`; no CHECK/enums, statuses are free text)
- `bookings`: `id`, `booking_id` (unique), `quotation_number`, `invoice_number`, `customer_id`, `customer_name/email/phone/company/address`, `items` JSONB, `total_amount`, `subtotal_amount`, `promo_code`, `promo_discount`, `payment_method`, `payment_status`, `payment_reference`, `quotation_pdf_url`, `status`, `notes`, `source`, `created_at`, `updated_at`; tracking: `artwork_status`, `artwork_received_at`, `artwork_approved_at`, `sent_to_publication_at`, `proof_url`, `published_at`; settings migration: `design_requested`, `design_fee`, `receipt_url`, `receipt_uploaded_at`, `receipt_status`.
- `customers`, `invoices`, `quotations`, `payments`, `promo_codes`, `push_subscriptions`, `design_requests`, `settings` (key/value rates), `publication_groups` (`id`, `name`, **`contact_email`**, `active`), `newspapers` (`group_id`, rates, `logo_url`). There is **no `publications` table**.
- `items[]` (JSONB per booking): `newspaperId`, `newspaperName`, `groupName`, `adType` (box|classified), `pubDate` (`YYYY-MM-DD`), `price`, `details`, `description`, `adFileUrl`. ⚠ `groupId` is **not** saved by the website, and there is **no "edition" field**.

### References customers and you see
- `booking_id` = `BOOK-YYYYMMDD-NNN` (random NNN, generated in the browser).
- `quotation_number` = `ADM-YYYY-NNNN`; this is the "Ref" printed for customers and the HelaPay reference.
- `invoice_number` = `INV-YYYYMM-NNNN` / `INV-YYYYMMDD-NNN`.
- ⚠ Neither is `AD-1234`. Which one do you say in chat?

### Status model: four independent tracks, no single pipeline
1. `status`: `pending` → `confirmed` → `completed` | `cancelled` (admin dropdown). The server also writes `paid` (payment route / HelaPay webhook) and `markPublished()` writes `completed`.
2. `payment_status`: `pending` | `completed` | `failed`.
3. `receipt_status`: `none` → `submitted` → `approved` | `rejected`.
4. Timestamps: `artwork_approved_at`, `sent_to_publication_at`, `published_at`, plus `proof_url`.
Notably **"sent to publication" is a manual button**; sending the email does not set `sent_to_publication_at`.
AgentCo's booking state machine (Phase 2) will be *derived* from these columns, not replace them.

### Payments
- Bank transfer: receipt uploaded to Firebase Storage (`receipts/{bookingId}/…`), URL in `bookings.receipt_url`. Admin approves → `payment_status=completed`, `receipt_status=approved` (does **not** change `status`).
- HelaPay: `POST /api/helapay` (QR + status), `POST /api/helapay-webhook` (re-verifies with HelaPay, then sets booking `payment_status=completed`, `status=paid`, inserts a `payments` row **without `booking_id`**, sends web push).
- Bank details in `js/config.js`: Sampath Bank PLC (account name P S Kavishka).
- ⚠ Known bug: `saveBookingToFirebase()` drops `receiptUrl`, `receiptStatus`, `designRequested`, `designFee`, so a receipt pre-uploaded at booking time is not stored on the booking.

### Publications and sending to papers
- `publication_groups.contact_email` is **empty for all 6 seeded groups**. Real addresses must be entered in `/admin-publications`. ⚠ I need your list for Phase 2.
- Today the admin UI builds a plain-text mail per group (subject `Classified Ad – … | Publication Date … | Ref …`) with a **mandatory payment-slip attachment** and sends it through Apps Script action `send_to_publication` (cc `adspot77@gmail.com`). Artwork URLs and customer contact details are **not** in that mail.
- Several client actions have **no handler in any `.gs` file in the repo** (`send_proof_of_publication`, `send_design_delivery`, `send_manual_quotation`); the deployed script is unknown.

### Auth/admin facts relevant to AgentCo
- `server/middleware/auth.js`: Firebase Admin from `FIREBASE_PROJECT_ID/CLIENT_EMAIL/PRIVATE_KEY`; admin by email list (`adspot77@gmail.com`, `shehan.k98@gmail.com` default).
- Env vars read: `DATABASE_URL`, `NODE_ENV`, `PORT`, Firebase trio, `ADMIN_EMAILS`, `HELAPAY_*` (4), `VAPID_*` (3; optional), `SUPABASE_*` (migrate script only).

### Data AgentCo reads (Phase 1, SELECT only)
`bookings` (counts by the four status columns, revenue, receipts awaiting review, paid-not-sent, sent-not-published, unpaid > 3 days, `items[].pubDate` in next 7 days) and `design_requests`. Numbers only; no customer fields reach the model.
⚠ `bookings` has no `paid_at`; revenue windows use `updated_at` as a proxy.

## 3. Sidadiya (SBWEB)

### Tables (`src/db/schema.sql` + migrations 001–017)
`businesses` (status, `trial_started_at`, `trial_ends_at`, `preferred_plan_id`, verification fields), `users`, `business_members` (multi-shop), `stores`, `plans` (starter 999 / business 1,399 / pro 2,000 LKR, admin-editable), `subscriptions`, `payments` (plan payments: `amount`, `method`, `slip_url`, `status`, `reviewed_at`), `gateway_transactions`, `store_gateways`, `products`, `orders`/`order_items`/`order_status_history`, `coupons`, `product_reviews`, `store_visits`, `seller_digests`, `notifications` (email outbox), `platform_settings`, `job_runs`, `schema_migrations`.

### Lifecycle
- Register → 14-day trial (`platform_settings.trial_days`, default 14), no approval step.
- Business status: `TRIAL`, `TRIAL_EXPIRED`, `PENDING_PAYMENT`, `PENDING_APPROVAL`, `ACTIVE`, `EXPIRING`, `GRACE_PERIOD`, `SUSPENDED`, `CANCELLED`.
- Daily job (07:00 SLT) sends trial reminders (day 10 and 13), moves expired trials, handles expiring/grace/suspend. Weekly seller summary Mondays 08:00 SLT. Each run claims a `job_runs` row so it runs once per period.
- Plan payment: OnePay card (`/api/dashboard/subscription/onepay`, callback re-verified via OnePay status API) **or** bank slip (`payments.status=PENDING`) approved by admin at `POST /api/admin/payments/:id/approve`.
- Orders (sellers' shops) have their own `payment_status` incl. `SLIP_UPLOADED`.

### Data AgentCo reads (Phase 1, SELECT only)
Shop counts by `businesses.status`, signups 24h/7d, trials ending ≤ 3 days, expired trials, paid/active shops, suspended/cancelled, pending plan slips (`payments.status='PENDING'`), verification pending, APPROVED revenue 24h/30d, `notifications` by status, last `job_runs`.
⚠ The outbox status vocabulary was not fully enumerated, so it is grouped generically.

### Env vars read
`PORT`, `CORS_ORIGIN`, `DATABASE_URL`, `DATABASE_SSL`, `JWT_SECRET`, `JWT_EXPIRES_IN`, `ADMIN_EMAIL/PASSWORD/NAME`, `FRONTEND_DIR`, `UPLOAD_DRIVER`, `PUBLIC_BASE_URL`, `FIREBASE_*`, `SEED_DEMO`, `ENABLE_SCHEDULER`, `NOTIFY_TOKEN`, `APPS_SCRIPT_URL`, `ONEPAY_*`, `GATEWAY_ENC_KEY`.

## 4. Findings worth your attention (reported only; not changed, outside this spec)
Security:
1. Sidadiya: default admin `admin@sidadiya.lk` / `admin12345` is applied **on every boot** from `ADMIN_*` env defaults. Make sure `ADMIN_PASSWORD` is set to something strong in production.
2. Sidadiya: if `NOTIFY_TOKEN` is unset it falls back to `JWT_SECRET`, so the JWT signing secret doubles as the worker token. Set `NOTIFY_TOKEN`.
3. AdSpot: `POST /api/helapay`, `POST /api/helapay-webhook`, `POST /api/push-register`, `GET /api/publications/debug` are unauthenticated; `POST /api/payments` needs only a login (not admin) and can set a booking to `paid`.
4. AdSpot: `verifyFirebaseToken` has a non-production dev fallback (`uid='dev'`) when Firebase creds are unset.
5. AdSpot `AppScript-EmailService.gs`: admin-copy mail references an undefined `body` (throws after the customer mail is sent); invoice text says commission 10% while the setting is 5%; booking-confirmation hardcodes a placeholder bank account (Commercial Bank 1234567890) that contradicts the real Sampath details.

Behaviour that shapes AgentCo:
6. Because AdSpot mails are fire-and-forget (`no-cors`), nothing can confirm delivery. AgentCo's Gmail connector (Phase 2) will log Message-ID and thread ID itself.
7. `groupId` missing on saved items: publication routing must map `items[].newspaperId → newspapers.group_id → publication_groups.contact_email`.
8. CLAUDE.md of `adspot_media` is partly out of date: artwork path uses `quotationNumber` not `bookingId`; Apps Script action names differ (`booking_confirmation`, `invoice_email`); design fee is 3000 in settings but 2500 in several hard-coded places; the booking page still increments promo usage through Firestore.

## 5. How AgentCo treats these systems
- Phase 1: SELECT-only roles, read-only transactions, fixed parametrised SQL, aggregates only.
- Phase 2 will add writes **only** through narrow, audited services for status fields, and only after an owner command (see spec §7).
- All connector credentials are Railway env vars and never appear in model context.
