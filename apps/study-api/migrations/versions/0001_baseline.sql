-- StudyHub baseline schema (revision 2 of the data-layer design).
--
-- Rules this file encodes:
--   * A workspace owns every tenant row. Children reference parents with composite
--     (workspace_id, id) foreign keys so a row can never point into another workspace.
--   * Content is versioned: blob (bytes) -> processing revision (parser output) ->
--     embedding set (vectors from one profile). Citations pin a revision and chunk.
--   * Row-level security is a second guard. The application roles do not own tables and
--     cannot bypass RLS; identity is set per transaction with set_config(..., true).
-- Executed verbatim (no parameter substitution) by 0001_baseline.py.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS citext;

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'studyhub_app') THEN
    CREATE ROLE studyhub_app NOLOGIN NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'studyhub_worker') THEN
    CREATE ROLE studyhub_worker NOLOGIN NOBYPASSRLS;
  END IF;
END $$;

-- ---------------------------------------------------------------- helpers

CREATE FUNCTION studyhub_uuidv7() RETURNS uuid LANGUAGE sql VOLATILE AS $$
  SELECT encode(set_bit(set_bit(overlay(uuid_send(gen_random_uuid()) PLACING
    substring(int8send((extract(epoch FROM clock_timestamp()) * 1000)::bigint) FROM 3)
    FROM 1 FOR 6), 52, 1), 53, 1), 'hex')::uuid
$$;

CREATE FUNCTION app_workspace() RETURNS uuid LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('app.workspace_id', true), '')::uuid
$$;

CREATE FUNCTION app_user() RETURNS uuid LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('app.user_id', true), '')::uuid
$$;

CREATE FUNCTION touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;

-- ---------------------------------------------------------------- identity & tenancy

CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT studyhub_uuidv7(),
  display_name text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 120),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'pending_deletion', 'deleted')),
  is_instance_admin boolean NOT NULL DEFAULT false,
  deletion_requested_at timestamptz,
  purge_after timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (status <> 'pending_deletion' OR purge_after IS NOT NULL)
);

CREATE TABLE auth_identities (
  id uuid PRIMARY KEY DEFAULT studyhub_uuidv7(),
  user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  provider text NOT NULL CHECK (provider IN ('password', 'oidc')),
  issuer text NOT NULL DEFAULT 'local',
  subject text NOT NULL,
  email citext NOT NULL CHECK (email ~ '^[^@[:space:]]+@[^@[:space:]]+$'),
  email_verified_at timestamptz,
  password_hash text,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (provider <> 'password' OR password_hash IS NOT NULL)
);
-- The email is released when an identity is revoked, so a purged account's address can
-- register again and receive a brand-new user id.
CREATE UNIQUE INDEX auth_identities_live_subject ON auth_identities (provider, issuer, subject) WHERE revoked_at IS NULL;
CREATE UNIQUE INDEX auth_identities_live_email ON auth_identities (email) WHERE revoked_at IS NULL AND provider = 'password';
CREATE INDEX auth_identities_user ON auth_identities (user_id);

CREATE TABLE workspaces (
  id uuid PRIMARY KEY DEFAULT studyhub_uuidv7(),
  kind text NOT NULL CHECK (kind IN ('personal', 'team')),
  name text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
  slug citext NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'pending_deletion', 'deleted')),
  plan text NOT NULL DEFAULT 'community',
  created_by uuid REFERENCES users (id) ON DELETE SET NULL,
  purge_after timestamptz,
  row_version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX workspaces_one_personal ON workspaces (created_by) WHERE kind = 'personal' AND status <> 'deleted';

CREATE TABLE memberships (
  workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  role text NOT NULL CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'invited', 'removed')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, user_id)
);
CREATE INDEX memberships_user ON memberships (user_id) WHERE status = 'active';

CREATE FUNCTION enforce_workspace_owner() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  ws uuid := COALESCE(OLD.workspace_id, NEW.workspace_id);
BEGIN
  IF EXISTS (SELECT 1 FROM workspaces w WHERE w.id = ws AND w.status = 'active')
     AND NOT EXISTS (SELECT 1 FROM memberships m
                     WHERE m.workspace_id = ws AND m.role = 'owner' AND m.status = 'active') THEN
    RAISE EXCEPTION USING MESSAGE = 'A workspace must keep at least one active owner.',
                          ERRCODE = 'check_violation';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER memberships_keep_owner AFTER UPDATE OR DELETE ON memberships
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION enforce_workspace_owner();

