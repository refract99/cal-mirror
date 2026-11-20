"""A software WebAuthn authenticator for tests: ES256 key, "none" attestation, no network."""

import base64
import hashlib
import json
import os

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

FLAG_USER_PRESENT = 0x01
FLAG_USER_VERIFIED = 0x04
FLAG_ATTESTED = 0x40


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SoftAuthenticator:
    """One passkey. `sign_count` is the counter the next assertion reports."""

    def __init__(self, sign_count: int = 0) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.raw_id = os.urandom(16)
        self.sign_count = sign_count

    @property
    def credential_id(self) -> str:
        return b64url(self.raw_id)

    def cose_key(self) -> bytes:
        """The public key as a COSE_Key (CBOR), as the authenticator data carries it."""
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,
                3: -7,
                -1: 1,
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )

    @staticmethod
    def _client_data(kind: str, challenge: str, origin: str) -> bytes:
        data = {"type": kind, "challenge": challenge, "origin": origin, "crossOrigin": False}
        return json.dumps(data).encode()

    def register(self, challenge: str, rp_id: str, origin: str) -> dict:
        """A RegistrationResponseJSON for creation options carrying `challenge`."""
        flags = FLAG_USER_PRESENT | FLAG_USER_VERIFIED | FLAG_ATTESTED
        attested = bytes(16) + len(self.raw_id).to_bytes(2, "big") + self.raw_id
        auth_data = (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([flags])
            + self.sign_count.to_bytes(4, "big")
            + attested
            + self.cose_key()
        )
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        client_data = self._client_data("webauthn.create", challenge, origin)
        return {
            "id": self.credential_id,
            "rawId": self.credential_id,
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation),
                "transports": ["internal"],
            },
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }

    def assertion(self, challenge: str, rp_id: str, origin: str) -> dict:
        """An AuthenticationResponseJSON signed over `challenge`, reporting `sign_count`."""
        flags = FLAG_USER_PRESENT | FLAG_USER_VERIFIED
        auth_data = (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([flags])
            + self.sign_count.to_bytes(4, "big")
        )
        client_data = self._client_data("webauthn.get", challenge, origin)
        signed = auth_data + hashlib.sha256(client_data).digest()
        signature = self.key.sign(signed, ec.ECDSA(hashes.SHA256()))
        return {
            "id": self.credential_id,
            "rawId": self.credential_id,
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(signature),
            },
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }
