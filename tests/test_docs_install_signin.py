"""PRD-01 R21: install guide covers the setup code and the Google sign-in redirect URI."""

from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"


def test_install_sets_and_reads_the_setup_code() -> None:
    install = (DOCS / "install.md").read_text(encoding="utf-8")
    assert "CAL_MIRROR_SETUP_CODE" in install
    assert "--from-literal=setup-code=" in install
    assert "kubectl -n cal-mirror get secret cal-mirror-secrets" in install


def test_install_names_the_signin_redirect_uri() -> None:
    install = (DOCS / "install.md").read_text(encoding="utf-8")
    assert "/signin/google/callback" in install
    assert "/google/callback" in install
