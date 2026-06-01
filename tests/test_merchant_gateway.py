"""MerchantGateway: routing decisions + caching."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from adapters.shopify_mcp import ShopifyMCPAdapter, StubShopifyTransport
from gateway.merchant_gateway import MerchantGateway
from ucp.client import UCPRestClient
from ucp.discovery import UCPProfileDiscovery
from ucp.signing import RequestSigner, generate_keypair


@pytest.fixture
def stubs(tmp_path) -> Path:
    p = tmp_path / "profiles.json"
    p.write_text(
        json.dumps(
            {
                "profiles": {
                    "ucp-merchant.local": {
                        "merchant_domain": "ucp-merchant.local",
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
                                "base_url": "http://ucp-merchant.local",
                            }
                        ],
                        "payment_handlers": [],
                        "signing_keys": [],
                    },
                    "no-ucp-shop.com": {
                        "merchant_domain": "no-ucp-shop.com",
                        "capabilities": [],
                        "services": [],
                        "payment_handlers": [],
                        "signing_keys": [],
                    },
                }
            }
        )
    )
    return p


def _gateway(tmp_db, stubs) -> MerchantGateway:
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    discovery = UCPProfileDiscovery(tmp_db, http_client=http, stub_path=stubs)
    private_pem, _, _ = generate_keypair("k1")
    signer = RequestSigner(private_pem, key_id="k1")
    return MerchantGateway(
        discovery=discovery,
        signer=signer,
        direct_adapters={
            "demo-shop.myshopify.com": ShopifyMCPAdapter(
                "demo-shop.myshopify.com", StubShopifyTransport()
            ),
        },
    )


def test_ucp_route_chosen_when_profile_supports_checkout(tmp_db, stubs):
    gw = _gateway(tmp_db, stubs)
    client = asyncio.get_event_loop().run_until_complete(gw.resolve_client("ucp-merchant.local"))
    assert isinstance(client, UCPRestClient)


def test_direct_adapter_fallback_when_no_ucp(tmp_db, stubs):
    gw = _gateway(tmp_db, stubs)
    client = asyncio.get_event_loop().run_until_complete(
        gw.resolve_client("demo-shop.myshopify.com")
    )
    assert isinstance(client, ShopifyMCPAdapter)


def test_profile_with_empty_capabilities_falls_through(tmp_db, stubs):
    gw = _gateway(tmp_db, stubs)
    client = asyncio.get_event_loop().run_until_complete(gw.resolve_client("no-ucp-shop.com"))
    assert client is None  # no UCP caps, no direct adapter registered


def test_unknown_domain_returns_none(tmp_db, stubs):
    gw = _gateway(tmp_db, stubs)
    client = asyncio.get_event_loop().run_until_complete(gw.resolve_client("never-heard-of.com"))
    assert client is None


def test_client_cache_returns_same_instance(tmp_db, stubs):
    gw = _gateway(tmp_db, stubs)
    loop = asyncio.get_event_loop()
    a = loop.run_until_complete(gw.resolve_client("demo-shop.myshopify.com"))
    b = loop.run_until_complete(gw.resolve_client("demo-shop.myshopify.com"))
    assert a is b
