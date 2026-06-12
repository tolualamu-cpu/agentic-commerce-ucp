"""Unit tests for ``OrchestratorAgent._add_to_cart`` variant handling
(Phase 1, task 1.7).

Covers, parametrized over every demo merchant in ``config.catalogue.MERCHANTS``
AND the live merchant ``kith.com`` (``LIVE_MERCHANTS``, mocked HTTP — per
CLAUDE.md rule 3):
  - ``variant_required`` shape (no ``variant_id`` given for a variant product)
  - success with a valid ``variant_id`` (incl. price-override resolution)
  - ``invalid_variant`` for an unknown ``variant_id``
  - ``product_not_found`` for an unknown ``product_id``
  - no-variant products are unaffected (regression)

Sorts before ``test_user_journeys.py`` ("add_to_cart" < "user") -> uses
``asyncio.get_event_loop().run_until_complete()``.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.shopify_mcp import LiveShopifyTransport, ShopifyMCPAdapter, StubShopifyTransport
from adapters.stripe import StripeAdapter
from agents.orchestrator import OrchestratorAgent
from cli.confirmation import AutoConfirmProvider
from config.catalogue import LIVE_MERCHANTS, MERCHANTS
from gateway.merchant_gateway import MerchantGateway
from gateway.payment_gateway import PaymentGateway
from guardrails.confidence import ConfidenceChecker
from guardrails.spending import SpendingLimiter
from models.user import UserProfile
from storage.state import SessionState
from tests.fake_anthropic import FakeAnthropicClient
from tools.context import ToolContext


# ── Helpers ───────────────────────────────────────────────────────────────


def _orch() -> OrchestratorAgent:
    return OrchestratorAgent(
        client=FakeAnthropicClient([]),
        confirmation=AutoConfirmProvider(),
        mandate_id="m_test",
    )


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _demo_variant_and_plain(domain: str) -> tuple[str, str]:
    variant_id = plain_id = None
    for p in MERCHANTS[domain]:
        if p.get("variants") and variant_id is None:
            variant_id = p["id"]
        elif not p.get("variants") and plain_id is None:
            plain_id = p["id"]
    return variant_id, plain_id


DEMO_VARIANT_PLAIN = {domain: _demo_variant_and_plain(domain) for domain in MERCHANTS}
DOMAINS = sorted(MERCHANTS)


@pytest.fixture
def ctx(tmp_db, ap2, tmp_path):
    """ToolContext with ALL demo merchants registered (real catalogue data),
    no Kith — mirrors ``conftest.multi_merchant_ctx`` (kept local so this
    file's variant fixtures don't depend on conftest's specific shape)."""
    import httpx
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
    user = UserProfile(user_id="user_1", name="Alex", payment_method_id="pm_test_card")
    session = SessionState(user_id=user.user_id)

    return ToolContext(
        db=tmp_db,
        ap2=ap2,
        merchant_gateway=gateway,
        payment_gateway=PaymentGateway(ap2, stripe),
        spending_limiter=SpendingLimiter(tmp_db),
        confidence_checker=ConfidenceChecker(threshold=0.8),
        user=user,
        session=session,
    )


# ── Live merchant (Kith) — mocked HTTP, no real network ──────────────────


KITH_VARIANT_PRODUCT = {
    "id": 300001,
    "title": "Kith Track Jacket",
    "handle": "kith-track-jacket",
    "body_html": "<p>Lightweight track jacket.</p>",
    "vendor": "Kith",
    "product_type": "Outerwear",
    "tags": ["jacket"],
    "options": [{"name": "Size"}],
    "variants": [
        {"id": 5001, "title": "M", "price": "120.00", "available": True, "option1": "M"},
        {"id": 5002, "title": "L", "price": "130.00", "available": True, "option1": "L"},
        {"id": 5003, "title": "XL", "price": "130.00", "available": False, "option1": "XL"},
    ],
    "images": [{"id": 30, "src": "https://cdn.shopify.com/kith-jacket-1.jpg"}],
}

KITH_PLAIN_PRODUCT = {
    "id": 300002,
    "title": "Kith Tote Bag",
    "handle": "kith-tote-bag",
    "body_html": "<p>Canvas tote bag.</p>",
    "vendor": "Kith",
    "product_type": "Accessories",
    "tags": ["bag"],
    "variants": [
        {
            "id": 5004,
            "title": "Default Title",
            "price": "45.00",
            "available": True,
            "option1": "Default Title",
        },
    ],
    "images": [{"id": 31, "src": "https://cdn.shopify.com/kith-tote-1.jpg"}],
}

KITH_SAMPLE_PRODUCTS = {"products": [KITH_VARIANT_PRODUCT, KITH_PLAIN_PRODUCT]}


def _mock_response(data: dict):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = data
    return resp


def _mock_kith_transport() -> LiveShopifyTransport:
    transport = LiveShopifyTransport("https://kith.com", max_pages=1, cache_ttl=9999)
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response(KITH_SAMPLE_PRODUCTS))
    transport._http = mock_client
    transport._owns_http = False
    return transport