CREATE TABLE auth_sessions (
  id uuid PRIMARY KEY DEFAULT studyhub_uuidv7(),
  user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  token_hash bytea NOT NULL UNIQUE CHECK (length(token_hash) = 32),
  active_workspace_id uuid REFERENCES workspaces (id) ON DELETE SET NULL,
  ip inet,
  user_agent text,
  created_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz
);
CREATE INDEX auth_sessions_user ON auth_sessions (user_id) WHERE revoked_at IS NULL;

CREATE TABLE workspace_limits (
  workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
  key text NOT NULL CHECK (key IN ('storage_bytes', 'items', 'pages_month', 'tokens_month')),
  limit_value bigint CHECK (limit_value >= 0),
  used_value bigint NOT NULL DEFAULT 0 CHECK (used_value >= 0),
  period_start timestamptz,
  PRIMARY KEY (workspace_id, key)
);

CREATE TABLE tutor_providers (
  id uuid PRIMARY KEY DEFAULT studyhub_uuidv7(),
  scope text NOT NULL CHECK (scope IN ('system', 'workspace')),
  workspace_id uuid REFERENCES workspaces (id) ON DELETE CASCADE,
  label text NOT NULL CHECK (length(label) BETWEEN 1 AND 120),
  kind text NOT NULL CHECK (kind IN ('ollama', 'openai')),
  base_url text NOT NULL CHECK (base_url ~ '^https?://'),
  model text NOT NULL DEFAULT '',
  secret_ciphertext bytea,
  secret_nonce bytea,
  secret_key_version smallint,
  created_by uuid REFERENCES users (id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK ((scope = 'system') = (workspace_id IS NULL)),
  CHECK ((secret_ciphertext IS NULL) = (secret_nonce IS NULL)
     AND (secret_ciphertext IS NULL) = (secret_key_version IS NULL))
);
CREATE INDEX tutor_providers_workspace ON tutor_providers (workspace_id);

CREATE TABLE user_settings (
  user_id uuid PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
  schema_version integer NOT NULL DEFAULT 1,
  preferences jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(preferences) = 'object'),
  default_tutor_id uuid REFERENCES tutor_providers (id) ON DELETE SET NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- content pipeline

CREATE TABLE content_blobs (
  id uuid PRIMARY KEY DEFAULT studyhub_uuidv7(),
  workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
  sha256 bytea NOT NULL CHECK (length(sha256) = 32),
  generation integer NOT NULL DEFAULT 1 CHECK (generation >= 1),
  state text NOT NULL DEFAULT 'available' CHECK (state IN ('available', 'deleting', 'deleted')),
  object_key text NOT NULL UNIQUE,
  size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
  mime_type text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('pdf', 'md', 'txt', 'docx')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, id),
  UNIQUE (workspace_id, sha256, generation)
);
-- Only one generation of the same bytes can accept new references at a time.
CREATE UNIQUE INDEX content_blobs_attachable ON content_blobs (workspace_id, sha256) WHERE state = 'available';

CREATE TABLE upload_sessions (
  id uuid PRIMARY KEY DEFAULT studyhub_uuidv7(),
  workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
  created_by uuid REFERENCES users (id) ON DELETE SET NULL,
  filename text NOT NULL,
  staging_key text NOT NULL,
  expected_size bigint CHECK (expected_size >= 0),
  verified_sha256 bytea CHECK (verified_sha256 IS NULL OR length(verified_sha256) = 32),
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'verified', 'published', 'failed', 'expired')),
  library_item_id uuid,
  error text,
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX upload_sessions_open ON upload_sessions (expires_at) WHERE status IN ('open', 'verified');

