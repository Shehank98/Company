-- AgentCo core schema (Phase 1).
-- Isolation model: every tenant row carries company_id. Tenant tables have FORCED row-level
-- security keyed on app.company_id, and the app switches to the low-privilege role agentco_app
-- (SET LOCAL ROLE) inside every company-scoped transaction, so RLS applies even when the
-- connection itself is a superuser (as on Railway).

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agentco_app') THEN
    CREATE ROLE agentco_app NOLOGIN;
  END IF;
END $$;

CREATE TABLE companies (
  id          SERIAL PRIMARY KEY,
  slug        TEXT UNIQUE NOT NULL,
  name        TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agents (
  id                 SERIAL PRIMARY KEY,
  company_id         INT NOT NULL REFERENCES companies(id),
  dept               TEXT NOT NULL,            -- command|marketing|customer_desk|operations|finance|engineering|insights
  name               TEXT NOT NULL,
  role               TEXT NOT NULL,            -- behaviour key: command|ops_daily|insights_daily|...
  status             TEXT NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active','paused','budget_exhausted')),
  monthly_budget_usd NUMERIC(10,4) NOT NULL DEFAULT 5,
  model_tier         TEXT NOT NULL DEFAULT 'flash' CHECK (model_tier IN ('flash','pro')),
  schedule_hhmm      TEXT,                     -- daily run time, Asia/Colombo, e.g. '07:30'
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (company_id, name)
);

CREATE TABLE tasks (
  id          SERIAL PRIMARY KEY,
  company_id  INT NOT NULL REFERENCES companies(id),
  agent_id    INT REFERENCES agents(id),
  title       TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo','running','done','failed','blocked')),
  payload     JSONB NOT NULL DEFAULT '{}',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE events (
  id          BIGSERIAL PRIMARY KEY,
  company_id  INT NOT NULL REFERENCES companies(id),
  type        TEXT NOT NULL,
  source_dept TEXT,
  payload     JSONB NOT NULL DEFAULT '{}',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE runs (
  id          BIGSERIAL PRIMARY KEY,
  company_id  INT NOT NULL REFERENCES companies(id),
  agent_id    INT NOT NULL REFERENCES agents(id),
  task_id     INT REFERENCES tasks(id),
  status      TEXT NOT NULL DEFAULT 'running'
              CHECK (status IN ('running','done','failed','skipped','budget_stopped')),
  error       TEXT,
  started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);

-- Audit of every tool call, including denied ones.
CREATE TABLE tool_calls (
  id          BIGSERIAL PRIMARY KEY,
  company_id  INT NOT NULL REFERENCES companies(id),
  run_id      BIGINT REFERENCES runs(id),
  agent_id    INT REFERENCES agents(id),
  tool        TEXT NOT NULL,
  tier        TEXT,
  args        JSONB NOT NULL DEFAULT '{}',
  result      JSONB,
  status      TEXT NOT NULL CHECK (status IN ('ok','denied','pending_approval','error')),
  reason      TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE approvals (
  id               BIGSERIAL PRIMARY KEY,
  company_id       INT NOT NULL REFERENCES companies(id),
  tool             TEXT NOT NULL,
  tier             TEXT NOT NULL,
  summary          TEXT NOT NULL,
  payload          JSONB NOT NULL DEFAULT '{}',
  booking_ref      TEXT,                       -- OWNER_COMMAND approvals bind to a booking...
  amount           NUMERIC(14,2),              -- ...and an exact amount
  idempotency_key  TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','approved','rejected','executed','expired')),
  requested_by     INT REFERENCES agents(id),
  run_id           BIGINT REFERENCES runs(id),
  decided_at       TIMESTAMPTZ,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (company_id, idempotency_key)
);

CREATE TABLE cost_events (
  id          BIGSERIAL PRIMARY KEY,
  company_id  INT NOT NULL REFERENCES companies(id),
  agent_id    INT REFERENCES agents(id),
  run_id      BIGINT REFERENCES runs(id),
  provider    TEXT NOT NULL,
  model       TEXT NOT NULL,
  route       TEXT NOT NULL DEFAULT 'direct',  -- later: omniroute vs gemini baseline
  data_class  TEXT NOT NULL CHECK (data_class IN ('PUBLIC','INTERNAL','CONFIDENTIAL')),
  task        TEXT NOT NULL,
  tokens_in   INT NOT NULL DEFAULT 0,
  tokens_out  INT NOT NULL DEFAULT 0,
  cost_usd    NUMERIC(12,6) NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE notes (
  id          BIGSERIAL PRIMARY KEY,
  company_id  INT NOT NULL REFERENCES companies(id),
  agent_id    INT REFERENCES agents(id),
  kind        TEXT NOT NULL,                   -- daily_report|note|...
  title       TEXT NOT NULL,
  body        TEXT NOT NULL,
  data        JSONB NOT NULL DEFAULT '{}',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Only facts in this table (plus a post topic) may ever be sent to OmniRoute (Phase 3).
CREATE TABLE public_facts (
  id          SERIAL PRIMARY KEY,
  company_id  INT NOT NULL REFERENCES companies(id),
  key         TEXT NOT NULL,
  value       TEXT NOT NULL,
  UNIQUE (company_id, key)
);

-- Postgres job queue (FOR UPDATE SKIP LOCKED). System table: claimed unscoped, executed scoped.
CREATE TABLE jobs (
  id          BIGSERIAL PRIMARY KEY,
  company_id  INT NOT NULL REFERENCES companies(id),
  agent_id    INT REFERENCES agents(id),
  kind        TEXT NOT NULL,
  payload     JSONB NOT NULL DEFAULT '{}',
  status      TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
  run_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  locked_at   TIMESTAMPTZ,
  attempts    INT NOT NULL DEFAULT 0,
  last_error  TEXT,
  dedupe_key  TEXT UNIQUE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX jobs_queue_idx ON jobs (run_at) WHERE status = 'queued';

-- Owner chat. Single owner thread; scope = adspot|sidadiya|all. Not tenant-RLS'd (owner-only channel).
CREATE TABLE messages (
  id          BIGSERIAL PRIMARY KEY,
  scope       TEXT NOT NULL,
  role        TEXT NOT NULL CHECK (role IN ('owner','command','system')),
  body        TEXT NOT NULL,
  attachment  JSONB,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE push_subscriptions (
  id        SERIAL PRIMARY KEY,
  endpoint  TEXT UNIQUE NOT NULL,
  p256dh    TEXT NOT NULL,
  auth      TEXT NOT NULL
);

CREATE TABLE owner_auth (
  id              INT PRIMARY KEY CHECK (id = 1),
  totp_secret     TEXT NOT NULL,
  totp_confirmed  BOOLEAN NOT NULL DEFAULT FALSE
);

-- ---------- Row-level security on tenant tables ----------
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['agents','tasks','events','runs','tool_calls','approvals',
                           'cost_events','notes','public_facts']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY company_isolation ON %I
         USING (company_id = nullif(current_setting(''app.company_id'', true), '''')::int)
         WITH CHECK (company_id = nullif(current_setting(''app.company_id'', true), '''')::int)', t);
  END LOOP;
END $$;

-- ---------- Least privilege for agentco_app ----------
GRANT USAGE ON SCHEMA public TO agentco_app;
GRANT SELECT ON companies TO agentco_app;
GRANT SELECT, INSERT, UPDATE ON agents, tasks, runs, approvals, notes, public_facts TO agentco_app;
-- audit tables are append-only for the app role
GRANT SELECT, INSERT ON events, tool_calls, cost_events TO agentco_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO agentco_app;