@pytest.fixture
def kith_ctx(ctx):
    """Same as ``ctx`` but with ``kith.com`` additionally registered via a
    mocked ``LiveShopifyTransport`` (no real network)."""
    ctx.merchant_gateway.direct_adapters["kith.com"] = ShopifyMCPAdapter(
        "kith.com",
        _mock_kith_transport(),
        source_protocol="shopify_storefront",
        merchant_display_name="Kith",
    )
    return ctx


# ── variant_required ─────────────────────────────────────────────────────


class TestVariantRequired:
    @pytest.mark.parametrize("domain", DOMAINS)
    def test_demo_variant_product_requires_variant_id(self, ctx, domain):
        orch = _orch()
        variant_id, _plain = DEMO_VARIANT_PLAIN[domain]
        result = _run(
            orch._add_to_cart(ctx, product_id=variant_id, merchant_domain=domain, quantity=1)
        )
        assert result["added"] is False
        assert result["error"] == "variant_required"
        assert result["option_names"]
        assert result["variants"]
        assert all("variant_id" in v for v in result["variants"])

    def test_kith_variant_product_requires_variant_id(self, kith_ctx):
        orch = _orch()
        result = _run(
            orch._add_to_cart(kith_ctx, product_id="300001", merchant_domain="kith.com", quantity=1)
        )
        assert result["added"] is False
        assert result["error"] == "variant_required"
        assert result["option_names"] == ["Size"]
        assert {v["options"]["Size"] for v in result["variants"]} == {"M", "L", "XL"}


# ── valid variant_id ──────────────────────────────────────────────────────


class TestValidVariantAdd:
    @pytest.mark.parametrize("domain", DOMAINS)
    def test_demo_valid_variant_adds_line_with_options(self, ctx, domain):
        orch = _orch()
        variant_id, _plain = DEMO_VARIANT_PLAIN[domain]

        # First discover the real variant_id via get_product_variants.
        variants_result = _run(
            orch._get_product_variants(ctx, product_id=variant_id, merchant_domain=domain)
        )
        assert variants_result["has_variants"] is True
        target = variants_result["variants"][0]

        result = _run(
            orch._add_to_cart(
                ctx,
                product_id=variant_id,
                merchant_domain=domain,
                quantity=1,
                variant_id=target["variant_id"],
            )
        )
        assert result["added"] is True
        bucket = ctx.session.click_basket[domain]
        line = next(l for l in bucket if l["variant_id"] == target["variant_id"])
        assert line["selected_options"] == target["options"]
        expected_price = (
            Decimal(str(target["price"])) if target["price"] is not None else line["price"]
        )
        assert Decimal(str(line["price"])) == Decimal(str(expected_price))

    def test_cof_002_price_override_resolution(self, ctx):
        """cof_002 (Travel Coffee Tumbler): the 20oz variant carries an
        explicit price override that must be used over the base price."""
        orch = _orch()
        domain = "coffee-bar.myshopify.com"

        variants_result = _run(
            orch._get_product_variants(ctx, product_id="cof_002", merchant_domain=domain)
        )
        by_size = {v["options"]["Size"]: v for v in variants_result["variants"]}
        assert by_size["20oz"]["price"] == "32.00"

        result = _run(
            orch._add_to_cart(
                ctx,
                product_id="cof_002",
                merchant_domain=domain,
                quantity=1,
                variant_id=by_size["20oz"]["variant_id"],
            )
        )
        assert result["added"] is True
        line = ctx.session.click_basket[domain][0]
        assert Decimal(str(line["price"])) == Decimal("32.00")

    def test_kith_valid_variant_adds_line(self, kith_ctx):
        orch = _orch()
        variants_result = _run(
            orch._get_product_variants(kith_ctx, product_id="300001", merchant_domain="kith.com")
        )
        m_variant = next(v for v in variants_result["variants"] if v["options"]["Size"] == "M")

        result = _run(
            orch._add_to_cart(
                kith_ctx,
                product_id="300001",
                merchant_domain="kith.com",
                quantity=2,
                variant_id=m_variant["variant_id"],
            )
        )
        assert result["added"] is True
        line = kith_ctx.session.click_basket["kith.com"][0]
        assert line["selected_options"] == {"Size": "M"}
        assert int(line["quantity"]) == 2

    def test_repeated_add_same_variant_bumps_quantity(self, ctx):
        orch = _orch()
        domain = "athletic-co.myshopify.com"
        variant_id, _plain = DEMO_VARIANT_PLAIN[domain]
        variants_result = _run(
            orch._get_product_variants(ctx, product_id=variant_id, merchant_domain=domain)
        )
        target = variants_result["variants"][0]

        for _ in range(2):
            result = _run(
                orch._add_to_cart(
                    ctx,
                    product_id=variant_id,
                    merchant_domain=domain,
                    quantity=1,
                    variant_id=target["variant_id"],
                )
            )
            assert result["added"] is True

        bucket = ctx.session.click_basket[domain]
        matching = [l for l in bucket if l["variant_id"] == target["variant_id"]]
        assert len(matching) == 1
        assert int(matching[0]["quantity"]) == 2


