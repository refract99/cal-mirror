import io
import logging

import pytest

from cal_mirror.crypto import (
    KEY_ENV,
    Cipher,
    RedactingFilter,
    cipher_from_env,
    generate_key,
    redact,
)

VALUE = "fixture-password-1"


def _capture(case: str, secrets):
    stream = io.StringIO()
    logger = logging.getLogger(f"test_crypto.{case}")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter(secrets))
    logger.addHandler(handler)
    return logger, stream


def test_round_trip():
    c = Cipher(generate_key())
    assert c.decrypt(c.encrypt(VALUE)) == VALUE


def test_bytes_key_decrypts_same_token():
    k = generate_key()
    c = Cipher(k)
    token = c.encrypt(VALUE)
    assert Cipher(k.encode("ascii")).decrypt(token) == VALUE


def test_unicode_round_trip():
    c = Cipher(generate_key())
    assert c.decrypt(c.encrypt("ünï")) == "ünï"


def test_encrypt_hides_plaintext():
    c = Cipher(generate_key())
    token = c.encrypt(VALUE)
    assert token != VALUE
    assert VALUE not in token


def test_encrypt_is_not_deterministic():
    c = Cipher(generate_key())
    assert c.encrypt("x") != c.encrypt("x")


def test_wrong_key_cannot_decrypt():
    k = generate_key()
    c = Cipher(k)
    token = c.encrypt(VALUE)
    with pytest.raises(ValueError, match=r"^cannot decrypt secret$") as exc:
        Cipher(generate_key()).decrypt(token)
    message = str(exc.value)
    assert message == "cannot decrypt secret"
    assert k not in message
    assert VALUE not in message


def test_tampered_token_cannot_decrypt():
    k = generate_key()
    c = Cipher(k)
    token = c.encrypt(VALUE)
    with pytest.raises(ValueError, match=r"^cannot decrypt secret$") as exc:
        c.decrypt(token[:-4] + "AAAA")
    message = str(exc.value)
    assert message == "cannot decrypt secret"
    assert k not in message
    assert VALUE not in message


def test_garbage_cannot_decrypt():
    k = generate_key()
    c = Cipher(k)
    with pytest.raises(ValueError, match=r"^cannot decrypt secret$") as exc:
        c.decrypt("garbage")
    message = str(exc.value)
    assert message == "cannot decrypt secret"
    assert k not in message
    assert VALUE not in message


def test_short_key_rejected():
    with pytest.raises(ValueError, match=r"^invalid secret key$") as exc:
        Cipher("short")
    assert str(exc.value) == "invalid secret key"
    assert "short" not in str(exc.value)


def test_wrong_length_key_rejected():
    bad = "!" * 44
    with pytest.raises(ValueError, match=r"^invalid secret key$") as exc:
        Cipher(bad)
    assert str(exc.value) == "invalid secret key"
    assert bad not in str(exc.value)


def test_cipher_from_env_missing():
    with pytest.raises(ValueError, match=r"^CAL_MIRROR_SECRET_KEY is not set$") as exc:
        cipher_from_env({})
    assert str(exc.value) == "CAL_MIRROR_SECRET_KEY is not set"


def test_cipher_from_env_empty():
    with pytest.raises(ValueError, match=r"^CAL_MIRROR_SECRET_KEY is not set$") as exc:
        cipher_from_env({KEY_ENV: ""})
    assert str(exc.value) == "CAL_MIRROR_SECRET_KEY is not set"


def test_cipher_from_env_bad_value():
    with pytest.raises(ValueError, match=r"^invalid secret key$") as exc:
        cipher_from_env({KEY_ENV: "short"})
    assert str(exc.value) == "invalid secret key"
    assert "short" not in str(exc.value)


def test_cipher_from_env_round_trip():
    k = generate_key()
    c = cipher_from_env({KEY_ENV: k})
    assert c.decrypt(c.encrypt(VALUE)) == VALUE


def test_redact_longest_first():
    assert redact("a abcdef abc", ["abc", "abcdef", ""]) == "a [redacted] [redacted]"


def test_redact_empty_list():
    assert redact("plain", []) == "plain"


def test_redact_ignores_empty_string():
    assert redact("x", [""]) == "x"


def test_filter_redacts_value():
    logger, stream = _capture("redact_value", lambda: [VALUE])
    logger.info("value %s here", VALUE)
    logger.info("plain " + VALUE)
    output = stream.getvalue()
    assert "value [redacted] here" in output
    assert "plain [redacted]" in output
    assert VALUE not in output


def test_filter_passes_message_without_secret():
    logger, stream = _capture("clean", lambda: [VALUE])
    logger.info("nothing to hide")
    output = stream.getvalue()
    assert output == "nothing to hide\n"
    assert VALUE not in output
