PRAGMA journal_mode = WAL;

-- Every fetched document version, as received. Content hashes decide change.
CREATE TABLE IF NOT EXISTS raw_version (
    cs_type TEXT NOT NULL,
    doc_id TEXT NOT NULL,            -- csId: one per document version
    entity_key TEXT NOT NULL,        -- notificationId, body ref, legislation id, reference id
    version INTEGER,
    sha256 TEXT NOT NULL,
    json_doc TEXT NOT NULL,
    es_sort_date TEXT,               -- UTC
    ingest_date TEXT,                -- UTC
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (cs_type, doc_id, sha256)
);
CREATE INDEX IF NOT EXISTS raw_version_entity ON raw_version (cs_type, entity_key);

CREATE TABLE IF NOT EXISTS body (
    body_ref TEXT PRIMARY KEY,       -- organisation reference + body type, e.g. EPOS_43978NB
    body_number INTEGER,
    body_type TEXT,
    body_type_id INTEGER,
    organisation_id INTEGER,
    organisation_ref TEXT,
    organisation_version INTEGER,
    name TEXT,
    country_id INTEGER,
    country TEXT,
    street TEXT,
    postcode_city TEXT,
    website TEXT,
    last_approval_date TEXT,
    end_date TEXT,
    in_source INTEGER NOT NULL DEFAULT 1,
    sha256 TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS body_number_idx ON body (body_number);
CREATE INDEX IF NOT EXISTS body_country_idx ON body (country_id);

-- One row per notification version. series_key = body_ref:legislation_id.
CREATE TABLE IF NOT EXISTS notification (
    notification_id INTEGER PRIMARY KEY,
    series_key TEXT NOT NULL,
    body_ref TEXT NOT NULL,
    body_number INTEGER,
    legislation_id INTEGER NOT NULL,
    version INTEGER,
    is_latest INTEGER NOT NULL,
    in_source INTEGER NOT NULL DEFAULT 1,
    status_id INTEGER,
    end_date TEXT,
    last_approval_date TEXT,
    update_date TEXT,                -- UTC, after the Brussels time fix
    accreditation TEXT,
    accreditation_expiry_date TEXT,
    template_profile TEXT,
    sha256 TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notification_series_idx ON notification (series_key, version);
CREATE INDEX IF NOT EXISTS notification_latest_idx ON notification (is_latest, legislation_id, status_id);
CREATE INDEX IF NOT EXISTS notification_body_idx ON notification (body_number, legislation_id);

CREATE TABLE IF NOT EXISTS notification_scope (
    notification_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    value_id INTEGER,
    label TEXT
);
CREATE INDEX IF NOT EXISTS notification_scope_idx ON notification_scope (notification_id, kind);

CREATE TABLE IF NOT EXISTS legislation (
    legislation_id INTEGER PRIMARY KEY,
    code TEXT,
    number TEXT,
    name TEXT,
    status TEXT,
    profile TEXT,
    end_date TEXT,
    sha256 TEXT
);

CREATE TABLE IF NOT EXISTS reference (
    cs_type TEXT NOT NULL,
    ref_id INTEGER NOT NULL,
    legislation_id INTEGER NOT NULL,
    language TEXT NOT NULL,
    label TEXT,
    PRIMARY KEY (cs_type, ref_id, legislation_id)
);

CREATE TABLE IF NOT EXISTS change_event (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,            -- notification | body
    entity_key TEXT NOT NULL,        -- series_key or body_ref
    notification_id INTEGER,
    body_ref TEXT,
    body_number INTEGER,
    legislation_id INTEGER,
    country_id INTEGER,
    change_type TEXT NOT NULL,
    field TEXT,
    old_value TEXT,
    new_value TEXT,
    source_update_date TEXT,
    observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS change_event_observed_idx ON change_event (observed_at);

CREATE TABLE IF NOT EXISTS sync_run (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    ok INTEGER NOT NULL DEFAULT 0,
    requests INTEGER NOT NULL DEFAULT 0,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
