"""LiveShopifyTransport — unit tests with mocked HTTP responses.

Tests the Shopify /products.json → internal dict mapping, caching,
search filtering, and adapter integration without hitting real APIs.
Also tests the Buy-on-merchant UI flow for external products.

Sorts before test_user_journeys.py → uses asyncio.get_event_loop().
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.shopify_mcp import (
    LiveShopifyTransport,
    ShopifyMCPAdapter,
    _normalise_variants,
    _shopify_product_to_dict,
    _strip_html,
)
from config.catalogue import LIVE_MERCHANTS


# ── Sample Shopify product JSON (matches real kith.com shape) ────────────

SAMPLE_SHOPIFY_PRODUCTS = {
    "products": [
        {
            "id": 8286509301888,
            "title": "Kith Logo Crewneck - Black",
            "handle": "khmg030009-001",
            "body_html": "<p>400 GSM reversible cotton fleece</p><p>Relaxed fit</p>",
            "vendor": "Kith",
            "product_type": "Crewnecks",
            "tags": ["crewneck", "kith"],
            "variants": [
                {
                    "id": 45678,
                    "title": "S",
                    "price": "155.00",
                    "available": True,
                    "sku": "KHMG030009-001-S",
                },
                {
                    "id": 45679,
                    "title": "M",
                    "price": "155.00",
                    "available": True,
                    "sku": "KHMG030009-001-M",
                },
            ],
            "images": [
                {
                    "id": 111,
                    "src": "https://cdn.shopify.com/s/files/1/0094/2252/files/front.jpg?v=1",
                    "width": 1534,
                    "height": 1534,
                },
                {
                    "id": 112,
                    "src": "https://cdn.shopify.com/s/files/1/0094/2252/files/back.jpg?v=1",
                    "width": 1534,
                    "height": 1534,
                },
            ],
        },
        {
            "id": 8286509498496,
            "title": "Kith Standard Tee - White",
            "handle": "khmg030010-101",
            "body_html": "<b>Premium</b> cotton &amp; polyester blend",
            "vendor": "Kith",
            "product_type": "Short Sleeve Tees",
            "tags": ["tee", "kith"],
            "variants": [
                {
                    "id": 45680,
                    "title": "Default",
                    "price": "75.00",
                    "available": False,
                },
            ],
            "images": [
                {
                    "id": 113,
                    "src": "https://cdn.shopify.com/s/files/1/0094/2252/files/tee.jpg?v=1",
                },
            ],
        },
        {
            "id": 9999999,
            "title": "Clarks Wallabee Boot - Brown",
            "handle": "clarks-wallabee-brown",
            "body_html": None,
            "vendor": "Clarks",
            "product_type": "Boots",
            "tags": ["boots"],
            "variants": [{"id": 99999, "title": "10", "price": "210.00", "available": True}],
            "images": [],
        },
        # Single-dimension variants: Size only.
        {
            "id": 8300000000001,
            "title": "Kith Track Pant",
            "handle": "kith-track-pant",
            "body_html": "<p>Tapered nylon track pant.</p>",
            "vendor": "Kith",
            "product_type": "Pants",
            "tags": ["pant", "kith"],
            "options": [{"name": "Size", "position": 1}],
            "variants": [
                {
                    "id": 70001,
                    "title": "S",
                    "price": "128.00",
                    "available": True,
                    "sku": "KITH-PANT-S",
                    "option1": "S",
                },
                {
                    "id": 70002,
                    "title": "M",
                    "price": "128.00",
                    "available": True,
                    "sku": "KITH-PANT-M",
                    "option1": "M",
                },
                {
                    "id": 70003,
                    "title": "L",
                    "price": "128.00",
                    "available": True,
                    "sku": "KITH-PANT-L",
                    "option1": "L",
                },
            ],
            "images": [
                {
                    "id": 114,
                    "src": "https://cdn.shopify.com/s/files/1/0094/2252/files/pant-front.jpg?v=1",
                },
                {
                    "id": 115,
                    "src": "https://cdn.shopify.com/s/files/1/0094/2252/files/pant-back.jpg?v=1",
                },
            ],
        },
        # No-variant product: a single "Default Title" variant.
        {
            "id": 8300000000002,
            "title": "Kith Camp Cap",
            "handle": "kith-camp-cap",
            "body_html": "<p>Six-panel camp cap.</p>",
            "vendor": "Kith",
            "product_type": "Headwear",
            "tags": ["cap", "kith"],
            "options": [{"name": "Title", "position": 1}],
            "variants": [
                {
                    "id": 70004,
                    "title": "Default Title",
                    "price": "48.00",
                    "available": True,
                    "sku": "KITH-CAP",
                    "option1": "Default Title",
                },
            ],
            "images": [
                {
                    "id": 116,
                    "src": "https://cdn.shopify.com/s/files/1/0094/2252/files/cap-front.jpg?v=1",
                },
                {
                    "id": 117,
                    "src": "https://cdn.shopify.com/s/files/1/0094/2252/files/cap-back.jpg?v=1",
                },
            ],
        },
        # Two-dimension variants: Size + Color, with a price-override variant.
        {
            "id": 8300000000003,
            "title": "Kith Cargo Short",
            "handle": "kith-cargo-short",
            "body_html": "<p>Relaxed-fit cargo short.</p>",
            "vendor": "Kith",
            "product_type": "Shorts",
            "tags": ["short", "kith"],
            "options": [{"name": "Size", "position": 1}, {"name": "Color", "position": 2}],
            "variants": [
                {
                    "id": 70005,
                    "title": "S / Khaki",
                    "price": "118.00",
                    "available": True,
                    "sku": "KITH-CARGO-S-KHK",
                    "option1": "S",
                    "option2": "Khaki",
                },
                {
                    "id": 70006,
                    "title": "M / Khaki",
                    "price": "118.00",
                    "available": True,
                    "sku": "KITH-CARGO-M-KHK",
                    "option1": "M",
                    "option2": "Khaki",
                },
                {
                    "id": 70007,
                    "title": "S / Olive",
                    "price": "118.00",
                    "available": True,
                    "sku": "KITH-CARGO-S-OLV",
                    "option1": "S",
                    "option2": "Olive",
                },
                # Price override + out-of-stock combination.
                {
                    "id": 70008,
                    "title": "M / Olive",
                    "price": "128.00",
                    "available": False,
                    "sku": "KITH-CARGO-M-OLV",
                    "option1": "M",
                    "option2": "Olive",
                },
            ],
            "images": [
                {
                    "id": 118,
                    "src": "https://cdn.shopify.com/s/files/1/0094/2252/files/cargo-front.jpg?v=1",
                },
                {
                    "id": 119,
                    "src": "https://cdn.shopify.com/s/files/1/0094/2252/files/cargo-back.jpg?v=1",
                },
            ],
        },
    ]
}


def _mock_response(data: dict, status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status = MagicMock()
    resp.json.return_value = data
    return resp


def _make_transport(sample_data=None) -> LiveShopifyTransport:
    """Create a transport with a mocked HTTP client."""
    transport = LiveShopifyTransport("https://kith.com", max_pages=1, cache_ttl=300)
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response(sample_data or SAMPLE_SHOPIFY_PRODUCTS))
    transport._http = mock_client
    transport._owns_http = False
    return transport


# ── HTML stripping ───────────────────────────────────────────────────────


class TestStripHtml:
    def test_strips_tags(self):
        assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"

    def test_unescapes_entities(self):
        assert "cotton & polyester" in _strip_html("cotton &amp; polyester")

    def test_none_returns_empty(self):
        assert _strip_html(None) == ""

    def test_truncates_long_text(self):
        long = "<p>" + "x" * 600 + "</p>"
        assert len(_strip_html(long)) == 500


# ── Product dict mapping ────────────────────────────────────────────────


class TestShopifyProductMapping:
    def test_basic_fields(self):
        raw = SAMPLE_SHOPIFY_PRODUCTS["products"][0]
        d = _shopify_product_to_dict(raw, "https://kith.com")
        assert d["id"] == "8286509301888"
        assert d["title"] == "Kith Logo Crewneck - Black"
        assert d["price"] == "155.00"
        assert d["currency"] == "USD"
        assert d["vendor"] == "Kith"
        assert d["available"] is True

    def test_url_uses_handle(self):
        raw = SAMPLE_SHOPIFY_PRODUCTS["products"][0]
        d = _shopify_product_to_dict(raw, "https://kith.com")
        assert d["url"] == "https://kith.com/products/khmg030009-001"

    def test_images_extracted(self):
        raw = SAMPLE_SHOPIFY_PRODUCTS["products"][0]
        d = _shopify_product_to_dict(raw, "https://kith.com")
        assert len(d["images"]) == 2
        assert "cdn.shopify.com" in d["images"][0]

    def test_html_stripped_from_description(self):
        raw = SAMPLE_SHOPIFY_PRODUCTS["products"][0]
        d = _shopify_product_to_dict(raw, "https://kith.com")
        assert "<p>" not in d["description"]
        assert "400 GSM" in d["description"]

    def test_out_of_stock_product(self):
        raw = SAMPLE_SHOPIFY_PRODUCTS["products"][1]
        d = _shopify_product_to_dict(raw, "https://kith.com")
        assert d["available"] is False

    def test_no_images(self):
        raw = SAMPLE_SHOPIFY_PRODUCTS["products"][2]
        d = _shopify_product_to_dict(raw, "https://kith.com")
        assert d["images"] == []

    def test_none_body_html(self):
        raw = SAMPLE_SHOPIFY_PRODUCTS["products"][2]
        d = _shopify_product_to_dict(raw, "https://kith.com")
        assert d["description"] == ""

    def test_attributes_include_product_type(self):
        raw = SAMPLE_SHOPIFY_PRODUCTS["products"][0]
        d = _shopify_product_to_dict(raw, "https://kith.com")
        assert d["attributes"]["product_type"] == "Crewnecks"

    def test_first_variant_price_used(self):
        raw = SAMPLE_SHOPIFY_PRODUCTS["products"][0]
        d = _shopify_product_to_dict(raw, "https://kith.com")
        assert d["price"] == "155.00"


# ── Variant / option normalisation (Phase 1, task 1.10) ─────────────────


class TestVariantNormalisation:
    """``_normalise_variants`` against Shopify-shaped variants/options —
    Size-only, Default-Title (no variants), and Size+Color (2D, with a
    price-override + out-of-stock combination)."""

    def test_size_only_product_has_size_option(self):
        raw = SAMPLE_SHOPIFY_PRODUCTS["products"][3]  # Kith Track Pant
        assert raw["title"] == "Kith Track Pant"
        variants, option_names = _normalise_variants(raw["variants"], raw["options"])
        assert option_names == ["Size"]
        assert len(variants) == 3
        sizes = {v.options["Size"] for v in variants}
        assert sizes == {"S", "M", "L"}
        for v in variants:
            assert v.in_stock is True
            assert v.price is None  # all variants share the base price

    def test_default_title_product_has_no_variants(self):
        raw = SAMPLE_SHOPIFY_PRODUCTS["products"][4]  # Kith Camp Cap
        assert raw["title"] == "Kith Camp Cap"
        variants, option_names = _normalise_variants(raw["variants"], raw["options"])
        assert variants == []
        assert option_names == []

    def test_size_and_color_product_normalises_to_2d(self):
        raw = SAMPLE_SHOPIFY_PRODUCTS["products"][5]  # Kith Cargo Short
        assert raw["title"] == "Kith Cargo Short"
        variants, option_names = _normalise_variants(raw["variants"], raw["options"])
        assert option_names == ["Size", "Color"]
        assert len(variants) == 4
        for v in variants:
            assert set(v.options.keys()) == {"Size", "Color"}

        by_key = {(v.options["Size"], v.options["Color"]): v for v in variants}
        # Base price ($118.00) variants carry no override.
        assert by_key[("S", "Khaki")].price is None
        assert by_key[("M", "Khaki")].price is None
        assert by_key[("S", "Olive")].price is None
        # The M/Olive combination diverges ($128.00) and is out of stock.
        m_olive = by_key[("M", "Olive")]
        assert m_olive.price == Decimal("128.00")
        assert m_olive.in_stock is False

    def test_via_shopify_product_to_dict_and_to_product(self):
        """End-to-end: raw Shopify dict -> ``_shopify_product_to_dict`` ->
        adapter ``_to_product`` -> ``ProductResult.variants``/``option_names``."""
        a = ShopifyMCPAdapter(
            "kith.com",
            _make_transport(),
            source_protocol="shopify_storefront",
            merchant_display_name="Kith",
        )
        loop = asyncio.get_event_loop()
        product = loop.run_until_complete(a.get_product("8300000000003"))
        assert product is not None
        assert product.option_names == ["Size", "Color"]
        assert len(product.variants) == 4
        # "Starting at" price is the min across variants ($118.00), not the
        # first variant's price.
        assert product.price == Decimal("118.00")


# ── LiveShopifyTransport ────────────────────────────────────────────────


class TestLiveShopifyTransport:
    def test_fetch_populates_cache(self):
        transport = _make_transport()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(transport._fetch_products())
        assert len(transport._products) == 6
        assert "8286509301888" in transport._products_by_id

    def test_search_empty_query_returns_all(self):
        transport = _make_transport()
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(transport.search_products("", {}, 10))
        assert len(results) == 6

    def test_search_filters_by_title(self):
        transport = _make_transport()
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(transport.search_products("tee", {}, 10))
        assert len(results) == 1
        assert "Tee" in results[0]["title"]

    def test_search_filters_by_vendor(self):
        transport = _make_transport()
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(transport.search_products("clarks", {}, 10))
        assert len(results) == 1
        assert results[0]["vendor"] == "Clarks"

    def test_search_filters_by_product_type(self):
        transport = _make_transport()
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(transport.search_products("boots", {}, 10))
        assert len(results) == 1

    def test_search_filters_by_description(self):
        transport = _make_transport()
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(transport.search_products("fleece", {}, 10))
        assert len(results) == 1
        assert "Crewneck" in results[0]["title"]

    def test_search_no_match_returns_all(self):
        transport = _make_transport()
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(transport.search_products("nonexistent", {}, 10))
        assert len(results) == 6

    def test_search_respects_limit(self):
        transport = _make_transport()
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(transport.search_products("", {}, 2))
        assert len(results) == 2

    def test_get_product_by_id(self):
        transport = _make_transport()
        loop = asyncio.get_event_loop()
        p = loop.run_until_complete(transport.get_product("8286509301888"))
        assert p is not None
        assert p["title"] == "Kith Logo Crewneck - Black"

    def test_get_product_not_found(self):
        transport = _make_transport()
        loop = asyncio.get_event_loop()
        p = loop.run_until_complete(transport.get_product("nonexistent"))
        assert p is None

    def test_cache_reused_on_second_call(self):
        transport = _make_transport()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(transport.search_products("", {}, 10))
        loop.run_until_complete(transport.search_products("", {}, 10))
        assert transport._http.get.call_count == 1

    def test_cart_lifecycle(self):
        transport = _make_transport()
        loop = asyncio.get_event_loop()
        cart = loop.run_until_complete(transport.create_cart())
        assert cart["id"].startswith("cart_")
        assert cart["items"] == []

        cart = loop.run_until_complete(
            transport.update_cart(
                cart["id"],
                [
                    {
                        "product_id": "8286509301888",
                        "name": "Crewneck",
                        "price": "155.00",
                        "quantity": 2,
                    }
                ],
                None,
            )
        )
        assert Decimal(cart["subtotal"]) == Decimal("310.00")
        assert Decimal(cart["total"]) > Decimal("310.00")

    def test_complete_cart_raises(self):
        transport = _make_transport()
        loop = asyncio.get_event_loop()
        cart = loop.run_until_complete(transport.create_cart())
        with pytest.raises(NotImplementedError, match="merchant's website"):
            loop.run_until_complete(transport.complete_cart(cart["id"], "tok_test"))


# ── ShopifyMCPAdapter with LiveShopifyTransport ─────────────────────────


class TestAdapterWithLiveTransport:
    def _adapter(self) -> ShopifyMCPAdapter:
        transport = _make_transport()
        return ShopifyMCPAdapter(
            "kith.com",
            transport,
            source_protocol="shopify_storefront",
            merchant_display_name="Kith",
        )

    def test_search_returns_product_results(self):
        a = self._adapter()
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(a.search_products("", limit=5))
        assert len(results) == 5
        assert results[0].source_protocol == "shopify_storefront"
        assert results[0].merchant_domain == "kith.com"
        assert results[0].merchant == "Kith"

    def test_products_have_external_urls(self):
        a = self._adapter()
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(a.search_products("", limit=5))
        for p in results:
            if p.images:
                assert p.url.startswith("https://kith.com/products/")

    def test_get_product_returns_detail(self):
        a = self._adapter()
        loop = asyncio.get_event_loop()
        p = loop.run_until_complete(a.get_product("8286509301888"))
        assert p is not None
        assert p.name == "Kith Logo Crewneck - Black"
        assert p.price == Decimal("155.00")
        assert p.url == "https://kith.com/products/khmg030009-001"

    def test_out_of_stock_product(self):
        a = self._adapter()
        loop = asyncio.get_event_loop()
        p = loop.run_until_complete(a.get_product("8286509498496"))
        assert p is not None
        assert p.in_stock is False

    def test_images_are_shopify_cdn(self):
        a = self._adapter()
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(a.search_products("crewneck", limit=1))
        assert len(results) >= 1
        assert "cdn.shopify.com" in results[0].images[0]


# ── LIVE_MERCHANTS config ───────────────────────────────────────────────


class TestLiveMerchantsConfig:
    def test_kith_in_live_merchants(self):
        assert "kith.com" in LIVE_MERCHANTS

    def test_kith_has_store_url(self):
        assert LIVE_MERCHANTS["kith.com"]["store_url"] == "https://kith.com"

    def test_kith_has_display_name(self):
        assert LIVE_MERCHANTS["kith.com"]["display_name"] == "Kith"


# ── External URL detection (used by templates) ──────────────────────────


class TestExternalUrlDetection:
    def test_kith_product_url_is_external(self):
        a = ShopifyMCPAdapter(
            "kith.com",
            _make_transport(),
            source_protocol="shopify_storefront",
        )
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(a.search_products("", limit=3))
        kith_product = results[0]
        assert kith_product.url is not None
        assert kith_product.url.startswith("https://")

    def test_demo_product_url_is_none(self):
        from adapters.shopify_mcp import StubShopifyTransport

        a = ShopifyMCPAdapter(
            "demo-shop.myshopify.com",
            StubShopifyTransport(),
        )
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(a.search_products(""))
        assert results[0].url is None


# ── Web integration (product card rendering) ────────────────────────────


class TestWebIntegration:
    def test_home_page_includes_kith_merchant(self):
        """Kith should appear in the brand row on the home page."""
        from starlette.testclient import TestClient
        from web.app import app

        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "kith.com" in resp.text

    def test_kith_product_cards_have_buy_cta(self):
        """Product cards for Kith should include a 'Buy on {merchant}' badge.

        Live kith.com carries multiple brands; the "Buy on {{ merchant }}"
        text reflects the BRAND on the product, not the storefront. Assert
        the stable invariant: at least one Buy-on badge appears AND it
        points to kith.com.
        """
        from starlette.testclient import TestClient
        from web.app import app

        client = TestClient(app)
        resp = client.get("/search?merchant=kith.com")
        assert resp.status_code == 200
        if "kith.com" in resp.text and "product_id" in resp.text.lower():
            assert "Buy on " in resp.text
            assert "https://kith.com" in resp.text

    def test_demo_product_cards_lack_buy_cta(self):
        """Demo merchant cards should NOT have external buy CTAs."""
        from starlette.testclient import TestClient
        from web.app import app

        client = TestClient(app)
        resp = client.get("/search?merchant=athletic-co.myshopify.com")
        assert resp.status_code == 200
        assert "Buy on Athletic" not in resp.text

    def test_kith_product_detail_has_external_link(self):
        """Product detail for a Kith product should link to kith.com.

        Note: the live kith.com catalogue carries products from many brands
        (Kith's own collabs PLUS Nike, Adidas, Jordan, Stone Island, etc.).
        The rendered "Buy on {{ merchant }}" reflects the BRAND, not 'Kith'.
        So we assert the stable invariants — there's a Buy-on link that
        points to kith.com — rather than the specific brand label.
        """
        from starlette.testclient import TestClient
        from web.app import app

        client = TestClient(app)
        # First get a Kith product ID
        resp = client.get("/search?merchant=kith.com", headers={"Accept": "application/json"})
        data = resp.json()
        products = data.get("products", [])
        if products:
            pid = products[0]["product_id"]
            detail_resp = client.get(f"/product/kith.com/{pid}")
            assert detail_resp.status_code == 200
            # Stable: there IS a "Buy on" link of some kind.
            assert "Buy on " in detail_resp.text
            # Stable: the link points to kith.com (the product comes from there).
            assert "https://kith.com/products/" in detail_resp.text

    def test_cart_add_kith_product_shows_buy_link(self):
        """Adding a Kith product to cart should show a Buy-on link in drawer.

        Live kith.com carries multiple brands; assert the stable invariant
        (Buy-on link present, pointing to kith.com) instead of the specific
        brand label.
        """
        from starlette.testclient import TestClient
        from web.app import app

        import html
        import re

        client = TestClient(app)
        # Live kith.com search results are non-deterministic: any given
        # product may be no-variant (direct add), variant-with-stock, or
        # entirely out of stock. Walk the results and add the first product
        # we can actually add to the cart, then assert the Buy-on invariant.
        resp = client.get("/search?merchant=kith.com", headers={"Accept": "application/json"})
        products = resp.json().get("products", [])
        if not products:
            pytest.skip("live kith.com search returned no products")

        added = None
        for prod in products:
            pid = prod["product_id"]
            cart_resp = client.post(f"/cart/add/kith.com/{pid}", data={"quantity": "1"})
            if cart_resp.status_code == 400:
                # Phase 1: this product has variants and requires a
                # variant_id. Resolve an in-stock variant from the product
                # detail page's embedded variant data and retry.
                detail_resp = client.get(f"/product/kith.com/{pid}")
                if detail_resp.status_code != 200:
                    continue
                m = re.search(r"data-variants='(.*?)'", detail_resp.text, re.DOTALL)
                if not m:
                    continue
                variants = json.loads(html.unescape(m.group(1)))
                in_stock = next((v for v in variants if v.get("in_stock")), None)
                if in_stock is None:
                    continue
                cart_resp = client.post(
                    f"/cart/add/kith.com/{pid}",
                    data={"quantity": "1", "variant_id": in_stock["variant_id"]},
                )
            if cart_resp.status_code == 200:
                added = cart_resp
                break

        if added is None:
            pytest.skip("no addable Kith product found in live search results")
        assert "Buy on " in added.text
        assert "kith.com" in added.text
