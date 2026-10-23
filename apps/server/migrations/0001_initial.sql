-- The complete schema for plain Postgres (16+): the original app's Supabase
-- migrations squashed, without auth, RLS, grants, storage or removed features.
--
-- Applied by the runner (python -m redream_api.db.migrate) inside one
-- transaction, together with its schema_migrations row; never edit a file
-- once it has been applied anywhere: add the next numbered one instead.
--
-- Mirrors redream_api.db.models exactly (names of indexes and constraints
-- included); tests/pg compares the two on a real Postgres.

-- =========================================================================
-- users: the account (login email, argon2id hash) and the profile fields
-- =========================================================================
CREATE TABLE users (
    id                  UUID PRIMARY KEY,
    -- Stored stripped and lower-cased; unique case-insensitively (index below).
    email               TEXT NOT NULL,
    password_hash       TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    gender              TEXT,
    age_range           TEXT,
    nightmare_frequency TEXT
);

CREATE UNIQUE INDEX users_email_lower_key ON users (lower(email));

-- =========================================================================
-- auth_tokens: one signed-in session, by the sha256 hex of its bearer token
-- =========================================================================
CREATE TABLE auth_tokens (
    token_hash   VARCHAR(64) PRIMARY KEY,
    user_id      UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ NOT NULL,
    expires_at   TIMESTAMPTZ NOT NULL
);

CREATE INDEX ix_auth_tokens_user_id ON auth_tokens (user_id);

-- =========================================================================
-- user_consents: append-only consent proof log (GDPR Art. 9(2)(a), 7(1));
-- the latest row per (user, consent_key) is the current status
-- =========================================================================
CREATE TABLE user_consents (
    id          BIGSERIAL PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    consent_key TEXT NOT NULL DEFAULT 'health_data_processing',
    version     TEXT NOT NULL,
    granted     BOOLEAN NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_user_consents_user_id ON user_consents (user_id);

-- =========================================================================
-- chats + chat_messages: one IRT conversation and its turns
-- =========================================================================
CREATE TABLE chats (
    id                      BIGSERIAL PRIMARY KEY,
    user_id                 UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    language                TEXT NOT NULL DEFAULT 'en'
        CONSTRAINT chats_language_check CHECK (language IN ('en', 'de')),
    -- Set when a crisis message switched `language`; cleared by the next AI turn.
    language_switch_pending BOOLEAN NOT NULL DEFAULT false,
    current_stage           TEXT NOT NULL DEFAULT 'recording'
        CONSTRAINT chats_current_stage_check
        CHECK (current_stage IN ('recording', 'intensity', 'rewriting', 'summary', 'final')),
    is_done                 BOOLEAN NOT NULL DEFAULT false,
    is_nightmare            BOOLEAN,
    title                   TEXT,
    -- Generated once by the AI, echoed into every later AI call (ADR 0002).
    nightmare_summary       TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_chats_user_id ON chats (user_id);
CREATE INDEX ix_chats_created_at ON chats (created_at);

CREATE TABLE chat_messages (
    id             UUID PRIMARY KEY,
    chat_id        BIGINT NOT NULL REFERENCES chats (id) ON DELETE CASCADE,
    role           TEXT NOT NULL
        CONSTRAINT chat_messages_role_check CHECK (role IN ('user', 'assistant')),
    content        TEXT NOT NULL,
    stage          TEXT
        CONSTRAINT chat_messages_stage_check
        CHECK (stage IN ('recording', 'intensity', 'rewriting', 'summary', 'final')),
    -- The crisis check flagged this user message (ADR 0005).
    safety_flagged BOOLEAN NOT NULL DEFAULT false,
    -- Which prompt revision wrote this assistant message.
    prompt_version TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_chat_messages_chat_id ON chat_messages (chat_id);
CREATE INDEX ix_chat_messages_created_at ON chat_messages (created_at);

-- =========================================================================
-- dreams: the journal (manual entries and chat summaries) and the rehearsal
-- =========================================================================
CREATE TABLE dreams (
    id                     BIGSERIAL PRIMARY KEY,
    user_id                UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    title                  TEXT NOT NULL,
    original_dream         TEXT NOT NULL,
    rewritten_dream        TEXT,
    -- The 0-10 rating (app, chat summary or PUT /chats/{id}/intensity).
    intensity              INTEGER
        CONSTRAINT dreams_intensity_check CHECK (intensity BETWEEN 0 AND 10),
    is_nightmare           BOOLEAN NOT NULL DEFAULT false,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_edited            TIMESTAMPTZ NOT NULL DEFAULT now(),
    practice_timestamps    JSONB NOT NULL DEFAULT '[]',
    is_current_dream       BOOLEAN NOT NULL DEFAULT false,
    image_url              TEXT,
    session_id             TEXT,
    source                 TEXT NOT NULL DEFAULT 'manual'
        CONSTRAINT dreams_source_check CHECK (source IN ('manual', 'chatbot')),
    -- The chat's language at the summary, or the app's at creation; NULL for
    -- manual dreams created without one.
    language               TEXT
        CONSTRAINT dreams_language_check CHECK (language IN ('en', 'de')),
    -- Store key under AUDIO_DIR; the API serves it as a short-lived signed link.
    rehearsal_audio_key    TEXT,
    rehearsal_script       TEXT,
    rehearsal_duration     TEXT,
    rehearsal_language     TEXT
        CONSTRAINT dreams_rehearsal_language_check CHECK (rehearsal_language IN ('en', 'de')),
    rehearsal_generated_at TIMESTAMPTZ
);

CREATE INDEX ix_dreams_user_id ON dreams (user_id);
CREATE INDEX ix_dreams_created_at ON dreams (created_at);
CREATE INDEX ix_dreams_session_id ON dreams (session_id);
CREATE INDEX ix_dreams_source ON dreams (source);

-- =========================================================================
-- daily_moods: one mood per user and day
-- =========================================================================
CREATE TABLE daily_moods (
    id           BIGSERIAL PRIMARY KEY,
    user_id      UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    mood_date    DATE NOT NULL,
    emotion      TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_updated TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT daily_moods_user_id_mood_date_key UNIQUE (user_id, mood_date)
);

CREATE INDEX ix_daily_moods_user_id ON daily_moods (user_id);
CREATE INDEX ix_daily_moods_mood_date ON daily_moods (mood_date);
