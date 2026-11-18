"""PRD-01 R6, R8, R13, R14, R15: passkeys as a second factor."""

import hashlib
import json
import secrets
from datetime import UTC, datetime

import pyotp
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from webauthn.helpers import bytes_to_base64url, encode_cbor

from cal_mirror.model import AuthRecord, Passkey, Settings
from cal_mirror.ports import FixedClock
from cal_mirror.store_memory import MemoryStore
from cal_mirror.web.app import create_app
from cal_mirror.web.page_signin import hash_password

ORIGIN = "https://testserver"
RP_ID = "testserver"
PASSWORD = "fixture-password-1"
START = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
ENROLL = "/account/2fa"


class Authenticator:
    """A software passkey: one P-256 key pair and its credential ID."""

    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.raw_id = secrets.token_bytes(16)
        self.id = bytes_to_base64url(self.raw_id)
        numbers = self.key.public_key().public_numbers()
        self.cose = encode_cbor(
            {
                1: 2,
                3: -7,
                -1: 1,
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )

    def passkey(self, sign_count: int = 0, name: str = "Laptop") -> Passkey:
        return Passkey(self.id, name, self.cose, sign_count, START)

    def _client_data(self, kind: str, challenge: str, origin: str) -> bytes:
        data = {"type": kind, "challenge": challenge, "origin": origin, "crossOrigin": False}
        return json.dumps(data).encode()

    def register(self, challenge: str, origin: str = ORIGIN) -> str:
        auth_data = (
            hashlib.sha256(RP_ID.encode()).digest()
            + bytes([0x45])
            + (0).to_bytes(4, "big")
            + bytes(16)
            + len(self.raw_id).to_bytes(2, "big")
            + self.raw_id
            + self.cose
        )
        attestation = encode_cbor({"fmt": "none", "attStmt": {}, "authData": auth_data})
        credential = {
            "id": self.id,
            "rawId": self.id,
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(
                    self._client_data("webauthn.create", challenge, origin)
                ),
                "attestationObject": bytes_to_base64url(attestation),
            },
            "clientExtensionResults": {},
        }
        return json.dumps(credential)

    def assert_(self, challenge: str, sign_count: int, origin: str = ORIGIN) -> str:
        auth_data = (
            hashlib.sha256(RP_ID.encode()).digest() + bytes([0x05]) + sign_count.to_bytes(4, "big")
        )
        client_data = self._client_data("webauthn.get", challenge, origin)
        signed = auth_data + hashlib.sha256(client_data).digest()
        signature = self.key.sign(signed, ec.ECDSA(hashes.SHA256()))
        credential = {
            "id": self.id,
            "rawId": self.id,
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "authenticatorData": bytes_to_base64url(auth_data),
                "signature": bytes_to_base64url(signature),
            },
            "clientExtensionResults": {},
        }
        return json.dumps(credential)


class Env:
    def __init__(self, totp: bool = False, limit: int = 5) -> None:
        self.store = MemoryStore()
        self.store.save_settings(Settings(signin_failure_limit=limit))
        self.store.save_auth(AuthRecord(password_hash=hash_password(PASSWORD), totp_enabled=totp))
        self.secret = pyotp.random_base32()
        if totp:
            self.store.set_secret("totp_secret", self.secret)
        self.clock = FixedClock(START)
        self.app = create_app(self.store, self.clock)
        self.client = self.new_client()

    def new_client(self) -> TestClient:
        return TestClient(
            self.app, base_url=ORIGIN, headers={"Origin": ORIGIN}, follow_redirects=False
        )

    def password(self, c: TestClient | None = None):
        return (c or self.client).post("/signin", data={"password": PASSWORD})

    def full_session(self) -> None:
        self.password()
        code = pyotp.TOTP(self.secret).at(self.clock.now())
        assert self.client.post("/signin/totp", data={"code": code}).status_code == 303
        assert self.client.get("/status").status_code == 200

    def registration_challenge(self) -> str:
        options = self.client.post("/account/passkeys/options")
        assert options.status_code == 200
        body = options.json()
        assert body["rp"]["id"] == RP_ID
        return body["challenge"]

    def signin_challenge(self, c: TestClient | None = None) -> str:
        options = (c or self.client).post("/signin/passkey/options")
        assert options.status_code == 200
        body = options.json()
        assert body["rpId"] == RP_ID
        return body["challenge"]

    def submit(self, credential: str, c: TestClient | None = None):
        return (c or self.client).post("/signin/passkey", data={"credential": credential})

    def signed_in(self, c: TestClient | None = None) -> bool:
        return (c or self.client).get("/status").status_code == 200

    def locked(self) -> bool:
        return self.password(self.new_client()).status_code == 429