CREATE TABLE processing_revisions (
  id uuid PRIMARY KEY DEFAULT studyhub_uuidv7(),
  workspace_id uuid NOT NULL,
  blob_id uuid NOT NULL,
  parser text NOT NULL CHECK (parser IN ('docling', 'markdown', 'text', 'legacy')),
  parser_version text NOT NULL,
  config_fingerprint text NOT NULL,
  config jsonb NOT NULL DEFAULT '{}',
  state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'ready', 'failed', 'superseded')),
  error text,
  title text,
  page_count integer CHECK (page_count >= 0),
  artifact_manifest jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(artifact_manifest) = 'object'),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  ready_at timestamptz,
  FOREIGN KEY (workspace_id, blob_id) REFERENCES content_blobs (workspace_id, id) ON DELETE CASCADE,
  UNIQUE (workspace_id, id),
  UNIQUE (workspace_id, blob_id, id),
  UNIQUE (blob_id, config_fingerprint),
  CHECK ((state = 'ready') = (ready_at IS NOT NULL))
);

CREATE TABLE sections (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  workspace_id uuid NOT NULL,
  revision_id uuid NOT NULL,
  parent_id bigint,
  slug text NOT NULL,
  title text NOT NULL,
  level smallint NOT NULL CHECK (level BETWEEN 1 AND 9),
  position integer NOT NULL CHECK (position >= 0),
  page_start integer NOT NULL CHECK (page_start >= 1),
  page_end integer CHECK (page_end >= page_start),
  FOREIGN KEY (workspace_id, revision_id) REFERENCES processing_revisions (workspace_id, id) ON DELETE CASCADE,
  UNIQUE (revision_id, id),
  UNIQUE (revision_id, slug),
  UNIQUE (revision_id, position),
  -- A parent is always written first, so a tree cannot contain a cycle.
  CHECK (parent_id IS NULL OR parent_id < id)
);
ALTER TABLE sections ADD FOREIGN KEY (revision_id, parent_id) REFERENCES sections (revision_id, id) ON DELETE CASCADE;

CREATE TABLE chunks (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  workspace_id uuid NOT NULL,
  revision_id uuid NOT NULL,
  section_id bigint,
  ordinal integer NOT NULL CHECK (ordinal >= 0),
  page integer CHECK (page >= 1),
  text text NOT NULL,
  char_count integer GENERATED ALWAYS AS (length(text)) STORED,
  FOREIGN KEY (workspace_id, revision_id) REFERENCES processing_revisions (workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (revision_id, section_id) REFERENCES sections (revision_id, id) ON DELETE SET NULL (section_id),
  UNIQUE (revision_id, ordinal),
  UNIQUE (revision_id, id)
);
CREATE INDEX chunks_revision_page ON chunks (revision_id, page);

CREATE TABLE embedding_profiles (
  id smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  provider text NOT NULL,
  model text NOT NULL,
  model_digest text NOT NULL,
  dimensions smallint NOT NULL CHECK (dimensions BETWEEN 1 AND 16000),
  metric text NOT NULL DEFAULT 'cosine' CHECK (metric IN ('cosine', 'l2', 'ip')),
  preprocessing_fingerprint text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (provider, model, model_digest, preprocessing_fingerprint)
);

CREATE TABLE embedding_sets (
  id uuid PRIMARY KEY DEFAULT studyhub_uuidv7(),
  workspace_id uuid NOT NULL,
  revision_id uuid NOT NULL,
  profile_id smallint NOT NULL REFERENCES embedding_profiles (id),
  state text NOT NULL DEFAULT 'building' CHECK (state IN ('building', 'ready', 'retired', 'failed')),
  expected_count integer NOT NULL CHECK (expected_count >= 0),
  embedded_count integer NOT NULL DEFAULT 0 CHECK (embedded_count >= 0 AND embedded_count <= expected_count),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  ready_at timestamptz,
  FOREIGN KEY (workspace_id, revision_id) REFERENCES processing_revisions (workspace_id, id) ON DELETE CASCADE,
  UNIQUE (revision_id, profile_id),
  UNIQUE (revision_id, id, profile_id),
  CHECK (state <> 'ready' OR (embedded_count = expected_count AND ready_at IS NOT NULL))
);

CREATE TABLE embeddings (
  set_id uuid NOT NULL,
  chunk_id bigint NOT NULL,
  workspace_id uuid NOT NULL,
  revision_id uuid NOT NULL,
  profile_id smallint NOT NULL,
  embedding vector NOT NULL,
  PRIMARY KEY (set_id, chunk_id),
  FOREIGN KEY (revision_id, set_id, profile_id) REFERENCES embedding_sets (revision_id, id, profile_id) ON DELETE CASCADE,
  FOREIGN KEY (revision_id, chunk_id) REFERENCES chunks (revision_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, revision_id) REFERENCES processing_revisions (workspace_id, id) ON DELETE CASCADE
);

CREATE FUNCTION check_embedding_dims() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF vector_dims(NEW.embedding) IS DISTINCT FROM
     (SELECT dimensions FROM embedding_profiles WHERE id = NEW.profile_id) THEN
    RAISE EXCEPTION USING MESSAGE = 'Embedding dimensions do not match its profile.',
                          ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER embeddings_dims BEFORE INSERT OR UPDATE OF embedding, profile_id ON embeddings
  FOR EACH ROW EXECUTE FUNCTION check_embedding_dims();

-- ---------------------------------------------------------------- research (before library: items point at jobs)

CREATE TABLE research_jobs (
  id uuid PRIMARY KEY DEFAULT studyhub_uuidv7(),
  workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
  created_by uuid REFERENCES users (id) ON DELETE SET NULL,
  visibility text NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'workspace')),
  mode text NOT NULL CHECK (mode IN ('topic', 'url', 'companion')),
  material_type text NOT NULL CHECK (material_type IN ('study_guide', 'learning_companion')),
  source_item_id uuid,
  query text NOT NULL,
  urls text[] NOT NULL DEFAULT '{}',
  depth text NOT NULL CHECK (depth IN ('quick', 'standard', 'deep')),
  level text NOT NULL CHECK (level IN ('beginner', 'intermediate', 'advanced')),
  goal text NOT NULL,
  status text NOT NULL CHECK (status IN ('created', 'planning', 'searching', 'reading_sources', 'checking_coverage',
                                         'synthesizing', 'review', 'publishing', 'indexing', 'ready', 'failed', 'cancelled')),
  review_state text NOT NULL DEFAULT 'draft' CHECK (review_state IN ('draft', 'research_complete')),
  progress jsonb NOT NULL DEFAULT '{}',
  outline jsonb NOT NULL DEFAULT '[]',
  content_sections jsonb NOT NULL DEFAULT '[]',
  draft text NOT NULL DEFAULT '',
  error text,
  row_version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, id)
);
CREATE INDEX research_jobs_list ON research_jobs (workspace_id, created_at DESC, id);

