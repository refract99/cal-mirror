"""Make a self-signed certificate for the web app, once, and hand folders to the app user."""

import datetime
import ipaddress
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

APP_UID = 10001
CERT_DIR = Path(os.environ.get("CERT_DIR", "/certs"))
ARCHIVE_DIR = Path(os.environ.get("ARCHIVE_DIR", "/archive"))


def alt_names(host: str) -> list[x509.GeneralName]:
    names: list[x509.GeneralName] = [x509.DNSName("localhost")]
    try:
        names.append(x509.IPAddress(ipaddress.ip_address(host)))
    except ValueError:
        if host != "localhost":
            names.append(x509.DNSName(host))
    names.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))
    return names


def make_cert(host: str) -> tuple[bytes, bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(alt_names(host)), critical=False)
        .sign(key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )


def main() -> None:
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = CERT_DIR / "tls.crt", CERT_DIR / "tls.key"
    if cert_path.exists() and key_path.exists():
        print("certificate already present")
    else:
        cert_pem, key_pem = make_cert(os.environ.get("HOST_NAME", "localhost"))
        cert_path.write_bytes(cert_pem)
        key_path.write_bytes(key_pem)
        print("certificate written")
    for path in (CERT_DIR, cert_path, key_path, ARCHIVE_DIR):
        os.chown(path, APP_UID, APP_UID)
    key_path.chmod(0o600)


if __name__ == "__main__":
    main()