def test_register_list_and_store_public_data_only() -> None:
    env = Env(totp=True)
    env.full_session()
    key = Authenticator()
    challenge = env.registration_challenge()
    added = env.client.post(
        "/account/passkeys", data={"name": "Laptop", "credential": key.register(challenge)}
    )
    assert added.status_code == 303
    assert added.headers["location"] == ENROLL
    [stored] = env.store.list_passkeys()
    assert stored.credential_id == key.id
    assert stored.name == "Laptop"
    assert stored.public_key == key.cose
    assert stored.sign_count == 0
    assert stored.created == START
    page = env.client.get(ENROLL)
    assert "Laptop" in page.text
    assert "2026-11-03" in page.text
    second = Authenticator()
    env.client.post(
        "/account/passkeys",
        data={"name": "Phone", "credential": second.register(env.registration_challenge())},
    )
    assert [p.name for p in env.store.list_passkeys()] == ["Laptop", "Phone"]
    assert "Phone" in env.client.get(ENROLL).text


def test_registration_over_another_challenge_is_refused() -> None:
    env = Env(totp=True)
    env.full_session()
    env.registration_challenge()
    other = bytes_to_base64url(secrets.token_bytes(32))
    refused = env.client.post(
        "/account/passkeys", data={"name": "Laptop", "credential": Authenticator().register(other)}
    )
    assert refused.status_code == 400
    assert env.store.list_passkeys() == []


def test_passkey_completes_enrollment() -> None:
    env = Env()
    env.password()
    assert env.client.get("/status").headers["location"] == ENROLL
    key = Authenticator()
    added = env.client.post(
        "/account/passkeys",
        data={"name": "Laptop", "credential": key.register(env.registration_challenge())},
    )
    assert added.status_code == 303
    assert env.signed_in()


def test_delete_each_but_never_the_last_factor() -> None:
    env = Env(totp=True)
    env.full_session()
    first, second = Authenticator(), Authenticator()
    env.store.add_passkey(first.passkey(name="Laptop"))
    env.store.add_passkey(second.passkey(name="Phone"))
    gone = env.client.post("/account/passkeys/delete", data={"credential_id": first.id})
    assert gone.status_code == 303
    assert [p.credential_id for p in env.store.list_passkeys()] == [second.id]
    env.store.save_auth(AuthRecord(password_hash=hash_password(PASSWORD), totp_enabled=False))
    last = env.client.post("/account/passkeys/delete", data={"credential_id": second.id})
    assert last.status_code == 400
    assert [p.credential_id for p in env.store.list_passkeys()] == [second.id]


def test_registered_passkey_signs_in() -> None:
    env = Env()
    key = Authenticator()
    env.store.add_passkey(key.passkey())
    step = env.password()
    assert step.headers["location"] == "/signin/totp"
    assert "/static/passkey.js" in env.client.get("/signin/totp").text
    done = env.submit(key.assert_(env.signin_challenge(), 1))
    assert done.status_code == 303
    assert done.headers["location"] == "/status"
    assert env.signed_in()
    assert env.store.list_passkeys()[0].sign_count == 1


def test_unregistered_credential_fails_and_counts() -> None:
    env = Env(limit=1)
    env.store.add_passkey(Authenticator().passkey())
    env.password()
    refused = env.submit(Authenticator().assert_(env.signin_challenge(), 1))
    assert refused.status_code in (401, 429)
    assert not env.signed_in()
    assert env.locked()


def test_assertion_over_another_challenge_fails_and_counts() -> None:
    env = Env(limit=1)
    key = Authenticator()
    env.store.add_passkey(key.passkey())
    env.password()
    env.signin_challenge()
    other = bytes_to_base64url(secrets.token_bytes(32))
    env.submit(key.assert_(other, 1))
    assert not env.signed_in()
    assert env.locked()


def test_sign_count_must_grow() -> None:
    for count in (5, 3):
        env = Env(limit=1)
        key = Authenticator()
        env.store.add_passkey(key.passkey(sign_count=5))
        env.password()
        env.submit(key.assert_(env.signin_challenge(), count))
        assert not env.signed_in()
        assert env.locked()
        assert env.store.list_passkeys()[0].sign_count == 5


def test_challenge_is_accepted_once() -> None:
    env = Env()
    key = Authenticator()
    env.store.add_passkey(key.passkey())
    env.password()
    pending = dict(env.client.cookies)
    challenge = env.signin_challenge()
    assert env.submit(key.assert_(challenge, 1)).status_code == 303
    replay = env.new_client()
    for name, value in pending.items():
        replay.cookies.set(name, value)
    env.submit(key.assert_(challenge, 2), replay)
    assert not env.signed_in(replay)
    again = env.new_client()
    env.password(again)
    env.submit(key.assert_(challenge, 3), again)
    assert not env.signed_in(again)


def test_challenge_dies_with_its_pending_signin() -> None:
    env = Env()
    key = Authenticator()
    env.store.add_passkey(key.passkey())
    env.password()
    challenge = env.signin_challenge()
    env.clock.advance(301)
    env.submit(key.assert_(challenge, 1))
    assert not env.signed_in()
    assert env.store.list_passkeys()[0].sign_count == 0
