"""Shared pytest fixtures."""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

import pytest

# Make project root importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def tmp_db(tmp_path):
    from storage.db import DB

    db = DB(tmp_path / "test.json")
    yield db
    db.close()


@pytest.fixture
def hmac_key() -> str:
    return secrets.token_hex(32)


@pytest.fixture
def ap2(tmp_db, hmac_key):
    from ucp.ap2_extension import AP2MandateEngine

    return AP2MandateEngine(tmp_db, hmac_key)


@pytest.fixture
def tool_ctx(tmp_db, ap2, tmp_path):
    """A fully-wired ToolContext using stub adapters — no live network."""
    import json
    import httpx

    from adapters.shopify_mcp import ShopifyMCPAdapter, StubShopifyTransport
    from adapters.stripe import StripeAdapter
    from gateway.merchant_gateway import MerchantGateway
    from gateway.payment_gateway import PaymentGateway
    from guardrails.confidence import ConfidenceChecker
    from guardrails.spending import SpendingLimiter
    from models.user import UserProfile
    from storage.state import SessionState
    from tools.context import ToolContext
    from ucp.discovery import UCPProfileDiscovery
    from ucp.signing import RequestSigner, generate_keypair

    # offline profile stub for one merchant + Shopify direct adapter for another
    stub_path = tmp_path / "profiles.json"
    stub_path.write_text(
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
                    }
                }
            }
        )
    )

    offline_http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    discovery = UCPProfileDiscovery(tmp_db, http_client=offline_http, stub_path=stub_path)

    private_pem, _, _ = generate_keypair("k1")
    signer = RequestSigner(private_pem, key_id="k1")

    shopify = ShopifyMCPAdapter("demo-shop.myshopify.com", StubShopifyTransport())
    gateway = MerchantGateway(
        discovery=discovery,
        signer=signer,
        direct_adapters={"demo-shop.myshopify.com": shopify},
    )

    stripe = StripeAdapter(api_key=None)
    payments = PaymentGateway(ap2, stripe)
    user = UserProfile(
        user_id="user_1",
        name="Alex",
        payment_method_id="pm_test_card",
        preferred_categories=["apparel", "running"],
    )
    session = SessionState(user_id=user.user_id)

    return ToolContext(
        db=tmp_db,
        ap2=ap2,
        merchant_gateway=gateway,
        payment_gateway=payments,
        spending_limiter=SpendingLimiter(tmp_db),
        confidence_checker=ConfidenceChecker(threshold=0.8),
        user=user,
        session=session,
    )


@pytest.fixture
def multi_merchant_ctx(tmp_db, ap2, tmp_path):
    """ToolContext with all three production demo merchants registered.

    Use this for cross-merchant scenarios — the default ``tool_ctx`` only has
    one stub merchant. Seeded with the real ``config/catalogue.py`` data so
    tests cover the same products the user sees in main.py.
    """
    import json
    import httpx

    from adapters.shopify_mcp import ShopifyMCPAdapter, StubShopifyTransport
    from adapters.stripe import StripeAdapter
    from config.catalogue import MERCHANTS
    from gateway.merchant_gateway import MerchantGateway
    from gateway.payment_gateway import PaymentGateway
    from guardrails.confidence import ConfidenceChecker
    from guardrails.spending import SpendingLimiter
    from models.user import Address, UserProfile
    from storage.state import SessionState
    from tools.context import ToolContext
    from ucp.discovery import UCPProfileDiscovery
    from ucp.signing import RequestSigner, generate_keypair

    stub_path = tmp_path / "profiles.json"
    stub_path.write_text(json.dumps({"profiles": {}}))
    offline_http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    discovery = UCPProfileDiscovery(tmp_db, http_client=offline_http, stub_path=stub_path)

    private_pem, _, _ = generate_keypair("k1")
    signer = RequestSigner(private_pem, key_id="k1")

    direct_adapters = {}
    for domain, seed in MERCHANTS.items():
        direct_adapters[domain] = ShopifyMCPAdapter(
            domain, StubShopifyTransport(seed_products=seed)
        )
    gateway = MerchantGateway(discovery=discovery, signer=signer, direct_adapters=direct_adapters)

    stripe = StripeAdapter(api_key=None)
    user = UserProfile(
        user_id="user_1",
        name="Alex",
        payment_method_id="pm_test_card",
        preferred_categories=["running", "electronics", "lifestyle"],
        addresses=[
            Address(
                line1="1 Demo St",
                city="San Francisco",
                region="CA",
                postal_code="94110",
                country="US",
                is_default_shipping=True,
                is_default_billing=True,
            )
        ],
    )
    return ToolContext(
        db=tmp_db,
        ap2=ap2,
        merchant_gateway=gateway,
        payment_gateway=PaymentGateway(ap2, stripe),
        spending_limiter=SpendingLimiter(tmp_db),
        confidence_checker=ConfidenceChecker(threshold=0.8),
        user=user,
        session=SessionState(user_id=user.user_id),
    )
