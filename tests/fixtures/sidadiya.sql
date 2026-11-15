-- Subset of SBWEB/src/db/schema.sql + migrations (real column names).
DROP TABLE IF EXISTS businesses, payments, notifications, job_runs CASCADE;
CREATE TABLE businesses (id SERIAL PRIMARY KEY, status TEXT, created_at TIMESTAMPTZ DEFAULT now(),
  trial_ends_at TIMESTAMPTZ, verification_status TEXT);
CREATE TABLE payments (id SERIAL PRIMARY KEY, amount NUMERIC, status TEXT, reviewed_at TIMESTAMPTZ);
CREATE TABLE notifications (id SERIAL PRIMARY KEY, status TEXT);
CREATE TABLE job_runs (id SERIAL PRIMARY KEY, job TEXT, period TEXT, started_at TIMESTAMPTZ DEFAULT now(), finished_at TIMESTAMPTZ);
INSERT INTO businesses (status, trial_ends_at, verification_status, created_at) VALUES
 ('TRIAL', now() + interval '2 days', 'PENDING', now()), ('ACTIVE', NULL, 'APPROVED', now() - interval '20 days'),
 ('TRIAL_EXPIRED', NULL, NULL, now() - interval '30 days');
INSERT INTO payments (amount,status,reviewed_at) VALUES (1399,'APPROVED',now()), (999,'PENDING',NULL);
INSERT INTO notifications (status) VALUES ('SENT'),('SENT'),('FAILED');
INSERT INTO job_runs (job, period, finished_at) VALUES ('daily','2026-11-14', now());
