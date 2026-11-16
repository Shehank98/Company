-- YouTube Shorts pipeline: script -> shots/assets -> render -> review -> PRIVATE upload.
-- AgentCo never publishes publicly; the owner flips the video public in YouTube Studio.
CREATE TABLE video_projects (
  id            BIGSERIAL PRIMARY KEY,
  company_id    INT NOT NULL REFERENCES companies(id),
  topic         TEXT NOT NULL,
  language      TEXT NOT NULL DEFAULT 'en',
  status        TEXT NOT NULL DEFAULT 'drafting_script'
                CHECK (status IN ('drafting_script','script_review','assets','rendering','review','uploaded','rejected','failed')),
  voice_mode    TEXT NOT NULL DEFAULT 'tts' CHECK (voice_mode IN ('tts','none')),
  clip_mode     TEXT NOT NULL DEFAULT 'flow_manual' CHECK (clip_mode IN ('flow_manual','veo_api')),
  hook          JSONB NOT NULL DEFAULT '{}',          -- the chosen hook + its scored panel
  hook_options  JSONB NOT NULL DEFAULT '[]',          -- all five, scored by the vendored hookscore
  title         TEXT,
  description   TEXT,
  tags          JSONB NOT NULL DEFAULT '[]',
  claims_check  JSONB NOT NULL DEFAULT '{}',
  render_file   TEXT,
  preview_file  TEXT,
  duration_s    NUMERIC(6,2),
  error         TEXT,
  created_by    INT REFERENCES agents(id),
  run_id        BIGINT REFERENCES runs(id),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE video_shots (
  id            BIGSERIAL PRIMARY KEY,
  company_id    INT NOT NULL REFERENCES companies(id),
  project_id    BIGINT NOT NULL REFERENCES video_projects(id),
  beat_no       INT NOT NULL,
  spoken        TEXT NOT NULL DEFAULT '',
  on_screen     TEXT NOT NULL DEFAULT '',
  visual_prompt TEXT NOT NULL DEFAULT '',             -- exact prompt for Google Flow / Veo (9:16)
  duration_s    NUMERIC(5,2),
  asset_file    TEXT,
  asset_kind    TEXT CHECK (asset_kind IN ('video','image')),
  source        TEXT CHECK (source IN ('flow_upload','veo','owner_image')),
  voice_file    TEXT,
  op_name       TEXT,                                 -- Veo long-running operation
  status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','generating','ready','failed')),
  error         TEXT,
  UNIQUE (project_id, beat_no)
);

CREATE TABLE youtube_uploads (
  id          BIGSERIAL PRIMARY KEY,
  company_id  INT NOT NULL REFERENCES companies(id),
  project_id  BIGINT NOT NULL UNIQUE REFERENCES video_projects(id),   -- at most one upload per project
  status      TEXT NOT NULL CHECK (status IN ('claimed','done','failed')),
  video_id    TEXT,
  url         TEXT,
  privacy     TEXT NOT NULL DEFAULT 'private',
  error       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['video_projects','video_shots','youtube_uploads']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY company_isolation ON %I
         USING (company_id = nullif(current_setting(''app.company_id'', true), '''')::int)
         WITH CHECK (company_id = nullif(current_setting(''app.company_id'', true), '''')::int)', t);
  END LOOP;
END $$;
GRANT SELECT, INSERT, UPDATE ON video_projects, video_shots, youtube_uploads TO agentco_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO agentco_app;