CREATE TABLE research_sources (
  workspace_id uuid NOT NULL,
  job_id uuid NOT NULL,
  id text NOT NULL,
  seq bigint GENERATED ALWAYS AS IDENTITY,
  title text NOT NULL,
  url text NOT NULL,
  author text,
  publisher text,
  published_at timestamptz,
  retrieved_at timestamptz NOT NULL,
  source_type text NOT NULL,
  retained boolean NOT NULL,
  excerpt text NOT NULL,
  PRIMARY KEY (job_id, id),
  FOREIGN KEY (workspace_id, job_id) REFERENCES research_jobs (workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE research_resources (
  workspace_id uuid NOT NULL,
  job_id uuid NOT NULL,
  id text NOT NULL,
  seq bigint GENERATED ALWAYS AS IDENTITY,
  chapter text NOT NULL,
  title text NOT NULL,
  url text NOT NULL,
  resource_type text NOT NULL CHECK (resource_type IN ('video', 'playlist_en', 'playlist_ar', 'article_or_demo')),
  publisher text NOT NULL,
  snippet text NOT NULL DEFAULT '',
  validation_reason text NOT NULL DEFAULT '',
  selected boolean NOT NULL DEFAULT false,
  thumbnail_url text NOT NULL DEFAULT '',
  video_count integer CHECK (video_count >= 0),
  PRIMARY KEY (job_id, id),
  FOREIGN KEY (workspace_id, job_id) REFERENCES research_jobs (workspace_id, id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------- library, chat & citations

CREATE TABLE library_items (
  id uuid PRIMARY KEY DEFAULT studyhub_uuidv7(),
  workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
  created_by uuid REFERENCES users (id) ON DELETE SET NULL,
  blob_id uuid NOT NULL,
  current_revision_id uuid,
  title text NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
  title_locked boolean NOT NULL DEFAULT false,
  original_filename text NOT NULL,
  origin text NOT NULL CHECK (origin IN ('upload', 'folder_scan', 'research_book', 'learning_companion')),
  research_job_id uuid,
  output_slot smallint CHECK (output_slot >= 0),
  visibility text NOT NULL DEFAULT 'workspace' CHECK (visibility IN ('private', 'workspace')),
  state text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'trashed', 'purging', 'purged')),
  trashed_at timestamptz,
  purge_after timestamptz,
  row_version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, blob_id) REFERENCES content_blobs (workspace_id, id),
  FOREIGN KEY (workspace_id, blob_id, current_revision_id)
    REFERENCES processing_revisions (workspace_id, blob_id, id) ON DELETE SET NULL (current_revision_id),
  -- The item is the single authority for "which job produced me"; the job holds no pointer back.
  FOREIGN KEY (workspace_id, research_job_id) REFERENCES research_jobs (workspace_id, id),
  UNIQUE (workspace_id, id),
  UNIQUE (workspace_id, id, blob_id),
  UNIQUE (research_job_id, output_slot),
  CHECK ((origin IN ('research_book', 'learning_companion')) = (research_job_id IS NOT NULL)),
  CHECK ((research_job_id IS NULL) = (output_slot IS NULL)),
  CHECK ((state = 'active') = (trashed_at IS NULL))
);
-- One live copy of the same bytes per workspace. Restoring into a conflict is a 409.
CREATE UNIQUE INDEX library_items_one_live_per_blob ON library_items (workspace_id, blob_id) WHERE state = 'active';
CREATE INDEX library_items_list ON library_items (workspace_id, created_at DESC, id) WHERE state = 'active';
CREATE INDEX library_items_blob ON library_items (blob_id);

ALTER TABLE research_jobs ADD FOREIGN KEY (workspace_id, source_item_id)
  REFERENCES library_items (workspace_id, id) ON DELETE SET NULL (source_item_id);

CREATE TABLE item_derivations (
  workspace_id uuid NOT NULL,
  item_id uuid NOT NULL,
  source_item_id uuid NOT NULL,
  PRIMARY KEY (item_id, source_item_id),
  FOREIGN KEY (workspace_id, item_id) REFERENCES library_items (workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, source_item_id) REFERENCES library_items (workspace_id, id) ON DELETE CASCADE,
  CHECK (item_id <> source_item_id)
);

CREATE TABLE study_sessions (
  id uuid PRIMARY KEY DEFAULT studyhub_uuidv7(),
  workspace_id uuid NOT NULL,
  created_by uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  library_item_id uuid NOT NULL,
  visibility text NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'workspace')),
  page integer NOT NULL DEFAULT 1 CHECK (page >= 1),
  model text NOT NULL DEFAULT 'studyhub-auto',
  mode text NOT NULL DEFAULT 'guided' CHECK (mode IN ('guided', 'socratic', 'deep-dive', 'practical')),
  ui_state jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(ui_state) = 'object'),
  row_version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, library_item_id) REFERENCES library_items (workspace_id, id) ON DELETE CASCADE,
  UNIQUE (workspace_id, id)
);
CREATE INDEX study_sessions_owner ON study_sessions (workspace_id, created_by, updated_at DESC);
CREATE INDEX study_sessions_item ON study_sessions (library_item_id, created_by, updated_at DESC);

