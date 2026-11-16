-- Phase 2: bookings mirror + state machine, ledger, publications, mail log/outbox, payouts, settings.
-- All tenant tables: company_id + forced RLS (same pattern as 001).

CREATE TABLE publications (
  id          SERIAL PRIMARY KEY,
  company_id  INT NOT NULL REFERENCES companies(id),
  group_ref   TEXT NOT NULL,                -- AdSpot publication_groups.id (e.g. 'lake-house')
  name        TEXT NOT NULL,
  email       TEXT NOT NULL DEFAULT '',     -- the ONLY trusted source of publication addresses
  active      BOOLEAN NOT NULL DEFAULT TRUE,
  UNIQUE (company_id, group_ref)
);

-- Mirror of the live app's bookings (synced read-only) plus AgentCo-only workflow state.
CREATE TABLE bookings (
  id               BIGSERIAL PRIMARY KEY,
  company_id       INT NOT NULL REFERENCES companies(id),
  booking_ref      TEXT NOT NULL,           -- AdSpot booking_id  BOOK-YYYYMMDD-NNN
  quotation_ref    TEXT,                    -- AdSpot quotation_number ADM-YYYY-NNNN
  customer_name    TEXT,
  customer_email   TEXT,                    -- the ONLY address customer mails may go to
  total_amount     NUMERIC(14,2) NOT NULL DEFAULT 0,
  items            JSONB NOT NULL DEFAULT '[]',
  source_status    TEXT,
  source_payment   TEXT,
  state            TEXT NOT NULL DEFAULT 'awaiting_payment'
                   CHECK (state IN ('enquiry','awaiting_payment','paid','sent_to_publication','receipt_received',
                                    'customer_receipt_sent','published','closed','exception')),
  exception_reason TEXT,
  paid_at          TIMESTAMPTZ,
  source_synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (company_id, booking_ref)
);
CREATE INDEX bookings_quotation_idx ON bookings (company_id, quotation_ref);

-- Append-only money record (what the owner confirmed). AgentCo moves no money.
CREATE TABLE ledger_entries (
  id               BIGSERIAL PRIMARY KEY,
  company_id       INT NOT NULL REFERENCES companies(id),
  kind             TEXT NOT NULL CHECK (kind IN ('payment_in','payout_out')),
  booking_ref      TEXT,
  amount           NUMERIC(14,2) NOT NULL,
  method           TEXT,
  marked_by        TEXT NOT NULL,
  note             TEXT,
  idempotency_key  TEXT NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (company_id, idempotency_key)
);

CREATE TABLE mail_outbox (
  id               BIGSERIAL PRIMARY KEY,
  company_id       INT NOT NULL REFERENCES companies(id),
  kind             TEXT NOT NULL CHECK (kind IN ('customer_payment','customer_receipt','publication')),
  booking_ref      TEXT NOT NULL,
  to_addr          TEXT NOT NULL,
  subject          TEXT NOT NULL,
  body             TEXT NOT NULL,
  attachments      JSONB NOT NULL DEFAULT '[]',     -- [{name, source:'upload'|'url', ref}]
  thread_id        TEXT,
  in_reply_to      TEXT,
  status           TEXT NOT NULL DEFAULT 'draft'
                   CHECK (status IN ('draft','scheduled','sending','sent','cancelled','failed')),
  send_after       TIMESTAMPTZ,
  idempotency_key  TEXT NOT NULL,
  gmail_message_id TEXT,
  sent_at          TIMESTAMPTZ,
  error            TEXT,
  edited           BOOLEAN NOT NULL DEFAULT FALSE,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (company_id, idempotency_key)
);

-- Every mail in or out: Message-ID, thread id, booking id.
CREATE TABLE mail_log (
  id               BIGSERIAL PRIMARY KEY,
  company_id       INT NOT NULL REFERENCES companies(id),
  direction        TEXT NOT NULL CHECK (direction IN ('in','out')),
  gmail_message_id TEXT NOT NULL,
  thread_id        TEXT,
  rfc_message_id   TEXT,
  from_addr        TEXT,
  to_addr          TEXT,
  subject          TEXT,
  category         TEXT,
  summary          TEXT,
  booking_ref      TEXT,
  link_confidence  TEXT,                            -- verified (sender matches booking) | unverified (ref only)
  outbox_id        BIGINT REFERENCES mail_outbox(id),
  draft_id         TEXT,                            -- Gmail draft created for a reply
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (company_id, direction, gmail_message_id)
);

CREATE TABLE company_settings (
  company_id  INT NOT NULL REFERENCES companies(id),
  key         TEXT NOT NULL,
  value       TEXT NOT NULL,
  PRIMARY KEY (company_id, key)
);

-- Payout batches are PREPARED only; the owner pays publications manually and records it.
CREATE TABLE payout_batches (
  id          BIGSERIAL PRIMARY KEY,
  company_id  INT NOT NULL REFERENCES companies(id),
  status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','paid_manually')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  paid_at     TIMESTAMPTZ
);
CREATE TABLE payout_items (
  id          BIGSERIAL PRIMARY KEY,
  company_id  INT NOT NULL REFERENCES companies(id),
  batch_id    BIGINT NOT NULL REFERENCES payout_batches(id),
  booking_ref TEXT NOT NULL,
  group_ref   TEXT NOT NULL,
  amount      NUMERIC(14,2) NOT NULL,
  UNIQUE (company_id, booking_ref, group_ref)
);

ALTER TABLE messages ADD COLUMN linked_booking TEXT;

DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['publications','bookings','ledger_entries','mail_outbox','mail_log',
                           'company_settings','payout_batches','payout_items']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY company_isolation ON %I
         USING (company_id = nullif(current_setting(''app.company_id'', true), '''')::int)
         WITH CHECK (company_id = nullif(current_setting(''app.company_id'', true), '''')::int)', t);
  END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE ON publications, bookings, mail_outbox, company_settings,
                                payout_batches, payout_items TO agentco_app;
GRANT SELECT, INSERT ON ledger_entries, mail_log TO agentco_app;   -- append-only
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO agentco_app;
