CREATE TABLE passkeys (
    credential_id text PRIMARY KEY,
    name text NOT NULL,
    public_key bytea NOT NULL,
    sign_count bigint NOT NULL,
    created timestamptz NOT NULL
);
