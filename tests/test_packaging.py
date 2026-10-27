import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
K8S = ROOT / "deploy" / "k8s"
DOCKERFILES = {
    "Dockerfile.web": "cal_mirror.web.app",
    "Dockerfile.worker": "cal_mirror.worker",
}
MANIFESTS = [
    "web-deployment.yaml",
    "web-service.yaml",
    "certificate.yaml",
    "issuer-selfsigned.yaml",
    "worker-deployment.yaml",
    "networkpolicy-web.yaml",
    "networkpolicy-worker.yaml",
]
SETTING_KEYS = [
    "HOST_NAME",
    "OAUTH_REDIRECT",
    "LAN_CIDR",
    "VPN_CIDR",
    "TLS_ISSUER_KIND",
    "TLS_ISSUER_NAME",
    "PG_NAMESPACE",
    "PG_HOST",
    "PG_DB",
    "PG_USER",
    "USB_NODE",
    "ARCHIVE_HOST_PATH",
]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def k8s_files() -> list[Path]:
    return sorted(p for p in K8S.rglob("*") if p.is_file())


def packaging_files() -> list[Path]:
    names = [ROOT / name for name in DOCKERFILES]
    return [*names, ROOT / ".dockerignore", *k8s_files(), ROOT / "docs" / "install.md"]


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


def test_kinds():
    text = "\n".join(read(p) for p in k8s_files())
    for kind in ("Deployment", "Service", "NetworkPolicy", "Certificate", "Issuer"):
        assert re.search(rf"^kind: {kind}$", text, re.M), kind
    assert not re.search(r"^\s*kind:\s*(Ingress|Secret)\b", text, re.M)
    assert "stringData" not in text


def test_no_private_key_marker():
    marker = "BEGIN " + "PRIVATE KEY"
    for path in packaging_files():
        assert marker not in read(path), path


def test_deployments_and_policies():
    web = read(K8S / "web-deployment.yaml")
    worker = read(K8S / "worker-deployment.yaml")
    for text in (web, worker):
        assert "secretKeyRef" in text
        assert "cal-mirror-secrets" in text
    for needle in ("nodeSelector", "kubernetes.io/hostname", "hostPath", "replicas: 1"):
        assert needle in worker, needle
    assert "ipBlock" in read(K8S / "networkpolicy-web.yaml")
    for name in ("networkpolicy-web.yaml", "networkpolicy-worker.yaml"):
        text = read(K8S / name)
        for needle in ("port: 53", "5432", "443"):
            assert needle in text, (name, needle)


def test_settings_keys():
    settings = read(K8S / "settings.properties")
    named = read(K8S / "kustomization.yaml") + "".join(read(K8S / name) for name in MANIFESTS)
    install = read(ROOT / "docs" / "install.md")
    for key in SETTING_KEYS:
        assert re.search(rf"^{key}=", settings, re.M), key
        assert key in named, key
        assert key in install, key
    for key in re.findall(r"^([^=\s#]+)=", settings, re.M):
        assert not re.search(r"PASSWORD|SECRET|TOKEN", key), key


def test_kustomization_and_install_doc():
    kustomization = read(K8S / "kustomization.yaml")
    for word in ("configMapGenerator", "replacements", "images", *MANIFESTS):
        assert word in kustomization, word
    install = read(ROOT / "docs" / "install.md")
    assert "kubectl apply -k deploy/k8s" in install
    assert "kubectl -n cal-mirror create secret generic cal-mirror-secrets" in install
