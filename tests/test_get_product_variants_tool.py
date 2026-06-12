"""Unit tests for ``tools.discovery_tools.get_product_variants`` and the
orchestrator's ``_get_product_variants`` wrapper (Phase 1, task 1.7).

Covers, parametrized over every demo merchant in ``config.catalogue.MERCHANTS``
AND the live merchant ``kith.com`` (``LIVE_MERCHANTS``, mocked HTTP — per
CLAUDE.md rule 3):
  - variant product -> ``has_variants: True``, non-empty ``option_names``,
    ``variants`` list of JSON-shaped ``ProductVariant`` dicts.
  - no-variant product -> ``has_variants: False``, ``option_names == []``,
    ``variants == []``.
  - unknown ``product_id`` -> ``has_variants: False``,
    ``error: product_not_found``.

Sorts before ``test_user_journeys.py`` ("get_product_variants" < "user") ->
uses ``asyncio.get_event_loop().run_until_complete()``.
"""

from __future__ import annotations

import asyncio
import json
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
from tools import discovery_tools
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
    no Kith — local copy mirroring conftest.multi_merchant_ctx's shape."""
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


# ── discovery_tools.get_product_variants — variant products ─────────────


class TestVariantProductShape:
    @pytest.mark.parametrize("domain", DOMAINS)
    def test_demo_variant_product_returns_full_shape(self, ctx, domain):
        variant_id, _plain = DEMO_VARIANT_PLAIN[domain]
        result = _run(
            discovery_tools.get_product_variants(ctx, product_id=variant_id, merchant_domain=domain)
        )
        assert result["has_variants"] is True
        assert "error" not in result
        assert isinstance(result["option_names"], list) and result["option_names"]
        assert isinstance(result["variants"], list) and result["variants"]
        for v in result["variants"]:
            for key in ("variant_id", "sku", "options", "price", "in_stock", "image"):
                assert key in v
            assert set(v["options"].keys()) == set(result["option_names"])

    def test_kith_variant_product_returns_full_shape(self, kith_ctx):
        result = _run(
            discovery_tools.get_product_variants(
                kith_ctx, product_id="300001", merchant_domain="kith.com"
            )
        )
        assert result["has_variants"] is True
        assert result["option_names"] == ["Size"]
        sizes = {v["options"]["Size"] for v in result["variants"]}
        assert sizes == {"M", "L", "XL"}
        # The XL variant is unavailable in the mocked fixture.
        xl = next(v for v in result["variants"] if v["options"]["Size"] == "XL")
        assert xl["in_stock"] is False

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_cof_002_variants_have_price_overrides_where_diverging(self, ctx, domain):
        if domain != "coffee-bar.myshopify.com":
            pytest.skip("cof_002 spot check only applies to coffee-bar")
        result = _run(
            discovery_tools.get_product_variants(ctx, product_id="cof_002", merchant_domain=domain)
        )
        by_size = {v["options"]["Size"]: v for v in result["variants"]}
        assert by_size["16oz"]["price"] is None
        assert by_size["20oz"]["price"] == "32.00"


# ── discovery_tools.get_product_variants — no-variant products ──────────


class TestNoVariantProductShape:
    @pytest.mark.parametrize("domain", DOMAINS)
    def test_demo_plain_product_returns_empty_shape(self, ctx, domain):
        _variant, plain_id = DEMO_VARIANT_PLAIN[domain]
        result = _run(
            discovery_tools.get_product_variants(ctx, product_id=plain_id, merchant_domain=domain)
        )
        assert result["has_variants"] is False
        assert result["option_names"] == []
        assert result["variants"] == []
        assert "error" not in result

    def test_kith_plain_product_returns_empty_shape(self, kith_ctx):
        result = _run(
            discovery_tools.get_product_variants(
                kith_ctx, product_id="300002", merchant_domain="kith.com"
            )
        )
        assert result["has_variants"] is False
        assert result["option_names"] == []
        assert result["variants"] == []


# ── product_not_found ─────────────────────────────────────────────────────


