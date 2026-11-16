-- Manual-publish content packs (post copy + image + optional Ads Manager setup sheet).
-- Nothing here publishes anywhere: the owner copies the text/image into Facebook by hand.
CREATE TABLE content_packs (
  id            BIGSERIAL PRIMARY KEY,
  company_id    INT NOT NULL REFERENCES companies(id),
  kind          TEXT NOT NULL CHECK (kind IN ('post','ad')),
  topic         TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'drafting'
                CHECK (status IN ('drafting','draft','needs_review','approved','posted','rejected','failed')),
  copy          JSONB NOT NULL DEFAULT '{}',      -- {facebook, instagram, x, linkedin}
  hashtags      JSONB NOT NULL DEFAULT '[]',
  image_mode    TEXT NOT NULL DEFAULT 'flow_manual' CHECK (image_mode IN ('flow_manual','gemini_image')),
  image_brief   TEXT,
  image_prompt  TEXT,
  aspect_ratios JSONB NOT NULL DEFAULT '{}',
  image_file    TEXT,                             -- stored filename under UPLOAD_DIR/content
  ad_setup      JSONB,                            -- copy-into-Ads-Manager sheet (kind='ad')
  claims_check  JSONB NOT NULL DEFAULT '{}',
  error         TEXT,
  created_by    INT REFERENCES agents(id),
  run_id        BIGINT REFERENCES runs(id),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE content_packs ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_packs FORCE ROW LEVEL SECURITY;
CREATE POLICY company_isolation ON content_packs
  USING (company_id = nullif(current_setting('app.company_id', true), '')::int)
  WITH CHECK (company_id = nullif(current_setting('app.company_id', true), '')::int);
GRANT SELECT, INSERT, UPDATE ON content_packs TO agentco_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO agentco_app;