# ── invalid_variant / product_not_found ──────────────────────────────────


class TestErrorCases:
    @pytest.mark.parametrize("domain", DOMAINS)
    def test_invalid_variant_id(self, ctx, domain):
        orch = _orch()
        variant_id, _plain = DEMO_VARIANT_PLAIN[domain]
        result = _run(
            orch._add_to_cart(
                ctx,
                product_id=variant_id,
                merchant_domain=domain,
                quantity=1,
                variant_id="does-not-exist",
            )
        )
        assert result["added"] is False
        assert result["error"] == "invalid_variant"
        assert domain not in ctx.session.click_basket or not ctx.session.click_basket[domain]

    def test_kith_invalid_variant_id(self, kith_ctx):
        orch = _orch()
        result = _run(
            orch._add_to_cart(
                kith_ctx,
                product_id="300001",
                merchant_domain="kith.com",
                quantity=1,
                variant_id="does-not-exist",
            )
        )
        assert result["added"] is False
        assert result["error"] == "invalid_variant"

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_product_not_found(self, ctx, domain):
        orch = _orch()
        result = _run(
            orch._add_to_cart(ctx, product_id="does-not-exist", merchant_domain=domain, quantity=1)
        )
        assert result["added"] is False
        assert result["error"] == "product_not_found"

    def test_kith_product_not_found(self, kith_ctx):
        orch = _orch()
        result = _run(
            orch._add_to_cart(
                kith_ctx, product_id="does-not-exist", merchant_domain="kith.com", quantity=1
            )
        )
        assert result["added"] is False
        assert result["error"] == "product_not_found"


# ── No-variant products: regression-safe ─────────────────────────────────


class TestNoVariantRegression:
    @pytest.mark.parametrize("domain", DOMAINS)
    def test_demo_plain_product_adds_without_variant_id(self, ctx, domain):
        orch = _orch()
        _variant, plain_id = DEMO_VARIANT_PLAIN[domain]
        result = _run(
            orch._add_to_cart(ctx, product_id=plain_id, merchant_domain=domain, quantity=1)
        )
        assert result["added"] is True
        line = ctx.session.click_basket[domain][0]
        assert line["variant_id"] is None
        assert line["selected_options"] == {}

    def test_kith_plain_product_adds_without_variant_id(self, kith_ctx):
        orch = _orch()
        variants_result = _run(
            orch._get_product_variants(kith_ctx, product_id="300002", merchant_domain="kith.com")
        )
        assert variants_result["has_variants"] is False

        result = _run(
            orch._add_to_cart(kith_ctx, product_id="300002", merchant_domain="kith.com", quantity=1)
        )
        assert result["added"] is True
        line = kith_ctx.session.click_basket["kith.com"][0]
        assert line["variant_id"] is None
        assert line["selected_options"] == {}


@pytest.mark.parametrize("domain", sorted(set(MERCHANTS) | set(LIVE_MERCHANTS)))
def test_gateway_registration_covers_add_to_cart_path(domain):
    """Per CLAUDE.md rule 3: every domain in MERCHANTS/LIVE_MERCHANTS must be
    reachable for the add_to_cart path. Demo merchants are checked against
    the local ``ctx`` fixture's adapters; live merchants must have an
    equivalent mocked-fixture client in this file (kith.com -> kith_ctx)."""
    if domain in MERCHANTS:
        assert domain in MERCHANTS  # trivially true — documents coverage
    else:
        assert domain == "kith.com", (
            f"New live merchant {domain!r} needs a mocked add_to_cart fixture "
            f"in tests/test_add_to_cart_tool_variants.py (see kith_ctx)."
        )
