"""UCP profile schemas — mirrors /.well-known/ucp response shape."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class UCPCapabilityDeclaration(BaseModel):
    """A single capability advertised by a merchant.

    UCP uses reverse-domain namespacing, e.g. 'dev.ucp.shopping.checkout'.
    """

    namespace: str
    version: str
    spec_url: str
    schema_url: str | None = None


class UCPService(BaseModel):
    """Transport endpoint declared by a merchant."""

    type: Literal["rest", "mcp", "a2a"]
    spec_url: str
    base_url: str | None = None


class PaymentHandler(BaseModel):
    """A payment handler offered by the merchant (Trust Triangle).

    Agent selects from these and obtains an opaque token from the handler.
    """

    id: str
    name: str
    spec_url: str


class SigningKey(BaseModel):
    """JWK-format public key for merchant webhook / response verification."""

    kid: str
    kty: str
    alg: str | None = None
    crv: str | None = None
    x: str | None = None
    n: str | None = None
    e: str | None = None
    use: str | None = None


class UCPProfile(BaseModel):
    """A merchant's /.well-known/ucp profile."""

    merchant_domain: str
    capabilities: list[UCPCapabilityDeclaration] = Field(default_factory=list)
    services: list[UCPService] = Field(default_factory=list)
    payment_handlers: list[PaymentHandler] = Field(default_factory=list)
    signing_keys: list[SigningKey] = Field(default_factory=list)
    cached_at: datetime | None = None

    def has_capability(self, namespace: str) -> bool:
        return any(c.namespace == namespace for c in self.capabilities)

    def preferred_transport(self) -> Literal["rest", "mcp", "a2a"] | None:
        order: list[Literal["rest", "mcp", "a2a"]] = ["rest", "mcp", "a2a"]
        available = {s.type for s in self.services}
        for t in order:
            if t in available:
                return t
        return None
