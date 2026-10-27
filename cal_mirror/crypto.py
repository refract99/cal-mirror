"""Secret encryption with a Fernet key, and log redaction.

The environment is not read at import. Callers pass a mapping to
`cipher_from_env`. Keys and plaintext are never logged.
"""

import logging
from collections.abc import Callable, Iterable, Mapping

from cryptography.fernet import Fernet, InvalidToken

KEY_ENV = "CAL_MIRROR_SECRET_KEY"
REDACTED = "[redacted]"


def generate_key() -> str:
    return Fernet.generate_key().decode("ascii")


class Cipher:
    def __init__(self, key: str | bytes) -> None:
        try:
            self._fernet = Fernet(key)
        except (ValueError, TypeError):
            raise ValueError("invalid secret key") from None

    def encrypt(self, plain: str) -> str:
        return self._fernet.encrypt(plain.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            plain = self._fernet.decrypt(token.encode("utf-8"))
        except InvalidToken:
            raise ValueError("cannot decrypt secret") from None
        return plain.decode("utf-8")


def cipher_from_env(environ: Mapping[str, str]) -> Cipher:
    value = environ.get(KEY_ENV)
    if value is None or value == "":
        raise ValueError(f"{KEY_ENV} is not set")
    return Cipher(value)


def redact(text: str, secrets: Iterable[str]) -> str:
    ordered = sorted((item for item in secrets if item), key=len, reverse=True)
    for secret in ordered:
        text = text.replace(secret, REDACTED)
    return text


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: Callable[[], Iterable[str]]) -> None:
        super().__init__()
        self._secrets = secrets

    def filter(self, record: logging.LogRecord) -> bool:
        current = self._secrets()
        record.msg = redact(record.getMessage(), current)
        record.args = ()
        return True
