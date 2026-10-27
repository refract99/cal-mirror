CREATE TABLE state (
    name text PRIMARY KEY,
    data jsonb NOT NULL
);

CREATE TABLE rules (
    position int PRIMARY KEY,
    id int NOT NULL,
    kind text,
    value text,
    calendar_id text
);

CREATE TABLE calendars (
    position int,
    id text PRIMARY KEY,
    summary text,
    access_role text,
    is_primary boolean
);

CREATE TABLE sync_tokens (
    calendar_id text PRIMARY KEY,
    token text NOT NULL
);

CREATE TABLE links (
    source text PRIMARY KEY,
    kind text,
    calendar_id text,
    event_id text,
    start text,
    rule_id int
);

CREATE TABLE placements (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at timestamptz,
    source text,
    calendar_id text,
    title text,
    organizer_email text,
    guest_domains text[]
);

CREATE TABLE notice_state (
    source text PRIMARY KEY,
    state jsonb
);

CREATE TABLE requests (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind text,
    state text,
    params jsonb,
    result jsonb,
    created timestamptz DEFAULT now()
);

CREATE TABLE secrets (
    name text PRIMARY KEY,
    token text NOT NULL
);

CREATE TABLE sessions (
    token_hash text PRIMARY KEY,
    expires timestamptz NOT NULL
);

CREATE TABLE meta (
    key text PRIMARY KEY,
    value text NOT NULL
);
