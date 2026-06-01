"""RFC 9421 HTTP Message Signatures + JWK key generation/loading.

Ed25519 is the chosen algorithm (compact, fast, modern). Compatible with RFC 9421
``alg=ed25519``. Signed components by default: @method, @target-uri, host,
content-type, content-digest. content-digest is the SHA-256 hash of the body per
RFC 9530, included in Signature-Input via ``;params``.
"""

from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import dataclass
from typing import Iterable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature


DEFAULT_COVERED_COMPONENTS: tuple[str, ...] = (
    "@method",
    "@target-uri",
    "host",
    "content-type",
    "content-digest",
)


@dataclass
class SignedRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes


def generate_keypair(key_id: str) -> tuple[str, str, dict]:
    """Generate an Ed25519 keypair.

    Returns (private_pem, public_pem, jwk_dict).
    """
    priv = Ed25519PrivateKey.generate()
    private_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    pub = priv.public_key()
    public_pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    raw_pub = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    jwk = {
        "kid": key_id,
        "kty": "OKP",
        "crv": "Ed25519",
        "alg": "EdDSA",
        "x": base64.urlsafe_b64encode(raw_pub).rstrip(b"=").decode("ascii"),
    }
    return private_pem, public_pem, jwk


def _load_private_key(pem: str) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("AGENT_PRIVATE_KEY_PEM must be an Ed25519 PKCS8 PEM key")
    return key


def _load_public_key_from_jwk(jwk: dict) -> Ed25519PublicKey:
    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
        raise ValueError("JWK is not an Ed25519 OKP key")
    raw = base64.urlsafe_b64decode(jwk["x"] + "==")
    return Ed25519PublicKey.from_public_bytes(raw)


def _content_digest(body: bytes) -> str:
    """RFC 9530 Content-Digest using SHA-256."""
    digest = hashlib.sha256(body).digest()
    return f"sha-256=:{base64.b64encode(digest).decode('ascii')}:"


def _parse_url(url: str) -> tuple[str, str]:
    """Returns (host, target_uri) per RFC 9421."""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    host = parts.netloc
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    return host, target


def _build_signature_base(
    components: Iterable[str], values: dict[str, str], sig_params: str
) -> bytes:
    lines = [f'"{c}": {values[c]}' for c in components]
    lines.append(f'"@signature-params": {sig_params}')
    return "\n".join(lines).encode("utf-8")


def _build_sig_params(
    components: Iterable[str], key_id: str, created: int, alg: str = "ed25519"
) -> str:
    inner = " ".join(f'"{c}"' for c in components)
    return f'({inner});created={created};keyid="{key_id}";alg="{alg}"'


class RequestSigner:
    """Signs outgoing HTTP requests per RFC 9421.

    Usage:
        signer = RequestSigner(private_pem, key_id="agent-key-1")
        signed = signer.sign("POST", "https://merchant.com/checkout-sessions",
                             headers={"content-type": "application/json"},
                             body=b'{"foo":"bar"}')
        # signed.headers now contains Content-Digest, Signature-Input, Signature
    """

    def __init__(
        self,
        private_key_pem: str,
        key_id: str,
        covered_components: Iterable[str] = DEFAULT_COVERED_COMPONENTS,
    ):
        self._key = _load_private_key(private_key_pem)
        self.key_id = key_id
        self.covered_components = tuple(covered_components)

    def sign(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
        *,
        created: int | None = None,
    ) -> SignedRequest:
        headers = dict(headers or {})
        host, target = _parse_url(url)

        # Inject derived headers
        headers.setdefault("host", host)
        if body and "content-digest" not in (h.lower() for h in headers):
            headers["content-digest"] = _content_digest(body)
        elif "content-digest" in self.covered_components and body == b"":
            # Empty body still gets a digest for canonical coverage
            headers.setdefault("content-digest", _content_digest(b""))

        # Component value map
        values: dict[str, str] = {
            "@method": method.upper(),
            "@target-uri": url,
            "@path": target,
            "host": headers.get("host", host),
            "content-type": headers.get("content-type", ""),
            "content-digest": headers.get("content-digest", ""),
        }

        for comp in self.covered_components:
            if comp not in values:
                values[comp] = headers.get(comp, "")

        created = created or int(time.time())
        sig_params = _build_sig_params(self.covered_components, self.key_id, created)
        base = _build_signature_base(self.covered_components, values, sig_params)

        signature = self._key.sign(base)
        sig_b64 = base64.b64encode(signature).decode("ascii")

        headers["Signature-Input"] = f"sig1={sig_params}"
        headers["Signature"] = f"sig1=:{sig_b64}:"

        return SignedRequest(method=method.upper(), url=url, headers=headers, body=body)

    def verify(self, signed: SignedRequest, public_jwk: dict) -> bool:
        """Verifies a previously-signed request against a public JWK.

        Used in tests and for verifying merchant webhooks (with their JWK).
        """
        try:
            sig_input = signed.headers.get("Signature-Input", "")
            sig_header = signed.headers.get("Signature", "")
            if not sig_input.startswith("sig1=") or not sig_header.startswith("sig1=:"):
                return False

            sig_params = sig_input[len("sig1=") :]
            sig_b64 = sig_header[len("sig1=:") : -1]
            signature = base64.b64decode(sig_b64)

            # Parse covered components from sig_params: ("a" "b");...
            start = sig_params.index("(")
            end = sig_params.index(")")
            inner = sig_params[start + 1 : end]
            components = tuple(s.strip('"') for s in inner.split())

            host, target = _parse_url(signed.url)
            values: dict[str, str] = {
                "@method": signed.method.upper(),
                "@target-uri": signed.url,
                "@path": target,
                "host": signed.headers.get("host", host),
                "content-type": signed.headers.get("content-type", ""),
                "content-digest": signed.headers.get("content-digest", ""),
            }
            for c in components:
                if c not in values:
                    values[c] = signed.headers.get(c, "")

            base = _build_signature_base(components, values, sig_params)
            pub = _load_public_key_from_jwk(public_jwk)
            pub.verify(signature, base)
            return True
        except (InvalidSignature, ValueError, KeyError):
            return False
