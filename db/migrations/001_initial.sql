-- ScammingScammingScammers — initial schema.
--
-- Postgres is the database, the job queue, and the realtime bus. At one call at a
-- time and a handful of events per second, adding Kafka or Redis would buy nothing
-- and cost a component that can break at three in the morning.
--
-- The enum values below mirror ssscammers/shared/enums.py by hand, and a test
-- fails the build if the two drift apart. Edit the Python, then edit here.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- Vocabulary. Mirrors ssscammers/shared/enums.py exactly.
-- ---------------------------------------------------------------------------

CREATE TYPE call_status AS ENUM (
    'ringing', 'in_progress', 'completed', 'failed', 'enriching', 'enriched'
);

CREATE TYPE end_reason AS ENUM (
    'caller_hangup', 'agent_hangup', 'max_duration', 'dead_air', 'watchdog_kill',
    'disclosed_exit', 'emergency_exit', 'spend_cap', 'pipeline_error', 'twilio_error'
);

CREATE TYPE entry_path AS ENUM ('direct', 'conditional_forward', 'unknown');

CREATE TYPE turn_role AS ENUM ('caller', 'agent');

CREATE TYPE caller_class AS ENUM (
    'unknown', 'scammer', 'lead_gen', 'robocall', 'legit', 'blocked'
);

CREATE TYPE triage_class AS ENUM (
    'unclear', 'scam', 'robocall', 'lead_gen', 'legit_business', 'legit_personal',
    'victim_callback', 'silence'
);

CREATE TYPE scam_type AS ENUM (
    'unknown', 'irs_tax', 'ssa_benefits', 'law_enforcement', 'tech_support',
    'bank_fraud_dept', 'card_otp_verification', 'refund_overpayment', 'gift_card',
    'crypto_investment', 'grandparent', 'medicare', 'auto_warranty',
    'utility_shutoff', 'delivery_package', 'other'
);

CREATE TYPE call_phase AS ENUM (
    'greeting', 'assessing', 'hook', 'stall', 'wind_down',
    'disclose_exit', 'emergency_exit', 'terminate'
);

CREATE TYPE caller_kind AS ENUM ('unknown', 'live_human', 'recording', 'ivr');

CREATE TYPE tactic AS ENUM (
    'none', 'mishear', 'read_back', 'fumble_data', 'tangent', 'hold_on',
    'tech_illiteracy', 'eager_nonconvergence', 'repeat_request'
);

CREATE TYPE label_source AS ENUM ('auto', 'manual');

-- ---------------------------------------------------------------------------
-- personas: prompts are versioned here so a historical call always links to the
-- exact text that produced it. Editing a persona inserts a row; it never mutates
-- one, or last month's transcripts stop making sense.
-- ---------------------------------------------------------------------------
CREATE TABLE personas (
    id              text        NOT NULL,
    prompt_version  int         NOT NULL DEFAULT 1,
    display_name    text        NOT NULL,
    description     text        NOT NULL DEFAULT '',
    system_prompt   text        NOT NULL,
    tts_provider    text        NOT NULL,
    tts_voice_id    text        NOT NULL,
    active          boolean     NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    retired_at      timestamptz,
    PRIMARY KEY (id, prompt_version)
);

-- ---------------------------------------------------------------------------
-- callers: one row per number. Reputation accumulates so a repeat scammer skips
-- probation and an allowlisted neighbour is never baited twice.
-- ---------------------------------------------------------------------------
CREATE TABLE callers (
    id                   uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    phone_e164           text         UNIQUE,   -- NULL when caller ID is withheld
    first_seen_at        timestamptz  NOT NULL DEFAULT now(),
    last_seen_at         timestamptz  NOT NULL DEFAULT now(),
    call_count           int          NOT NULL DEFAULT 0,
    total_seconds_wasted int          NOT NULL DEFAULT 0,
    classification       caller_class NOT NULL DEFAULT 'unknown',
    label_source         label_source NOT NULL DEFAULT 'auto',
    lookup_data          jsonb,                 -- Twilio Lookup: line type, carrier, CNAM
    notes                text
);
CREATE INDEX callers_classification_idx ON callers (classification);

-- ---------------------------------------------------------------------------
-- calls: the spine.
-- ---------------------------------------------------------------------------
CREATE TABLE calls (
    id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    twilio_call_sid     text        UNIQUE NOT NULL,
    caller_id           uuid        REFERENCES callers(id),
    persona_id          text,
    persona_version     int,
    entry_path          entry_path  NOT NULL DEFAULT 'unknown',
    status              call_status NOT NULL DEFAULT 'ringing',
    end_reason          end_reason,
    final_phase         call_phase,

    started_at          timestamptz NOT NULL,   -- webhook received
    answered_at         timestamptz,            -- media stream up, greeting done
    ended_at            timestamptz,
    -- Wasted time is measured from answer, not from ring: nobody is inconvenienced
    -- by a phone ringing.
    duration_seconds    int GENERATED ALWAYS AS (
                            EXTRACT(EPOCH FROM (ended_at - answered_at))::int
                        ) STORED,

    recording_sid       text,
    audio_object_key    text,
    playback_object_key text,
    audio_deleted_at    timestamptz,

    turn_count          int,
    caller_talk_seconds numeric(8,2),
    agent_talk_seconds  numeric(8,2),

    -- Two latency figures, deliberately. The filler noise starts ~100ms after the
    -- caller stops talking and would make every call look instant; the second
    -- column is the one ops health keys on.
    avg_time_to_first_audio_ms      int,
    avg_time_to_substantive_reply_ms int,

    cost_usd            jsonb,      -- {"twilio":..,"stt":..,"llm":..,"tts":..}

    flagged_legit       boolean     NOT NULL DEFAULT false,
    reviewed_at         timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),

    FOREIGN KEY (persona_id, persona_version) REFERENCES personas(id, prompt_version)
);
CREATE INDEX calls_started_idx ON calls (started_at DESC);
CREATE INDEX calls_caller_idx  ON calls (caller_id, started_at DESC);
CREATE INDEX calls_persona_idx ON calls (persona_id, started_at DESC);
CREATE INDEX calls_review_idx  ON calls (flagged_legit)
    WHERE flagged_legit AND reviewed_at IS NULL;

-- ---------------------------------------------------------------------------
-- call_events: append-only log. Everything else is derivable from it, and it is
-- what the live dashboard replays when a browser joins a call mid-flight.
-- ---------------------------------------------------------------------------
CREATE TABLE call_events (
    id      bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    call_id uuid        NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    seq     int         NOT NULL,   -- per-call, assigned by the pipeline
    ts      timestamptz NOT NULL,
    type    text        NOT NULL,
    payload jsonb       NOT NULL DEFAULT '{}',
    UNIQUE (call_id, seq)
);
CREATE INDEX call_events_call_idx ON call_events (call_id, seq);

-- ---------------------------------------------------------------------------
-- turns: word timings are what let the dashboard highlight the transcript in
-- time with the audio.
-- ---------------------------------------------------------------------------
CREATE TABLE turns (
    id             uuid      PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id        uuid      NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    turn_index     int       NOT NULL,
    role           turn_role NOT NULL,
    text           text      NOT NULL,
    audio_start_ms int,
    audio_end_ms   int,
    words          jsonb,    -- [{w, start_ms, end_ms}]
    stt_confidence real,

    time_to_first_audio_ms       int,   -- filler; not a health signal
    time_to_substantive_reply_ms int,   -- the real one

    interrupted    boolean   NOT NULL DEFAULT false,
    phase          call_phase,
    tactic         tactic,   -- realtime guess; enrichment overwrites authoritatively
    caller_kind    caller_kind DEFAULT 'unknown',
    filtered       boolean   NOT NULL DEFAULT false,  -- pre-TTS filter replaced this
    UNIQUE (call_id, turn_index)
);
CREATE INDEX turns_call_idx ON turns (call_id, turn_index);

-- ---------------------------------------------------------------------------
-- transcripts: denormalised text, for search only. `turns` stays the truth.
-- ---------------------------------------------------------------------------
CREATE TABLE transcripts (
    call_id   uuid     PRIMARY KEY REFERENCES calls(id) ON DELETE CASCADE,
    full_text text     NOT NULL,
    tsv       tsvector GENERATED ALWAYS AS (to_tsvector('english', full_text)) STORED
);
CREATE INDEX transcripts_tsv_idx ON transcripts USING GIN (tsv);

-- ---------------------------------------------------------------------------
-- triage_observations: why a call was classified the way it was. Kept because
-- the misroute review queue is useless without an explanation.
-- ---------------------------------------------------------------------------
CREATE TABLE triage_observations (
    id          bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    call_id     uuid         NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    at_seconds  numeric(8,2) NOT NULL,
    triage      triage_class NOT NULL,
    confidence  real         NOT NULL,
    scam_type   scam_type    NOT NULL DEFAULT 'unknown',
    explanation text
);
CREATE INDEX triage_call_idx ON triage_observations (call_id, at_seconds);

