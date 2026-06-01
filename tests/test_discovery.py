"""UCPProfileDiscovery: stub fallback + DB caching."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from ucp.discovery import UCPProfileDiscovery


@pytest.fixture
def stub_path(tmp_path: Path) -> Path:
    p = tmp_path / "profiles.json"
    p.write_text(
        json.dumps(
            {
                "profiles": {
                    "stub-merchant.local": {
                        "merchant_domain": "stub-merchant.local",
                        "capabilities": [
                            {
                                "namespace": "dev.ucp.shopping.checkout",
                                "version": "2025-01-15",
                                "spec_url": "https://ucp.dev/spec",
                            }
                        ],
                        "services": [
                            {
                                "type": "rest",
                                "spec_url": "https://ucp.dev/oas",
                                "base_url": "http://localhost:8080",
                            }
                        ],
                        "payment_handlers": [],
                        "signing_keys": [],
                    }
                }
            }
        )
    )
    return p


@pytest.fixture
def offline_http() -> httpx.AsyncClient:
    """An httpx client that always raises — forces stub path."""
    transport = httpx.MockTransport(lambda req: httpx.Response(404))
    return httpx.AsyncClient(transport=transport)


def test_stub_fallback(tmp_db, stub_path, offline_http):
    discovery = UCPProfileDiscovery(tmp_db, http_client=offline_http, stub_path=stub_path)
    profile = asyncio.get_event_loop().run_until_complete(
        discovery.try_discover("stub-merchant.local")
    )
    assert profile is not None
    assert profile.merchant_domain == "stub-merchant.local"
    assert profile.has_capability("dev.ucp.shopping.checkout")


def test_unknown_domain_returns_none(tmp_db, stub_path, offline_http):
    discovery = UCPProfileDiscovery(tmp_db, http_client=offline_http, stub_path=stub_path)
    profile = asyncio.get_event_loop().run_until_complete(discovery.try_discover("nope.example"))
    assert profile is None


def test_real_fetch_wins(tmp_db, stub_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/.well-known/ucp"
        return httpx.Response(
            200,
            json={
                "merchant_domain": "live.example",
                "capabilities": [
                    {
                        "namespace": "dev.ucp.shopping.checkout",
                        "version": "2025-01-15",
                        "spec_url": "https://ucp.dev/spec",
                    }
                ],
                "services": [{"type": "rest", "spec_url": "https://live.example/oas"}],
                "payment_handlers": [],
                "signing_keys": [],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    discovery = UCPProfileDiscovery(tmp_db, http_client=client, stub_path=stub_path)
    profile = asyncio.get_event_loop().run_until_complete(discovery.try_discover("live.example"))
    assert profile is not None
    assert profile.merchant_domain == "live.example"


def test_cache_hit_skips_fetch(tmp_db, stub_path):
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(
            200,
            json={
                "merchant_domain": "live.example",
                "capabilities": [],
                "services": [],
                "payment_handlers": [],
                "signing_keys": [],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    discovery = UCPProfileDiscovery(tmp_db, http_client=client, stub_path=stub_path, ttl_seconds=60)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(discovery.try_discover("live.example"))
    loop.run_until_complete(discovery.try_discover("live.example"))
    assert call_count["n"] == 1