CREATE TABLE messages (
  id uuid PRIMARY KEY DEFAULT studyhub_uuidv7(),
  seq bigint GENERATED ALWAYS AS IDENTITY,
  workspace_id uuid NOT NULL,
  session_id uuid NOT NULL,
  author_id uuid REFERENCES users (id) ON DELETE SET NULL,
  role text NOT NULL CHECK (role IN ('user', 'assistant')),
  content text NOT NULL,
  status text NOT NULL CHECK (status IN ('generating', 'complete', 'cancelled', 'failed')),
  model text,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, session_id) REFERENCES study_sessions (workspace_id, id) ON DELETE CASCADE,
  UNIQUE (workspace_id, id)
);
CREATE INDEX messages_session ON messages (session_id, seq);
-- One reply in flight per session, across every API replica.
CREATE UNIQUE INDEX messages_one_generating ON messages (session_id) WHERE status = 'generating';

CREATE TABLE citations (
  message_id uuid NOT NULL,
  ordinal smallint NOT NULL CHECK (ordinal >= 1),
  workspace_id uuid NOT NULL,
  library_item_id uuid NOT NULL,
  blob_id uuid NOT NULL,
  revision_id uuid,
  chunk_id bigint,
  page integer CHECK (page >= 1),
  label_snapshot text NOT NULL,
  PRIMARY KEY (message_id, ordinal),
  FOREIGN KEY (workspace_id, message_id) REFERENCES messages (workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, library_item_id, blob_id) REFERENCES library_items (workspace_id, id, blob_id) ON DELETE CASCADE,
  -- No cascade: a revision or chunk cannot disappear while an answer still cites it.
  FOREIGN KEY (workspace_id, blob_id, revision_id) REFERENCES processing_revisions (workspace_id, blob_id, id),
  FOREIGN KEY (revision_id, chunk_id) REFERENCES chunks (revision_id, id),
  CHECK (chunk_id IS NULL OR revision_id IS NOT NULL)
);
CREATE INDEX citations_item ON citations (library_item_id);

