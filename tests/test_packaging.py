import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILES = {
    "Dockerfile.web": "cal_mirror.web.app",
    "Dockerfile.worker": "cal_mirror.worker",
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def packaging_files() -> list[Path]:
    names = [ROOT / name for name in DOCKERFILES]
    return [*names, ROOT / ".dockerignore", ROOT / "docs" / "install.md"]


def test_dockerfiles_shape():
    for name, module in DOCKERFILES.items():
        text = read(ROOT / name)
        assert re.search(r"^FROM python:3\.13\.\d+-slim\b", text, re.M)
        assert re.search(r"ghcr\.io/astral-sh/uv:\d+\.\d+\.\d+", text)
        assert "uv sync --frozen --no-dev" in text
        users = re.findall(r"^USER\s+(\S+)", text, re.M)
        assert users
        assert users[-1] not in ("root", "0")
        cmd = re.findall(r"^CMD\s+(.*)$", text, re.M)
        assert cmd
        assert module in cmd[-1]


def test_no_latest_tags():
    for path in packaging_files():
        text = read(path)
        assert not re.search(r":latest\b|newTag:\s*latest", text), path


def test_no_private_key_marker():
    marker = "BEGIN " + "PRIVATE KEY"
    for path in packaging_files():
        assert marker not in read(path), path
