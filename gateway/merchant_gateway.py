"""MerchantGateway — try-UCP-first routing with direct-adapter fallback.

This is the architectural pivot point of the whole system. Agents call merchant
operations through this gateway; the gateway decides whether the request flows
through a UCP client (live profile, or stub) or a direct adapter.

Routing logic (per ARCHITECTURE.md §"The Gateway Pattern"):
    1. Cache hit (60s TTL) → return cached client
    2. UCPProfileDiscovery.try_discover(domain):
         - real /.well-known/ucp fetch
         - stub from merchant_profiles.json
    3. If profile exists with sufficient capabilities → instantiate UCPClient
    4. Else → direct adapter (e.g. ShopifyMCPAdapter)
    5. Else → None (unsupported; caller surfaces to user)
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from gateway.base import MerchantClient
from models.product import ProductResult
from ucp.capabilities import CapabilityNegotiator, UCPCapability
from ucp.client import UCPMCPClient, UCPRestClient
from ucp.discovery import UCPProfileDiscovery
from ucp.signing import RequestSigner


@dataclass
class _CacheEntry:
    client: MerchantClient
    expires_at: float


class MerchantGateway:
    """Routes merchant operations to the best available client for a domain."""

    def __init__(
        self,
        discovery: UCPProfileDiscovery,
        signer: RequestSigner,
        direct_adapters: dict[str, MerchantClient] | None = None,
        default_adapter_factory=None,
        negotiator: CapabilityNegotiator | None = None,
        cache_ttl: int = 60,
        ucp_http_client: httpx.AsyncClient | None = None,
    ):
        self.discovery = discovery
        self.signer = signer
        self.direct_adapters: dict[str, MerchantClient] = direct_adapters or {}
        self.default_adapter_factory = (
            default_adapter_factory  # called with domain → MerchantClient
        )
        self.negotiator = negotiator or CapabilityNegotiator()
        self.cache_ttl = cache_ttl
        # Optional shared http client for UCP REST/MCP clients — lets tests inject
        # a MockTransport. Production passes None and each client builds its own.
        self.ucp_http_client = ucp_http_client
        self._cache: dict[str, _CacheEntry] = {}

    # ── registration ──────────────────────────────────────────────────────────

    def register_direct_adapter(self, merchant_domain: str, adapter: MerchantClient) -> None:
        self.direct_adapters[merchant_domain] = adapter
        self._cache.pop(merchant_domain, None)

    # ── resolution ────────────────────────────────────────────────────────────

    async def resolve_client(self, merchant_domain: str) -> MerchantClient | None:
        entry = self._cache.get(merchant_domain)
        if entry and entry.expires_at > time.time():
            return entry.client

        client = await self._build_client(merchant_domain)
        if client is not None:
            self._cache[merchant_domain] = _CacheEntry(
                client=client,
                expires_at=time.time() + self.cache_ttl,
            )
        return client

    async def _build_client(self, merchant_domain: str) -> MerchantClient | None:
        profile = await self.discovery.try_discover(merchant_domain)

        if profile is not None and profile.capabilities:
            negotiation = self.negotiator.negotiate(profile)
            if UCPCapability.CHECKOUT in negotiation.shared:
                transport = profile.preferred_transport()
                if transport == "mcp":
                    return UCPMCPClient(profile, self.signer)
                # default to REST for "rest" or unknown
                return UCPRestClient(profile, self.signer, http_client=self.ucp_http_client)

        if merchant_domain in self.direct_adapters:
            return self.direct_adapters[merchant_domain]

        if self.default_adapter_factory is not None:
            return self.default_adapter_factory(merchant_domain)

        return None

    # ── high-level fan-out helpers ────────────────────────────────────────────

    async def search(
        self,
        query: str,
        domains: list[str],
        filters: dict | None = None,
        limit_per_merchant: int = 10,
    ) -> list[ProductResult]:
        """Fan out a search across multiple merchant domains."""
        import asyncio

        async def _one(domain: str) -> list[ProductResult]:
            client = await self.resolve_client(domain)
            if client is None:
                return []
            try:
                return await client.search_products(query, filters, limit=limit_per_merchant)
            except Exception:
                return []

        results = await asyncio.gather(*[_one(d) for d in domains])
        merged: list[ProductResult] = []
        for r in results:
            merged.extend(r)
        return merged

    async def close(self) -> None:
        for entry in self._cache.values():
            try:
                await entry.client.close()
            except Exception:
                pass
        self._cache.clear()
