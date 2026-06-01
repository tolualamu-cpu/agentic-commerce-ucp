"""Unit tests for _enrich_products_with_images in web/routers/chat.py.

The discovery agent (Claude Haiku) does not reliably include the images
array when serialising ProductResult to its JSON output. These tests verify
that the enrichment function correctly fills missing images from the
in-memory merchant adapters.

Covers all three merchants and various edge cases.
Sorts before test_user_journeys ('i' < 'u') — uses get_event_loop().
"""

from __future__ import annotations

import asyncio
import json
import pytest

from web.routers.chat import _enrich_products_with_images


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def multi_ctx(multi_merchant_ctx):
    """Alias for the multi_merchant_ctx fixture."""
    return multi_merchant_ctx


def _make_product_dict(product_id, merchant_domain, images=None):
    return {
        "product_id": product_id,
        "name": "Test Product",
        "price": "99.00",
        "currency": "USD",
        "merchant": "Test Merchant",
        "merchant_domain": merchant_domain,
        "in_stock": True,
        "images": images if images is not None else [],
        "attributes": {},
        "source_protocol": "stub",
        "confidence_score": 1.0,
    }


# ─── Athletic Co (shoes/apparel/earbuds) ─────────────────────────────────────


class TestEnrichAthleticCo:
    def test_shoe_missing_images_filled(self, multi_ctx):
        p = _make_product_dict("ath_001", "athletic-co.myshopify.com")
        result = asyncio.get_event_loop().run_until_complete(
            _enrich_products_with_images(multi_ctx, [p])
        )
        assert result[0]["images"], "ath_001 images should be filled in"
        assert result[0]["images"][0].startswith("https://")

    def test_trail_shoe_filled(self, multi_ctx):
        p = _make_product_dict("ath_002", "athletic-co.myshopify.com")
        result = asyncio.get_event_loop().run_until_complete(
            _enrich_products_with_images(multi_ctx, [p])
        )
        assert result[0]["images"]

    def test_apparel_shorts_filled(self, multi_ctx):
        p = _make_product_dict("ath_003", "athletic-co.myshopify.com")
        result = asyncio.get_event_loop().run_until_complete(
            _enrich_products_with_images(multi_ctx, [p])
        )
        assert result[0]["images"]

    def test_earbuds_filled(self, multi_ctx):
        p = _make_product_dict("ath_005", "athletic-co.myshopify.com")
        result = asyncio.get_event_loop().run_until_complete(
            _enrich_products_with_images(multi_ctx, [p])
        )
        assert result[0]["images"]

    def test_existing_images_preserved_for_shoe(self, multi_ctx):
        custom_url = "https://example.com/custom.jpg"
        p = _make_product_dict("ath_001", "athletic-co.myshopify.com", images=[custom_url])
        result = asyncio.get_event_loop().run_until_complete(
            _enrich_products_with_images(multi_ctx, [p])
        )
        assert result[0]["images"][0] == custom_url, "Custom image should not be replaced"


# ─── Audio Hub (headphones/earbuds/speaker) ──────────────────────────────────


class TestEnrichAudioHub:
    def test_headphones_missing_images_filled(self, multi_ctx):
        p = _make_product_dict("aud_001", "audio-hub.myshopify.com")
        result = asyncio.get_event_loop().run_until_complete(
            _enrich_products_with_images(multi_ctx, [p])
        )
        assert result[0]["images"]
        assert (
            "unsplash.com" in result[0]["images"][0]
        ), f"Expected Unsplash URL; got {result[0]['images'][0]!r}"

    def test_noise_cancelling_headphones_filled(self, multi_ctx):
        p = _make_product_dict("aud_002", "audio-hub.myshopify.com")
        result = asyncio.get_event_loop().run_until_complete(
            _enrich_products_with_images(multi_ctx, [p])
        )
        assert result[0]["images"]

    def test_compact_earbuds_filled(self, multi_ctx):
        """aud_004 had wrong images (sunglasses) — verify adapter returns correct ones."""
        p = _make_product_dict("aud_004", "audio-hub.myshopify.com")
        result = asyncio.get_event_loop().run_until_complete(
            _enrich_products_with_images(multi_ctx, [p])
        )
        assert result[0]["images"]
        # Should be AirPods-style earbuds photo, not sunglasses
        # (photo-1600490036547 = AirPods, not photo-1572635196237 = sunglasses)
        assert (
            "1572635196237" not in result[0]["images"][0]
        ), "Earbuds should not use the sunglasses photo ID"

    def test_bluetooth_speaker_filled(self, multi_ctx):
        p = _make_product_dict("aud_005", "audio-hub.myshopify.com")
        result = asyncio.get_event_loop().run_until_complete(
            _enrich_products_with_images(multi_ctx, [p])
        )
        assert result[0]["images"]


# ─── Coffee Bar (mugs/beans/brewing) ─────────────────────────────────────────


