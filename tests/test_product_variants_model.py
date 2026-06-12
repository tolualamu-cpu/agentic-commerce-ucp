"""Unit tests for the canonical variant schema (Phase 1, task 1.1/1.2):

  - ``ProductVariant`` — defaults, validation, serialization round-trip.
  - ``ProductResult.variants`` / ``ProductResult.option_names``.
  - ``CartItem.variant_id`` / ``CartItem.selected_options``.

Pure model-level tests — no I/O, no merchant adapters. Sorts before
``test_user_journeys.py`` alphabetically ("product_variants" < "user"), so
per CLAUDE.md's asyncio test-ordering rule this file must avoid
``asyncio.run()`` / module-level ``asyncio.Queue()``. It doesn't need
asyncio at all.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from models.product import CartItem, ProductResult, ProductVariant


# ── ProductVariant ───────────────────────────────────────────────────────


class TestProductVariantDefaults:
    def test_minimal_variant_only_requires_variant_id(self):
        v = ProductVariant(variant_id="v1")
        assert v.variant_id == "v1"
        assert v.sku is None
        assert v.options == {}
        assert v.price is None
        assert v.in_stock is True
        assert v.image is None

    def test_missing_variant_id_raises(self):
        with pytest.raises(ValidationError):
            ProductVariant()

    def test_full_variant_construction(self):
        v = ProductVariant(
            variant_id="ath_001-8",
            sku="ATH-001-8",
            options={"Size": "8"},
            price=Decimal("129.99"),
            in_stock=True,
            image="https://images.unsplash.com/photo-abc?w=800&q=80",
        )
        assert v.sku == "ATH-001-8"
        assert v.options == {"Size": "8"}
        assert v.price == Decimal("129.99")
        assert v.image.startswith("https://")

    def test_out_of_stock_variant(self):
        v = ProductVariant(variant_id="v1", in_stock=False)
        assert v.in_stock is False

    def test_price_accepts_string_decimal(self):
        v = ProductVariant(variant_id="v1", price="32.00")
        assert v.price == Decimal("32.00")

    def test_price_none_means_use_product_price(self):
        v = ProductVariant(variant_id="v1", price=None)
        assert v.price is None


class TestProductVariantSerializationRoundTrip:
    def test_model_dump_json_mode_round_trip(self):
        v = ProductVariant(
            variant_id="cof_002-16oz",
            sku="COF-002-16",
            options={"Size": "16oz"},
            price=Decimal("28.00"),
            in_stock=True,
            image=None,
        )
        dumped = v.model_dump(mode="json")
        # Decimal serializes to a JSON-safe string.
        assert dumped["price"] == "28.00"
        assert dumped["options"] == {"Size": "16oz"}

        restored = ProductVariant.model_validate(dumped)
        assert restored == v

    def test_multi_dimension_options_round_trip(self):
        v = ProductVariant(
            variant_id="ath_003-S-Black",
            options={"Size": "S", "Color": "Black"},
        )
        dumped = v.model_dump(mode="json")
        restored = ProductVariant.model_validate(dumped)
        assert restored.options == {"Size": "S", "Color": "Black"}

    def test_no_variant_price_round_trips_as_none(self):
        v = ProductVariant(variant_id="v1", options={"Size": "M"})
        dumped = v.model_dump(mode="json")
        assert dumped["price"] is None
        restored = ProductVariant.model_validate(dumped)
        assert restored.price is None


# ── ProductResult.variants / option_names ────────────────────────────────


def _base_product_kwargs(**overrides) -> dict:
    kwargs = dict(
        product_id="ath_001",
        name="Demo Running Shoes",
        price=Decimal("129.99"),
        merchant="Athletic Co",
        merchant_domain="athletic-co.myshopify.com",
    )
    kwargs.update(overrides)
    return kwargs


class TestProductResultVariantFields:
    def test_defaults_are_empty(self):
        p = ProductResult(**_base_product_kwargs())
        assert p.variants == []
        assert p.option_names == []

    def test_default_title_sentinel_normalizes_to_empty(self):
        """Per 1.1: a single 'Default Title' variant must normalize to
        ``variants=[]``/``option_names=[]`` upstream (in the adapter) — at
        the model level, an empty list is the only valid 'no picker' shape."""
        p = ProductResult(**_base_product_kwargs(variants=[], option_names=[]))
        assert p.variants == []
        assert p.option_names == []

    def test_single_dimension_variants(self):
        variants = [
            ProductVariant(variant_id=f"ath_001-{size}", options={"Size": str(size)})
            for size in (8, 9, 10, 11, 12)
        ]
        p = ProductResult(**_base_product_kwargs(variants=variants, option_names=["Size"]))
        assert p.option_names == ["Size"]
        assert len(p.variants) == 5
        assert {v.options["Size"] for v in p.variants} == {"8", "9", "10", "11", "12"}

    def test_multi_dimension_variants(self):
        variants = [
            ProductVariant(
                variant_id=f"ath_003-{size}-{color}",
                options={"Size": size, "Color": color},
            )
            for size in ("S", "M")
            for color in ("Black", "Navy")
        ]
        p = ProductResult(
            **_base_product_kwargs(
                product_id="ath_003",
                name="Performance Running Shorts",
                variants=variants,
                option_names=["Size", "Color"],
            )
        )
        assert p.option_names == ["Size", "Color"]
        assert len(p.variants) == 4
        # Cross product covers every combination.
        combos = {(v.options["Size"], v.options["Color"]) for v in p.variants}
        assert combos == {("S", "Black"), ("S", "Navy"), ("M", "Black"), ("M", "Navy")}

    def test_price_override_variant(self):
        """cof_002-style: variant.price diverges from the base product price."""
        variants = [
            ProductVariant(
                variant_id="cof_002-16oz", options={"Size": "16oz"}, price=Decimal("28.00")
            ),
            ProductVariant(
                variant_id="cof_002-20oz", options={"Size": "20oz"}, price=Decimal("32.00")
            ),
        ]
        p = ProductResult(
            **_base_product_kwargs(
                product_id="cof_002",
                name="Travel Coffee Tumbler",
                price=Decimal("30.00"),
                variants=variants,
                option_names=["Size"],
            )
        )
        prices = {v.options["Size"]: v.price for v in p.variants}
        assert prices == {"16oz": Decimal("28.00"), "20oz": Decimal("32.00")}
        # Base price is independent of variant overrides.
        assert p.price == Decimal("30.00")

    def test_all_variants_out_of_stock(self):
        """ath_002 (Trail Runner Pro)-style: product overall out of stock,
        every variant individually marked unavailable."""
        variants = [
            ProductVariant(
                variant_id=f"ath_002-{size}", options={"Size": str(size)}, in_stock=False
            )
            for size in (8, 9, 10, 11, 12)
        ]
        p = ProductResult(
            **_base_product_kwargs(
                product_id="ath_002",
                name="Trail Runner Pro",
                in_stock=False,
                variants=variants,
                option_names=["Size"],
            )
        )
        assert p.in_stock is False
        assert all(v.in_stock is False for v in p.variants)

    def test_serialization_round_trip_with_variants(self):
        variants = [
            ProductVariant(variant_id="aud_001-black", options={"Color": "Black"}),
            ProductVariant(variant_id="aud_001-white", options={"Color": "White"}),
        ]
        p = ProductResult(
            **_base_product_kwargs(
                product_id="aud_001",
                name="Demo Wireless Headphones",
                merchant="Audio Hub",
                merchant_domain="audio-hub.myshopify.com",
                variants=variants,
                option_names=["Color"],
            )
        )
        dumped = p.model_dump(mode="json")
        assert dumped["option_names"] == ["Color"]
        assert len(dumped["variants"]) == 2
        restored = ProductResult.model_validate(dumped)
        assert restored.variants == p.variants
        assert restored.option_names == p.option_names


# ── CartItem.variant_id / selected_options ───────────────────────────────


class TestCartItemVariantFields:
    def test_defaults_are_none_and_empty(self):
        item = CartItem(
            product_id="ath_005", name="Athletic Wireless Earbuds", price=Decimal("89.99")
        )
        assert item.variant_id is None
        assert item.selected_options == {}

    def test_variant_item_carries_id_and_options(self):
        item = CartItem(
            product_id="ath_003",
            variant_id="ath_003-S-Black",
            name="Performance Running Shorts",
            price=Decimal("44.99"),
            merchant_domain="athletic-co.myshopify.com",
            selected_options={"Size": "S", "Color": "Black"},
        )
        assert item.variant_id == "ath_003-S-Black"
        assert item.selected_options == {"Size": "S", "Color": "Black"}

    def test_line_total_unaffected_by_variant_fields(self):
        item = CartItem(
            product_id="cof_002",
            variant_id="cof_002-16oz",
            name="Travel Coffee Tumbler",
            price=Decimal("28.00"),
            quantity=3,
            selected_options={"Size": "16oz"},
        )
        assert item.line_total == Decimal("84.00")

    def test_serialization_round_trip(self):
        item = CartItem(
            product_id="ath_003",
            variant_id="ath_003-S-Black",
            name="Performance Running Shorts",
            price=Decimal("44.99"),
            selected_options={"Size": "S", "Color": "Black"},
        )
        dumped = item.model_dump(mode="json")
        restored = CartItem.model_validate(dumped)
        assert restored == item

    def test_no_variant_cart_item_round_trip_unaffected(self):
        """Family-of-1 / single-SKU products must round-trip exactly as
        before — no regression for the common case."""
        item = CartItem(
            product_id="ath_005", name="Athletic Wireless Earbuds", price=Decimal("89.99")
        )
        dumped = item.model_dump(mode="json")
        assert dumped["variant_id"] is None
        assert dumped["selected_options"] == {}
        restored = CartItem.model_validate(dumped)
        assert restored == item
