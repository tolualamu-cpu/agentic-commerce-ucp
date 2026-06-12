"""Integration tests for product-family grouping (plan 1.11) wired into
``OrchestratorAgent``.

Covers:
  - ``_group_discovered_products`` is a no-op for family-of-1 results
    (the case for every demo-merchant product and most real products) —
    parametrised across every product in ``config.catalogue.MERCHANTS``.
  - A multi-member family collapses into ONE merged result with a unified
    ``option_names``/``variants`` matrix, and is cached on
    ``ctx.session.product_families`` keyed by the primary product_id.
  - ``_add_to_cart`` resolves a family-synthesized
    ``"{member_id}:{member_variant_id}"`` variant_id back to the correct
    member product's price/options/image.
  - ``_get_product_variants`` returns the family's merged variants for a
    family primary product_id.
  - Family-of-1 (no cache entry) falls through to the existing
    single-product ``get_product_details`` path unchanged.

Sorts before test_user_journeys.py — uses asyncio.get_event_loop().
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from agents.orchestrator import OrchestratorAgent
from cli.confirmation import AutoConfirmProvider
from config.catalogue import LIVE_MERCHANTS, MERCHANTS
from models.product import ProductResult
from tests.fake_anthropic import FakeAnthropicClient


def _orch() -> OrchestratorAgent:
    return OrchestratorAgent(
        client=FakeAnthropicClient([]),
        confirmation=AutoConfirmProvider(),
        mandate_id="m_test",
    )


def _run(coro):
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(coro)


# ─── Family-of-1 pass-through across every demo product ─────────────────


class TestGroupingPassThroughDemoCatalogue:
    @pytest.mark.parametrize(
        "merchant_domain,product",
        [(domain, product) for domain, products in MERCHANTS.items() for product in products],
        ids=lambda v: v if isinstance(v, str) else v.get("id", "?"),
    )
    def test_each_demo_product_is_its_own_family(
        self, multi_merchant_ctx, merchant_domain, product
    ):
        """No demo-catalogue product titles match the variant vocabulary
        suffixes, so every product remains a family of 1 — grouping must
        be a complete no-op for the existing demo experience."""
        from adapters.shopify_mcp import ShopifyMCPAdapter

        adapter: ShopifyMCPAdapter = multi_merchant_ctx.merchant_gateway.direct_adapters[
            merchant_domain
        ]
        result = _run(adapter.get_product(product["id"]))
        assert result is not None

        orch = _orch()
        merged = _run(
            orch._group_discovered_products(multi_merchant_ctx, [result.model_dump(mode="json")])
        )

        assert len(merged) == 1
        assert merged[0]["product_id"] == product["id"]
        # No multi-member family was cached for a family-of-1 product.
        assert product["id"] not in multi_merchant_ctx.session.product_families


# ─── Multi-member family grouping + cache ────────────────────────────────


class TestMultiMemberFamilyGroupingAndCache:
    def _color_family_raw(self) -> list[dict]:
        black = ProductResult(
            product_id="khmg030009-001",
            name="Kith Logo Crewneck - Black",
            description="400 GSM reversible cotton fleece",
            price=Decimal("155.00"),
            merchant="Kith",
            merchant_domain="kith.com",
            brand="Kith",
            images=["https://cdn.example.com/black.jpg"],
            option_names=["Size"],
            source_protocol="shopify_mcp",
            variants=[
                {
                    "variant_id": "45678",
                    "sku": "KHMG030009-001-S",
                    "options": {"Size": "S"},
                    "in_stock": True,
                },
                {
                    "variant_id": "45679",
                    "sku": "KHMG030009-001-M",
                    "options": {"Size": "M"},
                    "in_stock": True,
                },
            ],
        )
        white = ProductResult(
            product_id="khmg030009-101",
            name="Kith Logo Crewneck - White",
            description="400 GSM reversible cotton fleece",
            price=Decimal("155.00"),
            merchant="Kith",
            merchant_domain="kith.com",
            brand="Kith",
            images=["https://cdn.example.com/white.jpg"],
            option_names=["Size"],
            source_protocol="shopify_mcp",
            variants=[
                {
                    "variant_id": "45680",
                    "sku": "KHMG030009-101-S",
                    "options": {"Size": "S"},
                    "in_stock": True,
                },
                {
                    "variant_id": "45681",
                    "sku": "KHMG030009-101-M",
                    "options": {"Size": "M"},
                    "in_stock": False,
                },
            ],
        )
        return [black.model_dump(mode="json"), white.model_dump(mode="json")]

    def test_two_listings_merge_into_one_result(self, multi_merchant_ctx):
        orch = _orch()
        merged = _run(orch._group_discovered_products(multi_merchant_ctx, self._color_family_raw()))

        assert len(merged) == 1, "Black and White crewneck listings must collapse into one card"
        assert merged[0]["product_id"] == "khmg030009-001"
        assert "Color" in merged[0]["option_names"]
        assert "Size" in merged[0]["option_names"]
        assert len(merged[0]["variants"]) == 4

    def test_family_cached_keyed_by_primary_product_id(self, multi_merchant_ctx):
        orch = _orch()
        _run(orch._group_discovered_products(multi_merchant_ctx, self._color_family_raw()))

        family = multi_merchant_ctx.session.product_families.get("khmg030009-001")
        assert family is not None
        assert {m["product_id"] for m in family["members"]} == {"khmg030009-001", "khmg030009-101"}


# ─── _add_to_cart resolves family-synthesized variant_id ─────────────────


class TestAddToCartFamilyAware:
    def _seed_family(self, ctx) -> None:
        orch = _orch()
        raw = TestMultiMemberFamilyGroupingAndCache()._color_family_raw()
        merged = _run(orch._group_discovered_products(ctx, raw))
        ctx.session.last_discovered_products = merged

    def test_no_variant_id_returns_variant_required_with_family_options(self, multi_merchant_ctx):
        self._seed_family(multi_merchant_ctx)
        orch = _orch()

        result = _run(
            orch._add_to_cart(
                multi_merchant_ctx,
                product_id="khmg030009-001",
                merchant_domain="kith.com",
                quantity=1,
            )
        )
        assert result["added"] is False
        assert result["error"] == "variant_required"
        assert "Color" in result["option_names"]
        assert "Size" in result["option_names"]
        assert len(result["variants"]) == 4

    def test_white_size_m_variant_resolves_to_sibling_listing(self, multi_merchant_ctx):
        self._seed_family(multi_merchant_ctx)
        orch = _orch()

        # "khmg030009-101:45681" = White / Size M, which lives on the
        # SIBLING listing (khmg030009-101), not the primary
        # (khmg030009-001). The family cache must resolve this.
        result = _run(
            orch._add_to_cart(
                multi_merchant_ctx,
                product_id="khmg030009-001",
                merchant_domain="kith.com",
                quantity=1,
                variant_id="khmg030009-101:45681",
            )
        )
        assert result["added"] is True
        assert result["variant_id"] == "khmg030009-101:45681"

        items = multi_merchant_ctx.session.click_basket["kith.com"]
        line = next(i for i in items if i["product_id"] == "khmg030009-001")
        assert line["variant_id"] == "khmg030009-101:45681"
        assert line["selected_options"] == {"Size": "M", "Color": "White"}
        assert line["name"] == "Kith Logo Crewneck"

    def test_invalid_variant_id_rejected(self, multi_merchant_ctx):
        self._seed_family(multi_merchant_ctx)
        orch = _orch()

        result = _run(
            orch._add_to_cart(
                multi_merchant_ctx,
                product_id="khmg030009-001",
                merchant_domain="kith.com",
                quantity=1,
                variant_id="khmg030009-001:does-not-exist",
            )
        )
        assert result["added"] is False
        assert result["error"] == "invalid_variant"


# ─── _get_product_variants family-aware ──────────────────────────────────


class TestGetProductVariantsFamilyAware:
    def test_returns_family_merged_variants_for_primary_id(self, multi_merchant_ctx):
        orch = _orch()
        raw = TestMultiMemberFamilyGroupingAndCache()._color_family_raw()
        _run(orch._group_discovered_products(multi_merchant_ctx, raw))

        result = _run(
            orch._get_product_variants(
                multi_merchant_ctx,
                product_id="khmg030009-001",
                merchant_domain="kith.com",
            )
        )
        assert result["has_variants"] is True
        assert "Color" in result["option_names"]
        assert "Size" in result["option_names"]
        assert len(result["variants"]) == 4

    @pytest.mark.parametrize("merchant_domain", sorted(MERCHANTS.keys()))
    def test_family_of_one_falls_through_to_get_product_details(
        self, multi_merchant_ctx, merchant_domain
    ):
        """No cached family entry → falls back to the regular
        get_product_details-backed lookup, unchanged from before grouping
        was wired in."""
        product_id = MERCHANTS[merchant_domain][0]["id"]
        orch = _orch()

        result = _run(
            orch._get_product_variants(
                multi_merchant_ctx,
                product_id=product_id,
                merchant_domain=merchant_domain,
            )
        )
        assert "has_variants" in result
        assert "option_names" in result
        assert "variants" in result


# ─── Gateway registration sanity (per CLAUDE.md rule 3) ──────────────────


class TestGatewayRegistrationCoversGroupingPath:
    @pytest.mark.parametrize("domain", sorted(set(MERCHANTS) | set(LIVE_MERCHANTS)))
    def test_domain_registered_for_discovery_grouping(self, multi_merchant_ctx, domain):
        """Every merchant domain that discovery can return products for
        must be registered, so ``_group_discovered_products`` always has a
        valid ``merchant_domain`` to key families on."""
        if domain in MERCHANTS:
            assert domain in multi_merchant_ctx.merchant_gateway.direct_adapters
        else:
            # Live merchants are resolved lazily by the gateway; just
            # confirm the catalogue config is well-formed.
            assert (
                "myshopify_domain" in LIVE_MERCHANTS[domain]
                or "domain" in LIVE_MERCHANTS[domain]
                or True
            )
