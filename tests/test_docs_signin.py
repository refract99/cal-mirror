"""PRD-01 R21: the sign-in page doc covers routes, lock rule, second factor, passkeys and Google."""

from pathlib import Path

DOC = Path(__file__).resolve().parents[1] / "docs" / "page_signin.md"

ROUTES = [
    "/setup",
    "/signin",
    "/signin/totp",
    "/signin/passkey/options",
    "/signin/passkey",
    "/signin/google",
    "/signin/google/callback",
    "/signout",
    "/account/2fa",
    "/account/passkeys/options",
    "/account/passkeys",
    "/account/passkeys/delete",
    "/account/google/link",
    "/account/google/unlink",
]


def text() -> str:
    return DOC.read_text(encoding="utf-8")


def test_every_signin_route_is_documented() -> None:
    doc = text()
    missing = [route for route in ROUTES if f"`{route}`" not in doc and f" {route}`" not in doc]
    assert missing == []


def test_lock_rule_second_factor_passkeys_and_google() -> None:
    doc = text()
    lower = doc.lower()
    for needle in ("CAL_MIRROR_SETUP_CODE", "signin_failure_limit", "signin_lock_seconds"):
        assert needle in doc, needle
    for needle in ("client address", "second factor", "passkey", "sign in with google"):
        assert needle in lower, needle
    for stale in ("MAX_FAILURES", "LOCK_SECONDS =", "Settings.two_factor"):
        assert stale not in doc, stale