-- ---------------------------------------------------------------- learning spaces

CREATE TABLE learning_sessions (
  id uuid PRIMARY KEY DEFAULT studyhub_uuidv7(),
  workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
  created_by uuid REFERENCES users (id) ON DELETE SET NULL,
  visibility text NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'workspace')),
  title text NOT NULL CHECK (length(title) BETWEEN 1 AND 160),
  learning_goal text NOT NULL,
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'ready')),
  notebook jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(notebook) = 'array'),
  notebook_schema_version integer NOT NULL DEFAULT 1,
  row_version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, id)
);
CREATE INDEX learning_sessions_list ON learning_sessions (workspace_id, updated_at DESC, id);

CREATE TABLE learning_materials (
  workspace_id uuid NOT NULL,
  session_id uuid NOT NULL,
  library_item_id uuid NOT NULL,
  material_type text NOT NULL,
  source_url text,
  timestamp_start text,
  timestamp_end text,
  PRIMARY KEY (session_id, library_item_id),
  UNIQUE (workspace_id, session_id, library_item_id),
  FOREIGN KEY (workspace_id, session_id) REFERENCES learning_sessions (workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, library_item_id) REFERENCES library_items (workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE learning_themes (
  id uuid PRIMARY KEY DEFAULT studyhub_uuidv7(),
  workspace_id uuid NOT NULL,
  session_id uuid NOT NULL,
  title text NOT NULL,
  summary text NOT NULL DEFAULT '',
  position integer NOT NULL CHECK (position >= 0),
  lesson text NOT NULL DEFAULT '',
  exercise text NOT NULL DEFAULT '',
  citations jsonb NOT NULL DEFAULT '[]',
  resources jsonb NOT NULL DEFAULT '[]',
  FOREIGN KEY (workspace_id, session_id) REFERENCES learning_sessions (workspace_id, id) ON DELETE CASCADE,
  UNIQUE (workspace_id, id),
  UNIQUE (session_id, id)
);

CREATE TABLE learning_theme_chapters (
  workspace_id uuid NOT NULL,
  session_id uuid NOT NULL,
  theme_id uuid NOT NULL,
  library_item_id uuid NOT NULL,
  chapter_key text NOT NULL,
  revision_id uuid,
  section_id bigint,
  match_status text NOT NULL CHECK (match_status IN ('matched', 'unresolved')),
  title text NOT NULL,
  page_start integer NOT NULL CHECK (page_start >= 1),
  page_end integer,
  position integer NOT NULL CHECK (position >= 0),
  done boolean NOT NULL DEFAULT false,
  done_at timestamptz,
  PRIMARY KEY (theme_id, library_item_id, chapter_key),
  FOREIGN KEY (session_id, theme_id) REFERENCES learning_themes (session_id, id) ON DELETE CASCADE,
  -- The chapter's book must be one of this space's materials, in the same workspace.
  FOREIGN KEY (workspace_id, session_id, library_item_id)
    REFERENCES learning_materials (workspace_id, session_id, library_item_id) ON DELETE CASCADE,
  FOREIGN KEY (revision_id, section_id) REFERENCES sections (revision_id, id),
  CHECK (match_status = 'unresolved' OR section_id IS NOT NULL),
  CHECK (done = (done_at IS NOT NULL))
);
CREATE INDEX learning_theme_chapters_session ON learning_theme_chapters (session_id, position);

CREATE TABLE exercise_answers (
  id uuid PRIMARY KEY DEFAULT studyhub_uuidv7(),
  workspace_id uuid NOT NULL,
  session_id uuid NOT NULL,
  author_id uuid REFERENCES users (id) ON DELETE CASCADE,
  block_id text NOT NULL,
  notebook_version integer NOT NULL DEFAULT 1,
  answer text NOT NULL,
  feedback text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, session_id) REFERENCES learning_sessions (workspace_id, id) ON DELETE CASCADE
);
CREATE INDEX exercise_answers_session ON exercise_answers (session_id, created_at);

-- ---------------------------------------------------------------- jobs & operations

CREATE TABLE jobs (
  id uuid PRIMARY KEY DEFAULT studyhub_uuidv7(),
  workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
  requested_by uuid REFERENCES users (id) ON DELETE SET NULL,
  queue text NOT NULL CHECK (queue IN ('ingest', 'research', 'purge')),
  kind text NOT NULL,
  target_type text NOT NULL CHECK (target_type IN ('revision', 'research_job', 'library_item', 'blob', 'workspace', 'user')),
  target_id uuid NOT NULL,
  target_generation integer NOT NULL DEFAULT 0,
  dedupe_key text,
  payload jsonb NOT NULL DEFAULT '{}',
  payload_version smallint NOT NULL DEFAULT 1,
  state text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
  stage text,
  priority smallint NOT NULL DEFAULT 100,
  attempts smallint NOT NULL DEFAULT 0,
  max_attempts smallint NOT NULL DEFAULT 5 CHECK (max_attempts >= 1),
  available_at timestamptz NOT NULL DEFAULT now(),
  lease_owner text,
  lease_token uuid,
  lease_expires_at timestamptz,
  cancel_requested boolean NOT NULL DEFAULT false,
  error_code text CHECK (error_code IN ('transient', 'permanent', 'superseded', 'cancelled')),
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  CHECK ((state = 'running') = (lease_token IS NOT NULL)),
  CHECK (attempts <= max_attempts)
);
CREATE UNIQUE INDEX jobs_dedupe_open ON jobs (dedupe_key) WHERE state IN ('queued', 'running');
CREATE INDEX jobs_claim ON jobs (queue, priority, available_at) WHERE state = 'queued';
CREATE INDEX jobs_expired ON jobs (queue, lease_expires_at) WHERE state = 'running';
CREATE INDEX jobs_target ON jobs (target_type, target_id, created_at DESC);

CREATE TABLE job_attempts (
  job_id uuid NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
  attempt_no smallint NOT NULL,
  lease_token uuid NOT NULL UNIQUE,
  worker text NOT NULL,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  outcome text NOT NULL DEFAULT 'running' CHECK (outcome IN ('running', 'published', 'superseded', 'failed', 'cancelled')),
  error text,
  output_key text,
  PRIMARY KEY (job_id, attempt_no)
);

CREATE TABLE idempotency_keys (
  actor_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  key text NOT NULL CHECK (length(key) BETWEEN 8 AND 200),
  workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
  operation text NOT NULL,
  request_hash bytea NOT NULL,
  status text NOT NULL DEFAULT 'in_progress' CHECK (status IN ('in_progress', 'done')),
  response_status smallint,
  response jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  PRIMARY KEY (actor_id, key)
);

CREATE TABLE deletion_operations (
  id uuid PRIMARY KEY DEFAULT studyhub_uuidv7(),
  target_type text NOT NULL CHECK (target_type IN ('user', 'workspace', 'library_item', 'blob')),
  target_id uuid NOT NULL,
  target_generation integer NOT NULL DEFAULT 0,
  stage text NOT NULL DEFAULT 'revoke' CHECK (stage IN ('revoke', 'rows', 'objects', 'verify', 'done')),
  state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'running', 'done', 'failed')),
  attempts smallint NOT NULL DEFAULT 0,
  progress jsonb NOT NULL DEFAULT '{}',
  requested_by uuid,
  not_before timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  UNIQUE (target_type, target_id, target_generation)
);

