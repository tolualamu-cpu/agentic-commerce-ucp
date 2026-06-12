"""Unit tests for demo-merchant variant/option seed data (Phase 1, task 1.4).

Iterates EVERY product in ``config.catalogue.MERCHANTS`` (per CLAUDE.md rule
3 — never hardcode merchant/product names beyond spot-checks) and verifies:

  - the raw seed shape (``"variants"``/``"options"`` keys) is well-formed and
    Shopify-shaped (``id``, ``title``, ``price``, ``available``, ``sku``,
    ``option1``/``option2``);
  - it normalises cleanly through ``adapters.shopify_mcp._normalise_variants``
    into ``ProductVariant``/``option_names`` with the expected counts and
    dimension keys;
  - spot-checks: ``ath_002`` (Trail Runner Pro, all variants unavailable),
    ``cof_002`` (Travel Coffee Tumbler, per-size price overrides), and the
    no-variant products (single-SKU, ``variants == []``).

Sorts before ``test_user_journeys.py`` ("catalogue_variants" < "user") ->
no ``asyncio.run()`` needed here (pure sync model/data tests), so the
asyncio-ordering rule doesn't apply, but we avoid it anyway for safety.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from adapters.shopify_mcp import _normalise_variants
from config.catalogue import MERCHANTS

ALL_PRODUCTS = [(domain, product) for domain, products in MERCHANTS.items() for product in products]
ALL_PRODUCT_IDS = [f"{domain}:{p['id']}" for domain, p in ALL_PRODUCTS]


# ── Raw seed-data shape ──────────────────────────────────────────────────


class TestRawSeedDataShape:
    @pytest.mark.parametrize("domain,product", ALL_PRODUCTS, ids=ALL_PRODUCT_IDS)
    def test_variants_and_options_are_consistent(self, domain, product):
        variants = product.get("variants")
        options = product.get("options")

        if not variants:
            # No-variant (single-SKU) product: neither key carries real data.
            assert not options, (
                f"{domain}:{product['id']} has 'options' but no 'variants' — "
                f"single-SKU products must omit both or leave both empty."
            )
            return

        assert isinstance(options, list) and options, (
            f"{domain}:{product['id']} has 'variants' but no non-empty 'options' list."
        )
        assert all(isinstance(o, str) and o for o in options), (
            f"{domain}:{product['id']} 'options' must be a list of non-empty strings."
        )
        assert len(options) <= 2, (
            f"{domain}:{product['id']} has >2 option dimensions — "
            f"normaliser only maps option1/option2."
        )

    @pytest.mark.parametrize("domain,product", ALL_PRODUCTS, ids=ALL_PRODUCT_IDS)
    def test_each_variant_dict_is_shopify_shaped(self, domain, product):
        variants = product.get("variants") or []
        for v in variants:
            for key in ("id", "title", "price", "available", "sku", "option1"):
                assert key in v, f"{domain}:{product['id']} variant {v.get('id')!r} missing {key!r}"
            assert isinstance(v["id"], str) and v["id"]
            assert isinstance(v["available"], bool)
            # price must parse as a Decimal
            Decimal(str(v["price"]))

    @pytest.mark.parametrize("domain,product", ALL_PRODUCTS, ids=ALL_PRODUCT_IDS)
    def test_variant_ids_unique_within_product(self, domain, product):
        variants = product.get("variants") or []
        ids = [v["id"] for v in variants]
        assert len(ids) == len(set(ids)), (
            f"{domain}:{product['id']} has duplicate variant ids: {ids}"
        )

    @pytest.mark.parametrize("domain,product", ALL_PRODUCTS, ids=ALL_PRODUCT_IDS)
    def test_option_dimensions_populate_option1_option2(self, domain, product):
        variants = product.get("variants") or []
        options = product.get("options") or []
        for v in variants:
            # option1 always present for variant products.
            if options:
                assert v.get("option1") not in (None, ""), (
                    f"{domain}:{product['id']} variant {v['id']} missing option1 "
                    f"despite declaring options={options}"
                )
            if len(options) >= 2:
                assert v.get("option2") not in (None, ""), (
                    f"{domain}:{product['id']} variant {v['id']} missing option2 "
                    f"despite declaring 2D options={options}"
                )
            else:
                assert v.get("option2") in (None,), (
                    f"{domain}:{product['id']} variant {v['id']} has option2 set "
                    f"but only declares 1 option dimension"
                )


# ── Normalisation through the shared adapter helper ──────────────────────


class TestNormaliseVariants:
    @pytest.mark.parametrize("domain,product", ALL_PRODUCTS, ids=ALL_PRODUCT_IDS)
    def test_normalisation_round_trip(self, domain, product):
        raw_variants = product.get("variants")
        raw_options = product.get("options")
        # Seed data uses bare strings ("Size") for options; the normaliser
        # accepts either bare strings or {"name": ...} dicts.
        variants, option_names = _normalise_variants(raw_variants, raw_options)

        if not raw_variants:
            assert variants == []
            assert option_names == []
            return

        assert option_names == raw_options
        assert len(variants) == len(raw_variants)

        for variant, raw in zip(variants, raw_variants):
            assert variant.variant_id == raw["id"]
            assert variant.sku == raw.get("sku")
            assert variant.in_stock == raw["available"]
            # options dict carries one entry per declared dimension
            assert set(variant.options.keys()) == set(option_names)
            if len(option_names) >= 1:
                assert variant.options[option_names[0]] == raw["option1"]
            if len(option_names) >= 2:
                assert variant.options[option_names[1]] == raw["option2"]

    @pytest.mark.parametrize("domain,product", ALL_PRODUCTS, ids=ALL_PRODUCT_IDS)
    def test_variant_price_is_none_unless_diverging(self, domain, product):
        """``ProductVariant.price`` should only be set when it diverges from
        the product's base ('starting at' / minimum) price."""
        raw_variants = product.get("variants")
        raw_options = product.get("options")
        variants, _ = _normalise_variants(raw_variants, raw_options)
        if not variants:
            return

        base_price = min(Decimal(str(v.get("price", "0"))) for v in raw_variants)
        for variant, raw in zip(variants, raw_variants):
            raw_price = Decimal(str(raw["price"]))
            if raw_price == base_price:
                assert variant.price is None
            else:
                assert variant.price == raw_price