-- ---------------------------------------------------------------------------
-- scam_classifications / call_enrichments: written after the call by the batch
-- enrichment worker, which has the whole transcript and no latency budget.
-- ---------------------------------------------------------------------------
CREATE TABLE scam_classifications (
    call_id          uuid      PRIMARY KEY REFERENCES calls(id) ON DELETE CASCADE,
    is_scam          boolean   NOT NULL,
    scam_type        scam_type NOT NULL DEFAULT 'unknown',
    script_family    text,
    script_signature jsonb,
    confidence       real      NOT NULL,
    rationale        text,
    classifier_model text      NOT NULL,
    classified_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX scam_type_idx ON scam_classifications (scam_type);

CREATE TABLE call_enrichments (
    call_id                   uuid PRIMARY KEY REFERENCES calls(id) ON DELETE CASCADE,
    summary                   text NOT NULL,
    title                     text NOT NULL,
    funniest_moment           jsonb,
    highlights                jsonb,
    sentiment_timeline        jsonb,
    frustration_final         real,
    frustration_peak          real,
    persona_believability     real,
    tactic_tags               jsonb,
    human_attended            boolean,
    first_live_human_turn     int,
    human_time_wasted_seconds int  NOT NULL DEFAULT 0,
    misrouted_suspected       boolean NOT NULL DEFAULT false,
    misrouted_reason          text,
    batch_id                  text,
    model                     text NOT NULL,
    usage                     jsonb,
    enriched_at               timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- jobs: the whole queue. SELECT ... FOR UPDATE SKIP LOCKED is enough here.
-- ---------------------------------------------------------------------------
CREATE TABLE jobs (
    id           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind         text        NOT NULL,
    payload      jsonb       NOT NULL DEFAULT '{}',
    run_after    timestamptz NOT NULL DEFAULT now(),
    attempts     int         NOT NULL DEFAULT 0,
    locked_until timestamptz,
    done_at      timestamptz,
    last_error   text
);
CREATE INDEX jobs_ready_idx ON jobs (run_after) WHERE done_at IS NULL;

-- ---------------------------------------------------------------------------
-- metrics_daily: nightly rollup. Reclassifying a call as legit has to zero its
-- contribution everywhere, so aggregates are recomputed from base tables rather
-- than incremented by triggers.
-- ---------------------------------------------------------------------------
CREATE TABLE metrics_daily (
    day                   date NOT NULL,
    persona_id            text NOT NULL DEFAULT '_all',
    scam_type             text NOT NULL DEFAULT '_all',
    calls                 int  NOT NULL DEFAULT 0,
    scam_calls            int  NOT NULL DEFAULT 0,
    seconds_wasted        int  NOT NULL DEFAULT 0,
    human_seconds_wasted  int  NOT NULL DEFAULT 0,
    longest_call_seconds  int  NOT NULL DEFAULT 0,
    avg_frustration_final real,
    PRIMARY KEY (day, persona_id, scam_type)
);

-- ---------------------------------------------------------------------------
-- settings: retention knobs and operational caps, editable from the dashboard.
-- ---------------------------------------------------------------------------
CREATE TABLE settings (
    key        text        PRIMARY KEY,
    value      jsonb       NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO settings (key, value) VALUES
    -- Scam audio is the trophy room; storage is negligible.
    ('retention.scam_audio_days',        '0'),
    -- A real person who reached this line by accident does not belong in a scam
    -- archive. This is the most important knob in the table.
    ('retention.legit_audio_days',       '7'),
    ('retention.unreviewed_flagged_days','30'),
    ('retention.event_log_days',         '365'),
    ('retention.twilio_purge',           'true'),
    ('system.enabled',                   'true')
ON CONFLICT (key) DO NOTHING;

-- ---------------------------------------------------------------------------
-- The headline metric, defined once so the dashboard and the rollup cannot
-- disagree: connected time on a confirmed scam call that was not a misroute.
-- No subtraction for holds or silence — an occupied line is an occupied line.
-- ---------------------------------------------------------------------------
CREATE VIEW wasted_time AS
SELECT
    c.id                AS call_id,
    c.persona_id,
    c.started_at,
    c.duration_seconds,
    COALESCE(sc.scam_type, 'unknown')::scam_type AS scam_type,
    CASE
        WHEN sc.is_scam AND sc.confidence >= 0.7 AND NOT c.flagged_legit
        THEN COALESCE(c.duration_seconds, 0)
        ELSE 0
    END AS scammer_seconds_wasted,
    COALESCE(e.human_time_wasted_seconds, 0) AS human_seconds_wasted
FROM calls c
LEFT JOIN scam_classifications sc ON sc.call_id = c.id
LEFT JOIN call_enrichments     e  ON e.call_id  = c.id;

COMMIT;