-- Append-only: ids only, no foreign keys, so records outlive what they describe.
CREATE TABLE audit_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  workspace_id uuid,
  actor_id uuid,
  action text NOT NULL,
  target_type text,
  target_id uuid,
  metadata jsonb NOT NULL DEFAULT '{}',
  ip inet,
  at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_events_workspace ON audit_events (workspace_id, at DESC);
CREATE INDEX audit_events_actor ON audit_events (actor_id, at DESC);

CREATE TABLE usage_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  workspace_id uuid NOT NULL,
  kind text NOT NULL CHECK (kind IN ('pages_parsed', 'chunks_embedded', 'tokens', 'storage_bytes')),
  quantity bigint NOT NULL,
  job_id uuid,
  at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX usage_events_workspace ON usage_events (workspace_id, at DESC);

-- ---------------------------------------------------------------- updated_at triggers

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['users', 'auth_identities', 'workspaces', 'memberships', 'tutor_providers',
    'user_settings', 'content_blobs', 'upload_sessions', 'processing_revisions', 'embedding_sets',
    'research_jobs', 'library_items', 'study_sessions', 'learning_sessions', 'jobs', 'deletion_operations']
  LOOP
    EXECUTE format('CREATE TRIGGER touch_updated_at BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION touch_updated_at()', t);
  END LOOP;