class TestEnrichCoffeeBar:
    def test_ceramic_mug_filled(self, multi_ctx):
        p = _make_product_dict("cof_001", "coffee-bar.myshopify.com")
        result = asyncio.get_event_loop().run_until_complete(
            _enrich_products_with_images(multi_ctx, [p])
        )
        assert result[0]["images"]

    def test_travel_tumbler_filled(self, multi_ctx):
        """cof_002 previously had a failing URL — verify adapter provides working one."""
        p = _make_product_dict("cof_002", "coffee-bar.myshopify.com")
        result = asyncio.get_event_loop().run_until_complete(
            _enrich_products_with_images(multi_ctx, [p])
        )
        assert result[0]["images"]
        # Should use the verified tumbler photo, not the old 404 URL
        assert (
            "1570087616523" not in result[0]["images"][0]
        ), "Tumbler should not use the previously-failing photo ID"

    def test_ethiopia_beans_filled(self, multi_ctx):
        p = _make_product_dict("cof_003", "coffee-bar.myshopify.com")
        result = asyncio.get_event_loop().run_until_complete(
            _enrich_products_with_images(multi_ctx, [p])
        )
        assert result[0]["images"]

    def test_pour_over_set_filled(self, multi_ctx):
        p = _make_product_dict("cof_004", "coffee-bar.myshopify.com")
        result = asyncio.get_event_loop().run_until_complete(
            _enrich_products_with_images(multi_ctx, [p])
        )
        assert result[0]["images"]


# ─── Edge cases ───────────────────────────────────────────────────────────────


class TestEnrichEdgeCases:
    def test_skips_unknown_merchant(self, multi_ctx):
        p = _make_product_dict("ath_001", "unknown-merchant.myshopify.com")
        result = asyncio.get_event_loop().run_until_complete(
            _enrich_products_with_images(multi_ctx, [p])
        )
        # Should return unchanged (empty images), no error
        assert isinstance(result[0]["images"], list)

    def test_skips_unknown_product_id(self, multi_ctx):
        p = _make_product_dict("nonexistent_999", "athletic-co.myshopify.com")
        result = asyncio.get_event_loop().run_until_complete(
            _enrich_products_with_images(multi_ctx, [p])
        )
        assert isinstance(result[0]["images"], list)

    def test_empty_list_returns_empty(self, multi_ctx):
        result = asyncio.get_event_loop().run_until_complete(
            _enrich_products_with_images(multi_ctx, [])
        )
        assert result == []

    def test_cross_merchant_mixed_list(self, multi_ctx):
        """A mix of products from different merchants all get enriched."""
        products = [
            _make_product_dict("ath_001", "athletic-co.myshopify.com"),
            _make_product_dict("aud_002", "audio-hub.myshopify.com"),
            _make_product_dict("cof_001", "coffee-bar.myshopify.com"),
        ]
        result = asyncio.get_event_loop().run_until_complete(
            _enrich_products_with_images(multi_ctx, products)
        )
        assert all(
            r["images"] for r in result
        ), "All products in a cross-merchant list should be enriched"

    def test_already_enriched_products_not_modified(self, multi_ctx):
        """Products with existing images must not be modified."""
        p = _make_product_dict(
            "ath_001",
            "athletic-co.myshopify.com",
            images=["https://example.com/shoe.jpg"],
        )
        result = asyncio.get_event_loop().run_until_complete(
            _enrich_products_with_images(multi_ctx, [p])
        )
        assert result[0]["images"] == ["https://example.com/shoe.jpg"]

    def test_returned_dicts_are_json_serialisable(self, multi_ctx):
        """Enriched products must be fully JSON-serialisable."""
        products = [
            _make_product_dict("ath_001", "athletic-co.myshopify.com"),
            _make_product_dict("aud_001", "audio-hub.myshopify.com"),
        ]
        result = asyncio.get_event_loop().run_until_complete(
            _enrich_products_with_images(multi_ctx, products)
        )
        for r in result:
            json.dumps(r, default=str)  # must not raise

    def test_large_batch_all_enriched(self, multi_ctx):
        """A batch of 5 products (mixed) are all enriched."""
        products = [
            _make_product_dict("ath_001", "athletic-co.myshopify.com"),
            _make_product_dict("ath_007", "athletic-co.myshopify.com"),
            _make_product_dict("aud_003", "audio-hub.myshopify.com"),
            _make_product_dict("cof_003", "coffee-bar.myshopify.com"),
            _make_product_dict("cof_009", "coffee-bar.myshopify.com"),
        ]
        result = asyncio.get_event_loop().run_until_complete(
            _enrich_products_with_images(multi_ctx, products)
        )
        assert len(result) == 5
        missing = [r["product_id"] for r in result if not r["images"]]
        assert not missing, f"Products missing images after enrichment: {missing}"
