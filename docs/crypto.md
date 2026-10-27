# Crypto

`cal_mirror/crypto.py` encrypts secrets before they are stored and redacts them
in log records. The module does not read the environment at import, and it never
logs a key or plaintext.

## Environment

`KEY_ENV` is `CAL_MIRROR_SECRET_KEY`. `cipher_from_env(environ)` reads that name
from the mapping the caller passes (in `main`, `os.environ`). A missing value or
`""` raises `ValueError` with the text `CAL_MIRROR_SECRET_KEY is not set`.

Make a key (44 characters of url-safe base64):

```sh
uv run python -c "from cal_mirror.crypto import generate_key; print(generate_key())"
```

Store it in the `cal-mirror-secrets` k8s Secret under `secret-key`. The web and
worker deployments map that entry to `CAL_MIRROR_SECRET_KEY`. Keep the same key
for the life of the database; a different key cannot read what is already stored.

## `Cipher`

`Cipher(key)` accepts `str` or `bytes` and builds a `cryptography.fernet.Fernet`.
It satisfies `SecretBox`: `encrypt(plain)` returns the Fernet token as ASCII text,
and `decrypt(token)` returns the UTF-8 plaintext.

Two fixed errors, and neither one includes the key, the token or the plaintext:

- The key is not a Fernet key (`ValueError` or `TypeError` from Fernet): `invalid secret key`.
- The token was not produced with this key, or it was altered: `cannot decrypt secret`.

## Log redaction

`REDACTED` is `[redacted]`. `redact(text, secrets)` replaces every occurrence of
each non-empty secret with that marker, longest secret first, and ignores `""`.

`RedactingFilter(secrets)` is a `logging.Filter`. `secrets` is a callable
returning the current secret strings. `filter` calls it on every record, rewrites
`record.msg` with `redact`, clears `record.args`, and returns `True`.

Attach it to a handler, not only a logger, and pass the store's secrets so a
value saved after startup is still covered:

```python
def current_secrets():
    found = []
    for name in ("google_token", "totp_secret"):
        value = store.get_secret(name)
        if value:
            found.append(value)
    return found


handler.addFilter(RedactingFilter(current_secrets))
```

Include any other plaintext still in memory (a password, a TOTP secret, or
recovery codes) in that callable. Rows that wire the store and the worker attach
this filter; this module does not.