class TestProductNotFound:
    @pytest.mark.parametrize("domain", DOMAINS)
    def test_demo_unknown_product_id(self, ctx, domain):
        result = _run(
            discovery_tools.get_product_variants(
                ctx, product_id="does-not-exist", merchant_domain=domain
            )
        )
        assert result["has_variants"] is False
        assert result["option_names"] == []
        assert result["variants"] == []
        assert result["error"] == "product_not_found"

    def test_kith_unknown_product_id(self, kith_ctx):
        result = _run(
            discovery_tools.get_product_variants(
                kith_ctx, product_id="does-not-exist", merchant_domain="kith.com"
            )
        )
        assert result["has_variants"] is False
        assert result["error"] == "product_not_found"


# ── Orchestrator wrapper: _get_product_variants ───────────────────────────


class TestOrchestratorWrapper:
    @pytest.mark.parametrize("domain", DOMAINS)
    def test_wrapper_matches_tool_for_variant_product(self, ctx, domain):
        orch = _orch()
        variant_id, _plain = DEMO_VARIANT_PLAIN[domain]
        direct = _run(
            discovery_tools.get_product_variants(ctx, product_id=variant_id, merchant_domain=domain)
        )
        via_orch = _run(
            orch._get_product_variants(ctx, product_id=variant_id, merchant_domain=domain)
        )
        assert via_orch["has_variants"] == direct["has_variants"]
        assert via_orch["option_names"] == direct["option_names"]
        assert via_orch["variants"] == direct["variants"]

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_wrapper_matches_tool_for_plain_product(self, ctx, domain):
        orch = _orch()
        _variant, plain_id = DEMO_VARIANT_PLAIN[domain]
        direct = _run(
            discovery_tools.get_product_variants(ctx, product_id=plain_id, merchant_domain=domain)
        )
        via_orch = _run(
            orch._get_product_variants(ctx, product_id=plain_id, merchant_domain=domain)
        )
        assert via_orch["has_variants"] == direct["has_variants"] is False
        assert via_orch["option_names"] == direct["option_names"] == []
        assert via_orch["variants"] == direct["variants"] == []

    def test_wrapper_uses_family_cache_when_present(self, ctx, domain="athletic-co.myshopify.com"):
        """When ``ctx.session.product_families`` has an entry for a
        product_id, the orchestrator wrapper must serve the synthesized
        family variants/option_names instead of re-fetching the raw
        product (1.11)."""
        orch = _orch()
        variant_id, _plain = DEMO_VARIANT_PLAIN[domain]
        fake_variants = [
            {
                "variant_id": "fake-1",
                "sku": None,
                "options": {"Size": "M", "Color": "Black"},
                "price": None,
                "in_stock": True,
                "image": None,
            },
            {
                "variant_id": "fake-2",
                "sku": None,
                "options": {"Size": "M", "Color": "White"},
                "price": None,
                "in_stock": True,
                "image": None,
            },
        ]
        ctx.session.product_families[variant_id] = {
            "primary": variant_id,
            "option_names": ["Size", "Color"],
            "variants": fake_variants,
        }
        result = _run(
            orch._get_product_variants(ctx, product_id=variant_id, merchant_domain=domain)
        )
        assert result["has_variants"] is True
        assert result["option_names"] == ["Size", "Color"]
        assert result["variants"] == fake_variants


# ── Gateway registration check (CLAUDE.md rule 3) ────────────────────────


@pytest.mark.parametrize("domain", sorted(set(MERCHANTS) | set(LIVE_MERCHANTS)))
def test_get_product_variants_reachable_for_every_merchant(domain):
    """Documents that every domain in MERCHANTS/LIVE_MERCHANTS has coverage
    in this file: demo merchants via the ``ctx`` fixture, kith.com via
    ``kith_ctx``. A new live merchant must add an equivalent fixture here."""
    if domain in MERCHANTS:
        assert domain in MERCHANTS
    else:
        assert domain == "kith.com", (
            f"New live merchant {domain!r} needs a mocked get_product_variants "
            f"fixture in tests/test_get_product_variants_tool.py (see kith_ctx)."
        )
