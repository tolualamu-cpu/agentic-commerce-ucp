"""UCPProfileDiscovery — fetches /.well-known/ucp, caches in DB.

MVP behaviour: try real fetch first; on failure or 404, look up the stub
in config/merchant_profiles.json. Both paths produce a UCPProfile or None.

Cache TTL: 60s per UCP spec (also configurable via settings).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from config.settings import settings
from models.ucp_profile import UCPProfile
from storage.db import DB, ProfileCacheQ
from ucp.profile_parser import (
    ProfileParserRegistry,
    _extract_supported_versions,
    get_default_registry,
)


DEFAULT_STUB_PATH = Path(__file__).resolve().parent.parent / "config" / "merchant_profiles.json"

# Sentinel for a fresh "this domain has no UCP profile" tombstone in the cache.
# Distinguishes a known-negative (skip the network) from a cache miss (resolve).
_NEGATIVE = object()


def _parse_dt(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


class UCPProfileDiscovery:
    """Discovers merchant UCP profiles.

    Resolution order:
        1. DB cache (if fresh)
        2. Real GET https://{domain}/.well-known/ucp
        3. Stub from merchant_profiles.json
        4. None (merchant has no UCP support — caller falls back to direct adapter)
    """

    def __init__(
        self,
        db: DB,
        http_client: httpx.AsyncClient | None = None,
        stub_path: Path = DEFAULT_STUB_PATH,
        ttl_seconds: int | None = None,
        parser_registry: ProfileParserRegistry | None = None,
    ):
        self.db = db
        self._http = http_client
        self._owns_http = http_client is None
        self.stub_path = stub_path
        self.ttl = ttl_seconds if ttl_seconds is not None else settings.profile_cache_ttl_seconds
        self._registry = parser_registry if parser_registry is not None else get_default_registry()
        self._stubs = self._load_stubs()

    def _load_stubs(self) -> dict[str, dict[str, Any]]:
        if not self.stub_path.exists():
            return {}
        data = json.loads(self.stub_path.read_text())
        return data.get("profiles", {})

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=5.0)
        return self._http

    async def try_discover(self, merchant_domain: str) -> UCPProfile | None:
        """Returns a UCPProfile if available, None otherwise.

        Caches both positive results AND negative ones (a tombstone): a domain
        with no UCP profile pays the live-fetch cost at most once per TTL instead
        of on every resolve. The TTL still applies to tombstones, so a merchant
        that later publishes a profile is rediscovered after expiry.
        """
        cached = self._read_cache(merchant_domain)
        if cached is _NEGATIVE:
            return None
        if cached is not None:
            return cached  # fresh positive hit (UCPProfile)

        profile = await self._fetch_real(merchant_domain)
        if profile is None:
            profile = self._fetch_stub(merchant_domain)

        if profile is not None:
            self._write_cache(profile)
        else:
            self._write_negative_cache(merchant_domain)
        return profile

    async def _fetch_real(self, merchant_domain: str) -> UCPProfile | None:
        url = f"https://{merchant_domain}/.well-known/ucp"
        try:
            resp = await self.http.get(url)
            if resp.status_code != 200:
                return None
            data = resp.json()

            # If the merchant publishes multiple spec versions, re-fetch the
            # highest version we have a parser for.  This guarantees we always
            # parse a version we know rather than hoping the default root profile
            # matches our parser's expected shape.
            supported = _extract_supported_versions(data)
            if supported:
                best_url = self._registry.negotiate_version_url(supported)
                if best_url:
                    v_resp = await self.http.get(best_url)
                    if v_resp.status_code == 200:
                        data = v_resp.json()

            normalised = self._registry.parse(data, merchant_domain)
            if normalised is None:
                return None
            return UCPProfile(**normalised)
        except (httpx.HTTPError, ValueError):
            return None

    def _fetch_stub(self, merchant_domain: str) -> UCPProfile | None:
        stub = self._stubs.get(merchant_domain)
        if not stub:
            return None
        # Drop comment fields
        clean = {k: v for k, v in stub.items() if not k.startswith("_")}
        return UCPProfile(**clean)

    def _read_cache(self, merchant_domain: str):
        """Return a fresh UCPProfile, the _NEGATIVE sentinel, or None (miss/stale)."""
        row = self.db.profile_cache.get(ProfileCacheQ.merchant_domain == merchant_domain)
        if not row:
            return None
        cached_at = _parse_dt(row["cached_at"])
        if datetime.now(timezone.utc) - cached_at > timedelta(seconds=self.ttl):
            return None
        if row.get("negative"):
            return _NEGATIVE
        try:
            return UCPProfile(**row["profile"])
        except Exception:
            return None

    def _write_cache(self, profile: UCPProfile) -> None:
        now = datetime.now(timezone.utc)
        profile_data = profile.model_dump(mode="json")
        self.db.profile_cache.upsert(
            {
                "merchant_domain": profile.merchant_domain,
                "profile": profile_data,
                "cached_at": now.isoformat(),
                "negative": False,
            },
            ProfileCacheQ.merchant_domain == profile.merchant_domain,
        )

    def _write_negative_cache(self, merchant_domain: str) -> None:
        """Tombstone a domain with no UCP profile so we don't re-fetch every resolve."""
        now = datetime.now(timezone.utc)
        self.db.profile_cache.upsert(
            {
                "merchant_domain": merchant_domain,
                "profile": None,
                "cached_at": now.isoformat(),
                "negative": True,
            },
            ProfileCacheQ.merchant_domain == merchant_domain,
        )

    async def close(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
