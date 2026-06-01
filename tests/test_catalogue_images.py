"""Unit tests: every product in the seed catalogue has valid image URLs.

Images are stored in config/catalogue.py and flow through the Shopify
adapter into ProductResult.images. These tests verify the catalog data
layer directly — no HTTP calls, no agent layer needed.
"""

from __future__ import annotations

import pytest

from config.catalogue import MERCHANTS


class TestCatalogueImages:
    """Every product in every merchant catalogue must have images."""

    @pytest.mark.parametrize("domain,products", list(MERCHANTS.items()))
    def test_all_products_have_images_field(self, domain, products):
        for p in products:
            assert "images" in p, f"{domain}/{p['id']} is missing the 'images' field"

    @pytest.mark.parametrize("domain,products", list(MERCHANTS.items()))
    def test_images_is_non_empty_list(self, domain, products):
        for p in products:
            imgs = p.get("images", [])
            assert isinstance(imgs, list) and len(imgs) >= 1, (
                f"{domain}/{p['id']} has empty or non-list images: {imgs!r}"
            )

    @pytest.mark.parametrize("domain,products", list(MERCHANTS.items()))
    def test_each_image_url_is_https_string(self, domain, products):
        for p in products:
            for url in p.get("images", []):
                assert isinstance(url, str) and url.startswith("https://"), (
                    f"{domain}/{p['id']} has invalid image URL: {url!r}"
                )

    def test_total_product_count_unchanged(self):
        """Sanity check: catalogue still has 24 products (7+7+10)."""
        total = sum(len(v) for v in MERCHANTS.values())
        assert total == 24, f"Expected 24 products, got {total}"

    def test_athletic_co_image_themes(self):
        """Spot-check: Athletic Co running shoes use Unsplash URLs."""
        from config.catalogue import ATHLETIC_CO

        shoe_ids = {"ath_001", "ath_006", "ath_007"}
        for p in ATHLETIC_CO:
            if p["id"] in shoe_ids:
                for url in p["images"]:
                    assert "unsplash.com" in url, (
                        f"{p['id']} image should be from Unsplash; got {url!r}"
                    )

    def test_shopify_adapter_maps_images(self):
        """The Shopify adapter must propagate images into ProductResult."""
        from adapters.shopify_mcp import ShopifyMCPAdapter, StubShopifyTransport
        from config.catalogue import COFFEE_BAR

        adapter = ShopifyMCPAdapter(
            "coffee-bar.myshopify.com",
            StubShopifyTransport(seed_products=COFFEE_BAR),
        )
        import asyncio

        results = asyncio.get_event_loop().run_until_complete(adapter.search_products("mug", {}))
        mug = next((r for r in results if "mug" in r.name.lower()), None)
        assert mug is not None, "Expected at least one mug in Coffee Bar catalogue"
        assert isinstance(mug.images, list) and len(mug.images) >= 1, (
            f"Mug product has no images: {mug}"
        )
        assert mug.images[0].startswith("https://"), (
            f"First image URL should be https://; got {mug.images[0]!r}"
        )
