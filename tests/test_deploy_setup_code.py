"""PRD-01 R3: the setup code reaches the web app in both deployments."""

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "deploy" / "compose" / "init.sh"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def run_init(tmp: Path) -> dict[str, str]:
    target = tmp / "deploy" / "compose" / "init.sh"
    target.parent.mkdir(parents=True)
    shutil.copy(INIT, target)
    sh = shutil.which("sh")
    assert sh is not None
    done = subprocess.run([sh, str(target)], capture_output=True, text=True, check=False)  # noqa: S603
    assert done.returncode == 0, done.stderr
    values = {}
    for line in read(tmp / ".env").splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def test_init_writes_a_fresh_random_setup_code(tmp_path: Path) -> None:
    first = run_init(tmp_path / "one")
    second = run_init(tmp_path / "two")
    code = first.get("CAL_MIRROR_SETUP_CODE", "")
    assert len(code) >= 16
    assert re.fullmatch(r"[A-Za-z0-9_-]+", code)
    assert second.get("CAL_MIRROR_SETUP_CODE", "") not in ("", code)
    assert "CAL_MIRROR_SECRET_KEY" in first


def env_entry(manifest: str, name: str) -> str:
    """The lines of one `env:` entry of a manifest, from `- name: <name>` to the next entry."""
    match = re.search(
        rf"^(\s*)- name: {re.escape(name)}\s*$\n((?:\1  .*\n|\s*\n)*)", manifest, re.M
    )
    assert match is not None, name
    return match.group(2)


def test_k8s_web_reads_the_setup_code_from_the_secret() -> None:
    entry = env_entry(
        read(ROOT / "deploy" / "k8s" / "web-deployment.yaml"), "CAL_MIRROR_SETUP_CODE"
    )
    assert "secretKeyRef" in entry
    assert re.search(r"name: cal-mirror-secrets\s*$", entry, re.M)
    assert re.search(r"key: setup-code\s*$", entry, re.M)


def web_service(compose: str) -> str:
    match = re.search(r"^  web:\s*$\n((?:    .*\n|\s*\n)*)", compose, re.M)
    assert match is not None
    return match.group(1)


def test_compose_passes_setup_code_and_signin_redirect_to_web() -> None:
    web = web_service(read(ROOT / "compose.yaml"))
    assert re.search(r"^\s+CAL_MIRROR_SETUP_CODE: .*\$\{CAL_MIRROR_SETUP_CODE\b", web, re.M)
    redirect = re.search(r"^\s+CAL_MIRROR_SIGNIN_REDIRECT: (\S+)\s*$", web, re.M)
    assert redirect is not None
    assert redirect.group(1).endswith("/signin/google/callback")
    assert "${HOST_NAME" in redirect.group(1)
