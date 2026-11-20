"""PRD-01 R3: the setup code reaches the web app in the compose deployment."""

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