# ── Spot checks ───────────────────────────────────────────────────────────


_NO_VARIANT_PRODUCTS = {
    "athletic-co.myshopify.com": ["ath_005"],
    "audio-hub.myshopify.com": ["aud_003", "aud_006"],
    "coffee-bar.myshopify.com": ["cof_001", "cof_004", "cof_006", "cof_007", "cof_008"],
}


class TestSpotChecks:
    def test_ath_002_all_variants_unavailable(self):
        product = next(p for p in MERCHANTS["athletic-co.myshopify.com"] if p["id"] == "ath_002")
        assert product["available"] is False
        variants = product.get("variants") or []
        assert variants, "ath_002 should still carry size variants"
        assert all(v["available"] is False for v in variants), (
            "Trail Runner Pro must remain out-of-stock across ALL size variants "
            "(regression: tests/test_*out_of_stock* rely on this)."
        )

    def test_cof_002_price_overrides_by_size(self):
        product = next(p for p in MERCHANTS["coffee-bar.myshopify.com"] if p["id"] == "cof_002")
        variants, option_names = _normalise_variants(
            product.get("variants"), product.get("options")
        )
        assert option_names == ["Size"]
        # base_price = min(variant prices) = 28.00 (16oz). The 16oz variant
        # matches the base price so its `.price` override is None (use
        # ProductResult.price); the 20oz variant diverges and carries an
        # explicit Decimal override.
        by_size = {v.options["Size"]: v for v in variants}
        assert by_size["16oz"].price is None
        assert by_size["20oz"].price == Decimal("32.00")

        # Effective prices (falling back to the raw seed price for 16oz)
        # are nonetheless distinct per size.
        raw_prices = {v["option1"]: Decimal(str(v["price"])) for v in product["variants"]}
        assert raw_prices["16oz"] == Decimal("28.00")
        assert raw_prices["20oz"] == Decimal("32.00")
        assert raw_prices["16oz"] != raw_prices["20oz"]

    @pytest.mark.parametrize(
        "domain,product_id",
        [(domain, pid) for domain, ids in _NO_VARIANT_PRODUCTS.items() for pid in ids],
    )
    def test_single_sku_products_have_no_variants(self, domain, product_id):
        product = next(p for p in MERCHANTS[domain] if p["id"] == product_id)
        assert not product.get("variants")
        assert not product.get("options")
        variants, option_names = _normalise_variants(
            product.get("variants"), product.get("options")
        )
        assert variants == []
        assert option_names == []

    @pytest.mark.parametrize("domain", sorted(MERCHANTS))
    def test_every_merchant_has_at_least_one_variant_and_one_plain_product(self, domain):
        """Every demo merchant exercises both the variant-picker path and the
        direct-add path (CLAUDE.md rule 3: span every merchant)."""
        products = MERCHANTS[domain]
        has_variant = any(p.get("variants") for p in products)
        has_plain = any(not p.get("variants") for p in products)
        assert has_variant, f"{domain} has no variant product"
        assert has_plain, f"{domain} has no single-SKU product"

    @pytest.mark.parametrize("domain", sorted(MERCHANTS))
    def test_at_least_one_two_dimensional_variant_product_exists_or_not(self, domain):
        """Document which merchants have 2D (Size+Color / Size+Width) variant
        products — not all need to, but Athletic Co does (ath_003/004/007)."""
        products = MERCHANTS[domain]
        two_d = [p["id"] for p in products if len(p.get("options") or []) == 2]
        if domain == "athletic-co.myshopify.com":
            assert two_d, "Athletic Co should have at least one 2D-variant product"