END $$;

-- ---------------------------------------------------------------- row-level security

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['workspace_limits', 'content_blobs', 'upload_sessions', 'processing_revisions',
    'sections', 'chunks', 'embedding_sets', 'embeddings', 'library_items', 'item_derivations', 'messages',
    'citations', 'research_sources', 'research_resources', 'learning_materials', 'learning_themes',
    'learning_theme_chapters', 'exercise_answers', 'idempotency_keys', 'usage_events']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant ON %I USING (workspace_id = app_workspace()) WITH CHECK (workspace_id = app_workspace())', t);
  END LOOP;
  -- Private-by-default resources: members of the workspace see only their own unless shared.
  FOREACH t IN ARRAY ARRAY['study_sessions', 'research_jobs', 'learning_sessions']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant ON %I USING (workspace_id = app_workspace() AND (visibility = ''workspace'' OR created_by = app_user())) WITH CHECK (workspace_id = app_workspace())', t);
  END LOOP;
END $$;

ALTER TABLE tutor_providers ENABLE ROW LEVEL SECURITY;
ALTER TABLE tutor_providers FORCE ROW LEVEL SECURITY;
CREATE POLICY visible ON tutor_providers FOR SELECT USING (scope = 'system' OR workspace_id = app_workspace());
CREATE POLICY write_own ON tutor_providers FOR INSERT WITH CHECK (scope = 'workspace' AND workspace_id = app_workspace());
CREATE POLICY update_own ON tutor_providers FOR UPDATE USING (workspace_id = app_workspace()) WITH CHECK (workspace_id = app_workspace());
CREATE POLICY delete_own ON tutor_providers FOR DELETE USING (workspace_id = app_workspace());

ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_events FORCE ROW LEVEL SECURITY;
CREATE POLICY read_own ON audit_events FOR SELECT USING (workspace_id = app_workspace());
CREATE POLICY append ON audit_events FOR INSERT WITH CHECK (workspace_id IS NULL OR workspace_id = app_workspace());

-- The worker is the only role that sees every workspace's queue; everything it then
-- touches goes through the same tenant policies with that job's workspace set.
ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON jobs TO studyhub_app USING (workspace_id = app_workspace()) WITH CHECK (workspace_id = app_workspace());
CREATE POLICY worker_all ON jobs TO studyhub_worker USING (true) WITH CHECK (true);

-- ---------------------------------------------------------------- privileges

GRANT USAGE ON SCHEMA public TO studyhub_app, studyhub_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO studyhub_app, studyhub_worker;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO studyhub_app, studyhub_worker;
REVOKE UPDATE, DELETE ON audit_events, usage_events FROM studyhub_app, studyhub_worker;
REVOKE ALL ON job_attempts FROM studyhub_app;
REVOKE DELETE ON deletion_operations FROM studyhub_app;
REVOKE INSERT, UPDATE, DELETE ON embedding_profiles FROM studyhub_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO studyhub_app, studyhub_worker;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO studyhub_app, studyhub_worker;
