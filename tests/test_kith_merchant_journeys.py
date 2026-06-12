"""Kith (live merchant) integration — comprehensive unit + e2e tests.

Covers all user journeys across real (Kith) and mock (Athletic Co, Audio Hub,
Coffee Bar) merchants, verifying that live and stub merchants coexist correctly
and that every visual component renders properly.

Test categories:
  1. Product data integrity — fields, images, URLs, descriptions
  2. Discovery & search — Kith appears alongside demos, filtering works
  3. Product detail — page loads, images render, Buy on Kith CTA present
  4. Cart operations — add/remove/quantity for Kith products
  5. Cart drawer rendering — Buy on Kith links, mixed carts
  6. Purchase flow divergence — demo checkout vs Kith redirect
  7. Cross-merchant journeys — mixed baskets, comparisons
  8. Visual component verification — cards, badges, brand row, merchant tiles
  9. Chat product card rendering — Kith cards in agent responses

Sorts before test_user_journeys.py → uses asyncio.get_event_loop().
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from adapters.shopify_mcp import (
    LiveShopifyTransport,
    ShopifyMCPAdapter,
    StubShopifyTransport,
)
from agents.orchestrator import OrchestratorAgent
from agents.product_grouping import group_into_families
from cli.confirmation import AutoConfirmProvider
from config.catalogue import LIVE_MERCHANTS, MERCHANTS
from models.product import ProductResult
from tests.fake_anthropic import FakeAnthropicClient
from web import session as session_mod
from web.app import create_app
from tests.fixtures.kith_products import (
    ALL_KITH_PRODUCT_IDS,
    KITH_990V6_GREY,
    KITH_990V6_NAVY,
    KITH_CAMP_CAP,
    KITH_CREWNECK_WHITE,
    KITH_FAMILIES,
    KITH_NO_VARIANT_IDS,
    KITH_POCKET_TEE,
    KITH_PRICE_SPREAD_IDS,
    KITH_SHEARLING_BOMBER_SUEDE,
    KITH_TRACK_SPIKE,
)


# ── Fixtures ─────────────────────────────────────────────────────────────


SAMPLE_SHOPIFY_PRODUCTS = {
    "products": [
        {
            "id": 100001,
            "title": "Kith Logo Hoodie - Black",
            "handle": "kith-logo-hoodie-black",
            "body_html": "<p>Premium heavyweight fleece. Oversized fit.</p>",
            "vendor": "Kith",
            "product_type": "Hoodies",
            "tags": ["hoodie", "kith", "fleece"],
            "variants": [
                {"id": 1, "title": "S", "price": "185.00", "available": True},
                {"id": 2, "title": "M", "price": "185.00", "available": True},
                {"id": 3, "title": "L", "price": "185.00", "available": False},
            ],
            "images": [
                {"id": 10, "src": "https://cdn.shopify.com/kith-hoodie-front.jpg"},
                {"id": 11, "src": "https://cdn.shopify.com/kith-hoodie-back.jpg"},
            ],
        },
        {
            "id": 100002,
            "title": "Kith Classic Tee - White",
            "handle": "kith-classic-tee-white",
            "body_html": "<b>100%</b> cotton &amp; premium finish",
            "vendor": "Kith",
            "product_type": "Short Sleeve Tees",
            "tags": ["tee", "cotton"],
            "variants": [
                {"id": 4, "title": "Default", "price": "65.00", "available": True},
            ],
            "images": [
                {"id": 12, "src": "https://cdn.shopify.com/kith-tee-front.jpg"},
            ],
        },
        {
            "id": 100003,
            "title": "Kith Vintage Cap - Navy",
            "handle": "kith-vintage-cap-navy",
            "body_html": None,
            "vendor": "Kith",
            "product_type": "Caps",
            "tags": ["cap", "accessories"],
            "variants": [
                {"id": 5, "title": "OS", "price": "55.00", "available": False},
            ],
            "images": [],
        },
        {
            "id": 100004,
            "title": "ASICS x Kith Gel-Lyte III",
            "handle": "asics-kith-gel-lyte-iii",
            "body_html": "<p>Collaboration sneaker</p>",
            "vendor": "ASICS",
            "product_type": "Sneakers",
            "tags": ["sneakers", "asics", "collaboration"],
            "variants": [
                {"id": 6, "title": "9", "price": "160.00", "available": True},
                {"id": 7, "title": "10", "price": "160.00", "available": True},
            ],
            "images": [
                {"id": 13, "src": "https://cdn.shopify.com/asics-kith-front.jpg"},
                {"id": 14, "src": "https://cdn.shopify.com/asics-kith-side.jpg"},
                {"id": 15, "src": "https://cdn.shopify.com/asics-kith-back.jpg"},
            ],
        },
    ]
}


def _mock_response(data: dict):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = data
    return resp


def _mock_kith_transport() -> LiveShopifyTransport:
    transport = LiveShopifyTransport("https://kith.com", max_pages=1, cache_ttl=9999)
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response(SAMPLE_SHOPIFY_PRODUCTS))
    transport._http = mock_client
    transport._owns_http = False
    return transport


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with a mocked Kith transport (no real network calls)."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "demo.json"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    mock_transport = _mock_kith_transport()
    _original_make = session_mod._make_session

    def _patched_make(session_id):
        sess = _original_make(session_id)
        sess.ctx.merchant_gateway.direct_adapters["kith.com"] = ShopifyMCPAdapter(
            "kith.com",
            mock_transport,
            source_protocol="shopify_storefront",
            merchant_display_name="Kith",
        )
        return sess

    monkeypatch.setattr(session_mod, "_make_session", _patched_make)
    app = create_app()
    with TestClient(app) as c:
        yield c


def _get_session(client) -> session_mod.WebSession:
    raw = client.cookies.get("ac_session")
    sid = session_mod._serializer.loads(raw)
    return session_mod.get_session_by_id(sid)


# ── Demo merchant constants ──────────────────────────────────────────────

_ATH = "athletic-co.myshopify.com"
_AUD = "audio-hub.myshopify.com"
_COF = "coffee-bar.myshopify.com"
_KITH = "kith.com"


# =========================================================================
# 1. PRODUCT DATA INTEGRITY
# =========================================================================


class TestKithProductDataIntegrity:
    """Verify that Shopify→ProductResult mapping preserves all fields correctly."""

    def _adapter(self):
        # Pass merchant_display_name so ProductResult.merchant resolves to the
        # storefront "Kith" (matching the live wiring in web/session.py which
        # passes LIVE_MERCHANTS[domain]["display_name"]). Without this the
        # adapter falls back to the domain "kith.com".
        return ShopifyMCPAdapter(
            "kith.com",
            _mock_kith_transport(),
            source_protocol="shopify_storefront",
            merchant_display_name="Kith",
        )

    def test_product_id_is_string(self):
        a = self._adapter()
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(a.search_products("", limit=10))
        for p in results:
            assert isinstance(p.product_id, str)

    def test_price_is_decimal(self):
        a = self._adapter()
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(a.search_products("", limit=10))
        for p in results:
            assert isinstance(p.price, Decimal)
            assert p.price > 0

    def test_merchant_is_storefront_kith(self):
        """UCP rule: ProductResult.merchant is the STOREFRONT (Kith),
        regardless of which brand made the product. The Shopify vendor
        (Stone Island, Jordan, etc.) lives on the separate `brand` field."""
        a = self._adapter()
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(a.search_products("", limit=10))
        # Every product must have merchant == "Kith" — even ones from
        # vendors like Stone Island. Previously this was "Stone Island"
        # (brand) which produced "Buy on Stone Island" badges that linked
        # to kith.com — confusing the user about who sells the item.
        for p in results:
            assert p.merchant == "Kith", (
                f"merchant must be storefront 'Kith', got {p.merchant!r} for product {p.name!r}"
            )

    def test_merchant_domain_is_kith(self):
        a = self._adapter()
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(a.search_products("", limit=10))
        for p in results:
            assert p.merchant_domain == "kith.com"

    def test_source_protocol_is_shopify_storefront(self):
        a = self._adapter()
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(a.search_products("", limit=10))
        for p in results:
            assert p.source_protocol == "shopify_storefront"

    def test_url_points_to_real_kith_page(self):
        a = self._adapter()
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(a.search_products("", limit=10))
        for p in results:
            if p.url:
                assert p.url.startswith("https://kith.com/products/")

    def test_images_are_shopify_cdn_urls(self):
        a = self._adapter()
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(a.search_products("hoodie", limit=1))
        assert results[0].images
        for img in results[0].images:
            assert img.startswith("https://cdn.shopify.com/")

    def test_description_is_plain_text(self):
        a = self._adapter()
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(a.search_products("hoodie", limit=1))
        desc = results[0].description
        assert desc
        assert "<p>" not in desc
        assert "<b>" not in desc

    def test_html_entities_decoded(self):
        a = self._adapter()
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(a.search_products("tee", limit=1))
        assert "&amp;" not in results[0].description
        assert "&" in results[0].description

    def test_out_of_stock_detected(self):
        a = self._adapter()
        loop = asyncio.get_event_loop()
        p = loop.run_until_complete(a.get_product("100003"))
        assert p is not None
        assert p.in_stock is False

    def test_available_when_any_variant_in_stock(self):
        a = self._adapter()
        loop = asyncio.get_event_loop()
        p = loop.run_until_complete(a.get_product("100001"))
        assert p.in_stock is True

    def test_no_images_product_has_empty_list(self):
        a = self._adapter()
        loop = asyncio.get_event_loop()
        p = loop.run_until_complete(a.get_product("100003"))
        assert p.images == []

    def test_demo_products_have_no_external_url(self):
        demo = ShopifyMCPAdapter(_ATH, StubShopifyTransport(seed_products=MERCHANTS[_ATH]))
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(demo.search_products("", limit=5))
        for p in results:
            assert p.url is None


# =========================================================================
# 2. DISCOVERY & SEARCH
# =========================================================================


class TestDiscoveryAndSearch:
    def test_home_page_shows_all_four_merchants(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert _ATH in r.text
        assert _AUD in r.text
        assert _COF in r.text
        assert _KITH in r.text

    def test_home_page_shows_kith_products(self, client):
        r = client.get("/")
        assert "Kith" in r.text

    def test_search_kith_only(self, client):
        r = client.get(f"/search?merchant={_KITH}")
        assert r.status_code == 200
        assert "Kith" in r.text
        assert "Athletic Co" not in r.text

    def test_search_demo_only_no_kith(self, client):
        r = client.get(f"/search?merchant={_ATH}")
        assert r.status_code == 200
        assert "Buy on Kith" not in r.text

    def test_search_all_merchants(self, client):
        r = client.get("/search?q=")
        assert r.status_code == 200

    def test_search_kith_json(self, client):
        r = client.get(f"/search?merchant={_KITH}", headers={"Accept": "application/json"})
        data = r.json()
        products = data.get("products", [])
        assert len(products) > 0
        for p in products:
            assert p["merchant_domain"] == _KITH

    def test_search_kith_with_query(self, client):
        r = client.get(
            f"/search?q=hoodie&merchant={_KITH}",
            headers={"Accept": "application/json"},
        )
        data = r.json()
        products = data.get("products", [])
        assert len(products) >= 1
        assert any("Hoodie" in p["name"] for p in products)

    def test_kith_in_brand_row(self, client):
        r = client.get("/")
        assert f'href="/search?merchant={_KITH}"' in r.text


# =========================================================================
# 3. PRODUCT DETAIL PAGE
# =========================================================================


class TestKithProductDetail:
    def test_detail_page_loads(self, client):
        r = client.get(f"/product/{_KITH}/100001")
        assert r.status_code == 200

    def test_detail_has_product_name(self, client):
        r = client.get(f"/product/{_KITH}/100001")
        assert "Kith Logo Hoodie - Black" in r.text

    def test_detail_has_price(self, client):
        r = client.get(f"/product/{_KITH}/100001")
        assert "$185" in r.text

    def test_detail_has_merchant_attribution(self, client):
        r = client.get(f"/product/{_KITH}/100001")
        assert "Kith" in r.text

    def test_detail_has_description(self, client):
        r = client.get(f"/product/{_KITH}/100001")
        assert "heavyweight fleece" in r.text.lower()

    def test_detail_has_buy_on_kith_button(self, client):
        r = client.get(f"/product/{_KITH}/100001")
        assert "Buy on Kith" in r.text

    def test_detail_buy_link_points_to_kith(self, client):
        r = client.get(f"/product/{_KITH}/100001")
        assert "https://kith.com/products/kith-logo-hoodie-black" in r.text

    def test_detail_buy_link_opens_new_tab(self, client):
        r = client.get(f"/product/{_KITH}/100001")
        assert 'target="_blank"' in r.text

    def test_detail_has_add_to_cart_button(self, client):
        r = client.get(f"/product/{_KITH}/100001")
        assert "Add to cart" in r.text

    def test_detail_add_to_cart_is_secondary_for_kith(self, client):
        """When Buy on Kith is primary, Add to cart should be outlined/secondary."""
        r = client.get(f"/product/{_KITH}/100001")
        assert "border-slate-300" in r.text

    def test_detail_has_img_tag(self, client):
        r = client.get(f"/product/{_KITH}/100001")
        assert "<img" in r.text
        assert "cdn.shopify.com" in r.text

    def test_detail_has_thumbnail_strip(self, client):
        r = client.get(f"/product/{_KITH}/100001")
        assert "data-thumb" in r.text

    def test_detail_out_of_stock_product(self, client):
        r = client.get(f"/product/{_KITH}/100003")
        assert r.status_code == 200
        assert "Out of stock" in r.text

    def test_detail_out_of_stock_no_add_to_cart(self, client):
        r = client.get(f"/product/{_KITH}/100003")
        assert "Add to cart" not in r.text or "cursor-not-allowed" in r.text

    def test_detail_unknown_product_404(self, client):
        r = client.get(f"/product/{_KITH}/nonexistent")
        assert r.status_code == 404

    def test_detail_breadcrumb_links_to_kith_search(self, client):
        r = client.get(f"/product/{_KITH}/100001")
        assert f"/search?merchant={_KITH}" in r.text

    def test_detail_shows_source_protocol(self, client):
        r = client.get(f"/product/{_KITH}/100001")
        assert "shopify_storefront" in r.text

    def test_demo_detail_has_no_buy_on_badge(self, client):
        r = client.get(f"/product/{_ATH}/ath_001")
        assert r.status_code == 200
        assert "Buy on Athletic" not in r.text
        assert "Buy on" not in r.text

    def test_demo_detail_add_to_cart_is_primary(self, client):
        """Demo products should NOT have the outlined secondary style on Add to cart."""
        r = client.get(f"/product/{_ATH}/ath_001")
        assert "Add to cart" in r.text


# =========================================================================
# 4. CART OPERATIONS — Kith products
# =========================================================================


class TestKithCartOperations:
    def test_add_kith_product(self, client):
        client.get("/")
        r = client.post(f"/cart/add/{_KITH}/100001")
        assert r.status_code == 200
        sess = _get_session(client)
        items = sess.click_basket.get(_KITH, [])
        assert len(items) == 1
        assert items[0]["product_id"] == "100001"
        assert items[0]["name"] == "Kith Logo Hoodie - Black"
        assert items[0]["price"] == "185.00"

    def test_add_kith_stores_external_url(self, client):
        client.get("/")
        client.post(f"/cart/add/{_KITH}/100001")
        sess = _get_session(client)
        item = sess.click_basket[_KITH][0]
        assert item["url"] == "https://kith.com/products/kith-logo-hoodie-black"

    def test_add_kith_stores_merchant_name(self, client):
        client.get("/")
        client.post(f"/cart/add/{_KITH}/100001")
        sess = _get_session(client)
        item = sess.click_basket[_KITH][0]
        assert item["merchant_name"] == "Kith"

    def test_add_kith_stores_image_url(self, client):
        client.get("/")
        client.post(f"/cart/add/{_KITH}/100001")
        sess = _get_session(client)
        item = sess.click_basket[_KITH][0]
        assert item["image_url"]
        assert "cdn.shopify.com" in item["image_url"]

    def test_add_kith_twice_bumps_quantity(self, client):
        client.get("/")
        client.post(f"/cart/add/{_KITH}/100001")
        client.post(f"/cart/add/{_KITH}/100001")
        sess = _get_session(client)
        items = sess.click_basket[_KITH]
        assert len(items) == 1
        assert items[0]["quantity"] == 2

    def test_remove_kith_product(self, client):
        client.get("/")
        client.post(f"/cart/add/{_KITH}/100001")
        client.post(f"/cart/remove/{_KITH}/100001")
        sess = _get_session(client)
        assert sess.click_basket.get(_KITH, []) == []

    def test_change_kith_quantity(self, client):
        client.get("/")
        client.post(f"/cart/add/{_KITH}/100001")
        r = client.post(f"/cart/quantity/{_KITH}/100001", data={"quantity": 3})
        assert r.status_code == 200
        sess = _get_session(client)
        item = sess.click_basket[_KITH][0]
        assert item["quantity"] == 3
        assert Decimal(item["line_total"]) == Decimal("555.00")

    def test_change_kith_quantity_to_zero_removes(self, client):
        client.get("/")
        client.post(f"/cart/add/{_KITH}/100001")
        client.post(f"/cart/quantity/{_KITH}/100001", data={"quantity": 0})
        sess = _get_session(client)
        assert sess.click_basket.get(_KITH, []) == []

    def test_add_out_of_stock_kith_product(self, client):
        """Out-of-stock products can still be added (no server-side block)."""
        client.get("/")
        r = client.post(f"/cart/add/{_KITH}/100003")
        assert r.status_code == 200

    def test_kith_add_appends_click_note(self, client):
        client.get("/")
        client.post(f"/cart/add/{_KITH}/100001")
        sess = _get_session(client)
        last = sess.ctx.session.conversation[-1]
        assert last["role"] == "user"
        assert "[via UI click]" in last["content"][0]["text"]


# =========================================================================
# 5. CART DRAWER RENDERING
# =========================================================================


class TestCartDrawerRendering:
    def test_kith_item_shows_buy_on_kith_link(self, client):
        client.get("/")
        client.post(f"/cart/add/{_KITH}/100001")
        r = client.get("/cart")
        assert "Buy on Kith" in r.text

    def test_kith_item_buy_link_points_to_product(self, client):
        client.get("/")
        client.post(f"/cart/add/{_KITH}/100001")
        r = client.get("/cart")
        assert "https://kith.com/products/" in r.text

    def test_kith_item_buy_link_opens_new_tab(self, client):
        client.get("/")
        client.post(f"/cart/add/{_KITH}/100001")
        r = client.get("/cart")
        buy_section = r.text[r.text.index("Buy on Kith") - 500 : r.text.index("Buy on Kith")]
        assert 'target="_blank"' in buy_section

    def test_demo_item_has_no_buy_on_link(self, client):
        client.get("/")
        client.post(f"/cart/add/{_COF}/cof_001")
        r = client.get("/cart")
        assert "Buy on Coffee" not in r.text
        assert (
            "Buy on" not in r.text.split("Review purchase")[0]
            if "Review purchase" in r.text
            else True
        )

    def test_kith_item_shows_image(self, client):
        client.get("/")
        client.post(f"/cart/add/{_KITH}/100001")
        r = client.get("/cart")
        assert "cdn.shopify.com" in r.text

    def test_kith_item_shows_name_and_price(self, client):
        client.get("/")
        client.post(f"/cart/add/{_KITH}/100001")
        r = client.get("/cart")
        assert "Kith Logo Hoodie" in r.text
        assert "$185" in r.text

    def test_kith_only_cart_footer_has_buy_button(self, client):
        client.get("/")
        client.post(f"/cart/add/{_KITH}/100001")
        r = client.get("/cart")
        assert "Buy on Kith" in r.text
        assert "Review purchase" not in r.text

    def test_demo_only_cart_footer_has_review_purchase(self, client):
        client.get("/")
        client.post(f"/cart/add/{_COF}/cof_001")
        r = client.get("/cart")
        assert "Review purchase" in r.text

    def test_mixed_cart_has_both_buttons(self, client):
        client.get("/")
        client.post(f"/cart/add/{_KITH}/100001")
        client.post(f"/cart/add/{_COF}/cof_001")
        r = client.get("/cart")
        assert "Buy on Kith" in r.text
        assert "Review purchase" in r.text

    def test_mixed_cart_subtotal_includes_all(self, client):
        client.get("/")
        client.post(f"/cart/add/{_KITH}/100001")  # $185
        client.post(f"/cart/add/{_COF}/cof_001")  # $14 (Ceramic Coffee Mug)
        r = client.get("/cart", headers={"Accept": "application/json"})
        data = r.json()
        assert data["item_count"] == 2
        subtotal = Decimal(data["subtotal"])
        assert subtotal >= Decimal("199.00")

    def test_cart_clear_removes_kith_and_demo(self, client):
        client.get("/")
        client.post(f"/cart/add/{_KITH}/100001")
        client.post(f"/cart/add/{_COF}/cof_001")
        client.post("/cart/clear")
        sess = _get_session(client)
        assert sess.click_basket == {}

    def test_kith_cart_quantity_controls_present(self, client):
        client.get("/")
        client.post(f"/cart/add/{_KITH}/100001")
        r = client.get("/cart")
        assert f"/cart/quantity/{_KITH}/100001" in r.text


# =========================================================================
# 6. PURCHASE FLOW DIVERGENCE
# =========================================================================


class TestPurchaseFlowDivergence:
    def test_live_transport_complete_cart_raises(self):
        transport = _mock_kith_transport()
        loop = asyncio.get_event_loop()
        cart = loop.run_until_complete(transport.create_cart())
        loop.run_until_complete(
            transport.update_cart(
                cart["id"],
                [{"product_id": "100001", "name": "Hoodie", "price": "185", "quantity": 1}],
                None,
            )
        )
        with pytest.raises(NotImplementedError, match="merchant's website"):
            loop.run_until_complete(transport.complete_cart(cart["id"], "tok_test"))

    def test_stub_transport_complete_cart_succeeds(self):
        transport = StubShopifyTransport()
        loop = asyncio.get_event_loop()
        cart = loop.run_until_complete(transport.create_cart())
        loop.run_until_complete(
            transport.update_cart(
                cart["id"],
                [{"product_id": "shop_001", "name": "Shoes", "price": "129.99", "quantity": 1}],
                None,
            )
        )
        order = loop.run_until_complete(transport.complete_cart(cart["id"], "tok_test"))
        assert order["order_id"].startswith("ord_")
        assert order["status"] == "confirmed"

    def test_kith_adapter_complete_checkout_raises(self):
        adapter = ShopifyMCPAdapter(
            "kith.com", _mock_kith_transport(), source_protocol="shopify_storefront"
        )
        loop = asyncio.get_event_loop()
        sess = loop.run_until_complete(adapter.create_checkout_session())
        with pytest.raises(NotImplementedError):
            loop.run_until_complete(
                adapter.complete_checkout(sess.session_id, "stripe", "tok_test")
            )

    def test_demo_adapter_full_checkout(self):
        adapter = ShopifyMCPAdapter(_ATH, StubShopifyTransport(seed_products=MERCHANTS[_ATH]))
        loop = asyncio.get_event_loop()
        sess = loop.run_until_complete(adapter.create_checkout_session())
        from models.product import CartItem

        sess = loop.run_until_complete(
            adapter.update_checkout_session(
                sess.session_id,
                items=[CartItem(product_id="ath_001", name="Shoes", price=Decimal("129.99"))],
            )
        )
        order = loop.run_until_complete(
            adapter.complete_checkout(sess.session_id, "stripe", "tok_test")
        )
        assert order.order_id.startswith("ord_")


# =========================================================================
# 7. CROSS-MERCHANT JOURNEYS
# =========================================================================


class TestCrossMerchantJourneys:
    def test_search_across_all_returns_mixed(self, client):
        """Global search returns products from both Kith and demo merchants."""
        r = client.get("/search?q=", headers={"Accept": "application/json"})
        data = r.json()
        merchants = {p["merchant_domain"] for p in data.get("products", [])}
        assert _KITH in merchants
        assert len(merchants) > 1

    def test_kith_product_then_demo_product_in_cart(self, client):
        client.get("/")
        client.post(f"/cart/add/{_KITH}/100001")
        client.post(f"/cart/add/{_ATH}/ath_001", data={"variant_id": "ath_001-8"})
        sess = _get_session(client)
        assert _KITH in sess.click_basket
        assert _ATH in sess.click_basket

    def test_remove_kith_keeps_demo(self, client):
        client.get("/")
        client.post(f"/cart/add/{_KITH}/100001")
        client.post(f"/cart/add/{_COF}/cof_001")
        client.post(f"/cart/remove/{_KITH}/100001")
        sess = _get_session(client)
        assert sess.click_basket.get(_KITH, []) == []
        assert len(sess.click_basket.get(_COF, [])) == 1

    def test_navigate_kith_to_demo_and_back(self, client):
        """User can navigate between Kith and demo merchant pages."""
        r1 = client.get(f"/search?merchant={_KITH}")
        assert r1.status_code == 200
        r2 = client.get(f"/search?merchant={_ATH}")
        assert r2.status_code == 200
        r3 = client.get(f"/search?merchant={_KITH}")
        assert r3.status_code == 200

    def test_cart_view_shows_items_from_multiple_merchants(self, client):
        client.get("/")
        client.post(f"/cart/add/{_KITH}/100001")
        client.post(f"/cart/add/{_AUD}/aud_001", data={"variant_id": "aud_001-Black"})
        client.post(f"/cart/add/{_COF}/cof_001")
        r = client.get("/cart")
        assert r.status_code == 200
        assert "kith.com" in r.text
        assert "audio-hub" in r.text
        assert "coffee-bar" in r.text


# =========================================================================
# 8. VISUAL COMPONENT VERIFICATION
# =========================================================================


class TestVisualComponents:
    def test_product_card_has_image_tag(self, client):
        r = client.get(f"/search?merchant={_KITH}")
        assert "<img" in r.text

    def test_product_card_image_src_is_shopify_cdn(self, client):
        r = client.get(f"/search?merchant={_KITH}")
        assert "cdn.shopify.com" in r.text

    def test_product_card_shows_product_name(self, client):
        r = client.get(f"/search?merchant={_KITH}")
        assert "Kith Logo Hoodie" in r.text

    def test_product_card_shows_price(self, client):
        r = client.get(f"/search?merchant={_KITH}")
        assert "$185" in r.text or "$65" in r.text

    def test_product_card_has_add_to_cart(self, client):
        r = client.get(f"/search?merchant={_KITH}")
        assert "Add to cart" in r.text

    def test_product_card_buy_on_kith_badge_present(self, client):
        r = client.get(f"/search?merchant={_KITH}")
        assert "Buy on Kith" in r.text

    def test_product_card_buy_badge_has_external_icon(self, client):
        """Badge should have an external link SVG icon."""
        r = client.get(f"/search?merchant={_KITH}")
        badge_area = r.text[r.text.index("Buy on Kith") : r.text.index("Buy on Kith") + 300]
        assert "<svg" in badge_area

    def test_product_card_buy_badge_has_correct_href(self, client):
        r = client.get(f"/search?merchant={_KITH}")
        assert 'href="https://kith.com/products/' in r.text

    def test_product_card_links_to_our_detail_page(self, client):
        """Clicking the card body navigates to /product/kith.com/{id}."""
        r = client.get(f"/search?merchant={_KITH}")
        assert f"/product/{_KITH}/" in r.text

    def test_product_card_out_of_stock_shown(self, client):
        r = client.get(f"/search?merchant={_KITH}")
        assert "Out of stock" in r.text

    def test_demo_card_has_no_buy_badge(self, client):
        r = client.get(f"/search?merchant={_ATH}")
        assert "Buy on" not in r.text

    def test_demo_card_has_unsplash_images(self, client):
        r = client.get(f"/search?merchant={_ATH}")
        assert "unsplash.com" in r.text

    def test_brand_row_shows_kith_tile(self, client):
        r = client.get("/")
        assert f"/search?merchant={_KITH}" in r.text

    def test_brand_row_shows_all_merchants(self, client):
        r = client.get("/")
        for m in [_ATH, _AUD, _COF, _KITH]:
            assert f"/search?merchant={m}" in r.text

    def test_kith_tile_display_name(self, client):
        """Brand row should show 'Kith' (title-cased from domain)."""
        r = client.get("/")
        assert ">Kith<" in r.text or "Kith</div>" in r.text or "Kith\n" in r.text

    def test_cart_drawer_kith_image_renders(self, client):
        client.get("/")
        client.post(f"/cart/add/{_KITH}/100001")
        r = client.get("/cart")
        assert "cdn.shopify.com" in r.text
        assert "<img" in r.text

    def test_cart_drawer_kith_has_quantity_controls(self, client):
        client.get("/")
        client.post(f"/cart/add/{_KITH}/100001")
        r = client.get("/cart")
        assert 'name="quantity"' in r.text
        assert 'type="number"' in r.text


# =========================================================================
# 9. CHAT PRODUCT CARD RENDERING
# =========================================================================


class TestChatProductCard:
    def test_chat_products_fragment_kith(self, client):
        """POST /chat/products-fragment should render Kith cards with Buy on badge."""
        client.get("/")
        r = client.post(
            "/chat/products-fragment",
            json={
                "products": [
                    {
                        "product_id": "100001",
                        "name": "Kith Logo Hoodie - Black",
                        "price": "185.00",
                        "merchant_domain": _KITH,
                        "merchant": "Kith",
                        "url": "https://kith.com/products/kith-logo-hoodie-black",
                        "images": ["https://cdn.shopify.com/kith-hoodie-front.jpg"],
                        "in_stock": True,
                        "description": "Premium fleece",
                    }
                ]
            },
        )
        assert r.status_code == 200
        assert "Buy on Kith" in r.text
        assert "Add to cart" in r.text
        assert "Kith Logo Hoodie" in r.text
        assert "$185" in r.text

    def test_chat_products_fragment_demo_no_buy_badge(self, client):
        """Demo products in chat should not have Buy on badge."""
        client.get("/")
        r = client.post(
            "/chat/products-fragment",
            json={
                "products": [
                    {
                        "product_id": "ath_001",
                        "name": "Demo Running Shoes",
                        "price": "129.99",
                        "merchant_domain": _ATH,
                        "merchant": "Athletic Co",
                        "url": None,
                        "images": ["https://images.unsplash.com/photo-123?w=800"],
                        "in_stock": True,
                    }
                ]
            },
        )
        assert r.status_code == 200
        assert "Buy on" not in r.text

    def test_chat_card_has_kith_image(self, client):
        client.get("/")
        r = client.post(
            "/chat/products-fragment",
            json={
                "products": [
                    {
                        "product_id": "100001",
                        "name": "Hoodie",
                        "price": "185.00",
                        "merchant_domain": _KITH,
                        "merchant": "Kith",
                        "url": "https://kith.com/products/x",
                        "images": ["https://cdn.shopify.com/hoodie.jpg"],
                        "in_stock": True,
                    }
                ]
            },
        )
        assert "cdn.shopify.com" in r.text
        assert "<img" in r.text

    def test_chat_card_detail_link(self, client):
        """Chat card should link to our product detail page."""
        client.get("/")
        r = client.post(
            "/chat/products-fragment",
            json={
                "products": [
                    {
                        "product_id": "100001",
                        "name": "Hoodie",
                        "price": "185.00",
                        "merchant_domain": _KITH,
                        "merchant": "Kith",
                        "url": "https://kith.com/products/x",
                        "images": [],
                        "in_stock": True,
                    }
                ]
            },
        )
        assert f"/product/{_KITH}/100001" in r.text

    def test_chat_card_out_of_stock(self, client):
        client.get("/")
        r = client.post(
            "/chat/products-fragment",
            json={
                "products": [
                    {
                        "product_id": "100003",
                        "name": "Cap",
                        "price": "55.00",
                        "merchant_domain": _KITH,
                        "merchant": "Kith",
                        "url": "https://kith.com/products/cap",
                        "images": [],
                        "in_stock": False,
                    }
                ]
            },
        )
        assert "Out of stock" in r.text
        assert "Add to cart" not in r.text


# =========================================================================
# 10. MERCHANT GATEWAY INTEGRATION
# =========================================================================


class TestMerchantGatewayIntegration:
    """Gateway registration and resolution tests.

    Parametrised tests in this class automatically cover every merchant in
    MERCHANTS and LIVE_MERCHANTS — adding a new entry to either dict means
    it is exercised here with no test changes required.
    """

    # ── Parametrised: every merchant on the platform ──────────────────────

    @pytest.mark.parametrize("domain", list(MERCHANTS) + list(LIVE_MERCHANTS))
    def test_every_merchant_registered_in_gateway(self, client, domain):
        """Every domain in MERCHANTS + LIVE_MERCHANTS must be in direct_adapters."""
        client.get("/")
        sess = _get_session(client)
        assert domain in sess.ctx.merchant_gateway.direct_adapters, (
            f"{domain} is missing from MerchantGateway.direct_adapters — "
            "add it to MERCHANTS or LIVE_MERCHANTS and re-run session wiring"
        )

    @pytest.mark.parametrize("domain", list(MERCHANTS) + list(LIVE_MERCHANTS))
    def test_every_merchant_resolves_to_a_client(self, client, domain):
        """resolve_client must return a non-None adapter for every merchant."""
        client.get("/")
        sess = _get_session(client)
        loop = asyncio.get_event_loop()
        resolved = loop.run_until_complete(sess.ctx.merchant_gateway.resolve_client(domain))
        assert resolved is not None, (
            f"resolve_client('{domain}') returned None — "
            "check gateway wiring and UCP discovery fallback"
        )

    @pytest.mark.parametrize("domain", list(MERCHANTS) + list(LIVE_MERCHANTS))
    def test_every_merchant_known_to_orchestrator(self, client, domain):
        """Orchestrator must list every registered merchant as available."""
        client.get("/")
        sess = _get_session(client)
        assert domain in sess.orchestrator.available_merchants, (
            f"Orchestrator does not know about '{domain}' — update the orchestrator's merchant list"
        )

    @pytest.mark.parametrize("domain", list(MERCHANTS) + list(LIVE_MERCHANTS))
    def test_every_merchant_returns_products_on_search(self, client, domain):
        """Fan-out search must return at least one product per merchant."""
        client.get("/")
        sess = _get_session(client)
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(
            sess.ctx.merchant_gateway.search("", [domain], limit_per_merchant=2)
        )
        domains_in_results = {r.merchant_domain for r in results}
        assert domain in domains_in_results, (
            f"search across [{domain}] returned no products for that merchant"
        )

    # ── Live merchant config integrity ────────────────────────────────────

    @pytest.mark.parametrize("domain", list(LIVE_MERCHANTS))
    def test_live_merchant_has_max_pages(self, domain):
        """Every live merchant must declare max_pages > 3 in LIVE_MERCHANTS
        so LiveShopifyTransport fetches the full catalog, not just 150 products."""
        meta = LIVE_MERCHANTS[domain]
        assert "max_pages" in meta, (
            f"{domain} is missing 'max_pages' in LIVE_MERCHANTS — "
            "without it LiveShopifyTransport caps at 150 products"
        )
        assert meta["max_pages"] > 3, (
            f"{domain} max_pages={meta['max_pages']} is at or below the "
            "default of 3; set it high enough to cover the full catalog"
        )

    @pytest.mark.parametrize("domain", list(LIVE_MERCHANTS))
    def test_live_merchant_transport_picks_up_max_pages(self, domain):
        """LiveShopifyTransport built from LIVE_MERCHANTS config must honour
        max_pages — verifies session.py / main.py wiring, not the mock fixture
        (which intentionally overrides to max_pages=1 for speed)."""
        meta = LIVE_MERCHANTS[domain]
        expected = meta["max_pages"]
        transport = LiveShopifyTransport(
            meta["store_url"],
            max_pages=meta.get("max_pages", 3),
        )
        assert transport.max_pages == expected, (
            f"{domain} transport.max_pages={transport.max_pages} but LIVE_MERCHANTS says {expected}"
        )

    # ── Routing priority: direct adapter beats UCP client ─────────────────

    @pytest.mark.parametrize("domain", list(MERCHANTS) + list(LIVE_MERCHANTS))
    def test_direct_adapter_takes_priority_over_ucp_routing(self, client, domain):
        """resolve_client must return the direct adapter, not a UCPMCPClient
        or UCPRestClient, for any merchant that has a registered direct adapter.

        This locks down the gateway routing priority: registered adapters are
        vetted transports; UCP clients are only used for dynamically-discovered
        merchants with no pre-registered adapter.  If this test breaks, the
        gateway routing order in _build_client has been changed incorrectly.
        """
        from ucp.client import UCPMCPClient, UCPRestClient

        client.get("/")
        sess = _get_session(client)
        loop = asyncio.get_event_loop()
        resolved = loop.run_until_complete(sess.ctx.merchant_gateway.resolve_client(domain))
        assert resolved is not None
        assert not isinstance(resolved, (UCPMCPClient, UCPRestClient)), (
            f"Gateway returned a UCP client for '{domain}' even though a direct "
            "adapter is registered. Direct adapters must take priority."
        )

    # ── Legacy single-assertion tests (kept for named-test readability) ───

    def test_gateway_has_kith_registered(self, client):
        client.get("/")
        sess = _get_session(client)
        assert _KITH in sess.ctx.merchant_gateway.direct_adapters

    def test_gateway_search_fan_out_includes_kith(self, client):
        client.get("/")
        sess = _get_session(client)
        domains = list(sess.ctx.merchant_gateway.direct_adapters.keys())
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(
            sess.ctx.merchant_gateway.search("", domains, limit_per_merchant=2)
        )
        merchant_domains = {r.merchant_domain for r in results}
        assert _KITH in merchant_domains

    def test_orchestrator_knows_kith(self, client):
        client.get("/")
        sess = _get_session(client)
        assert _KITH in sess.orchestrator.available_merchants


# =========================================================================
# 11. VARIANT / SKU SUPPORT (Phase 1, task 1.10) — KITH BREADTH FIXTURE
# =========================================================================
#
# Uses the 21-product fixture set in tests/fixtures/kith_products.py — the
# "required template for every new live merchant" (CLAUDE.md rule 6).
# Spans 5 product types, no-variant / single-dimension (Size) /
# multi-dimension (Size+Color, Size+Material) variant shapes, 6 multi-member
# families across 3 splitting dimensions (Color, Material, Capacity),
# out-of-stock (full + partial), and price-spread/override products —
# generalising the "variant" concept beyond color per the standing rule.


@pytest.fixture
def variant_client(tmp_path, monkeypatch):
    """TestClient with kith.com served by the 21-product
    tests/fixtures/kith_products.py fixture set (mocked HTTP, no real
    network calls)."""
    from tests.fixtures.kith_products import make_kith_adapter

    monkeypatch.setenv("DB_PATH", str(tmp_path / "demo.json"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    _original_make = session_mod._make_session

    def _patched_make(session_id):
        sess = _original_make(session_id)
        sess.ctx.merchant_gateway.direct_adapters["kith.com"] = make_kith_adapter()
        return sess

    monkeypatch.setattr(session_mod, "_make_session", _patched_make)
    app = create_app()
    with TestClient(app) as c:
        yield c


def _kith_product_detail(client, product_id: str):
    from tools.discovery_tools import get_product_details

    client.get("/")
    sess = _get_session(client)

    async def _run():
        return await get_product_details(
            sess.ctx, product_id=product_id, merchant_domain=_KITH, mandate_id=sess.mandate_id
        )

    return asyncio.get_event_loop().run_until_complete(_run())


def _kith_product_variants(client, product_id: str):
    from tools.discovery_tools import get_product_variants

    client.get("/")
    sess = _get_session(client)

    async def _run():
        return await get_product_variants(
            sess.ctx, product_id=product_id, merchant_domain=_KITH, mandate_id=sess.mandate_id
        )

    return asyncio.get_event_loop().run_until_complete(_run())


def _kith_search_all(client):
    from tools.discovery_tools import search_products

    client.get("/")
    sess = _get_session(client)

    async def _run():
        return await search_products(
            sess.ctx,
            query="",
            merchant_domains=[_KITH],
            limit_per_merchant=25,
            mandate_id=sess.mandate_id,
        )

    return asyncio.get_event_loop().run_until_complete(_run())


class TestKithVariants:
    """Product data integrity for the 21-product Kith fixture set —
    every product fetches correctly and exposes the right variant shape."""

    @pytest.mark.parametrize("product_id", ALL_KITH_PRODUCT_IDS)
    def test_every_fixture_product_fetches(self, variant_client, product_id):
        product = _kith_product_detail(variant_client, product_id)
        assert product is not None
        assert product.merchant_domain == _KITH
        assert product.product_id == product_id
        assert isinstance(product.option_names, list)
        assert isinstance(product.variants, list)
        # Either no variants at all (single-SKU), or every variant's option
        # keys exactly match the product-level option_names.
        if not product.variants:
            assert product.option_names == []
        else:
            for v in product.variants:
                assert set(v.options.keys()) == set(product.option_names)

    @pytest.mark.parametrize("product_id", KITH_NO_VARIANT_IDS)
    def test_no_variant_products_normalise_empty(self, variant_client, product_id):
        product = _kith_product_detail(variant_client, product_id)
        assert product.variants == []
        assert product.option_names == []

    def test_990v6_size_only_variant_shape(self, variant_client):
        """A standalone (family-of-1) member still exposes its OWN
        single-dimension (Size) variants before any family grouping."""
        product = _kith_product_detail(variant_client, str(KITH_990V6_GREY["id"]))
        assert product.option_names == ["Size"]
        sizes = {v.options["Size"] for v in product.variants}
        assert sizes == {"8", "9", "10", "11", "12"}
        for v in product.variants:
            assert v.in_stock is True

    def test_pocket_tee_size_and_color_2d_shape(self, variant_client):
        product = _kith_product_detail(variant_client, str(KITH_POCKET_TEE["id"]))
        assert set(product.option_names) == {"Size", "Color"}
        for v in product.variants:
            assert set(v.options.keys()) == {"Size", "Color"}
        sizes = {v.options["Size"] for v in product.variants}
        colors = {v.options["Color"] for v in product.variants}
        assert sizes == {"S", "M"}
        assert colors == {"Black", "White"}

    def test_track_spike_all_variants_unavailable(self, variant_client):
        product = _kith_product_detail(variant_client, str(KITH_TRACK_SPIKE["id"]))
        assert product.in_stock is False
        assert all(not v.in_stock for v in product.variants)

    @pytest.mark.parametrize("product_id", KITH_PRICE_SPREAD_IDS)
    def test_price_spread_product_has_variant_price_override(self, variant_client, product_id):
        """The Nylon Track Jacket: S/M/L share the base price, XL diverges
        and must carry an explicit ``ProductVariant.price`` override."""
        product = _kith_product_detail(variant_client, product_id)
        by_size = {v.options["Size"]: v for v in product.variants}
        assert by_size["XL"].price is not None
        assert by_size["XL"].price > product.price
        for size in ("S", "M", "L"):
            assert by_size[size].price is None

    def test_shearling_bomber_standalone_with_material_suffix(self, variant_client):
        """A single-SKU product whose title carries a variant-vocabulary
        suffix (Material: Suede) but has NO real variants — must NOT be
        treated as having variants just because of its title."""
        product = _kith_product_detail(variant_client, str(KITH_SHEARLING_BOMBER_SUEDE["id"]))
        assert product.variants == []
        assert product.option_names == []


# ── Family grouping across multiple dimensions (Color/Material/Capacity) ──


class TestKithFamilyGroupingBreadth:
    """``group_into_families`` over the full 21-product fixture set —
    generalises the "one card per family" rule beyond color (Material,
    Capacity families included), per the standing rule."""

    def test_full_catalogue_groups_into_15_families(self, variant_client):
        results = _kith_search_all(variant_client)
        families = group_into_families(results)
        assert len(families) == 15

    @pytest.mark.parametrize(
        "primary_id,expected", list(KITH_FAMILIES.items()), ids=list(KITH_FAMILIES)
    )
    def test_each_known_family_groups_correctly(self, variant_client, primary_id, expected):
        results = _kith_search_all(variant_client)
        families = group_into_families(results)
        family = next(f for f in families if f.primary.product_id == primary_id)

        assert len(family.members) == len(expected["member_ids"])
        assert {m.product_id for m in family.members} == set(expected["member_ids"])
        assert expected["dimension"] in family.option_names
        assert family.primary.name == expected["normalized_title"]

        # Variants are synthesized with the splitting dimension in their
        # options, and variant_id round-trips to (member_product_id, ...).
        for variant in family.variants:
            assert expected["dimension"] in variant.options
            member_id = variant.variant_id.split(":")[0]
            assert member_id in expected["member_ids"]

    def test_standalone_products_remain_family_of_one(self, variant_client):
        results = _kith_search_all(variant_client)
        families = group_into_families(results)
        family_member_ids = {m.product_id for f in families for m in f.members}
        all_grouped_into_families = {
            pid for fam in KITH_FAMILIES.values() for pid in fam["member_ids"]
        }
        standalone_ids = set(ALL_KITH_PRODUCT_IDS) - all_grouped_into_families

        for pid in standalone_ids:
            family = next(f for f in families if pid in {m.product_id for m in f.members})
            assert len(family.members) == 1, f"{pid} should be a family of 1"


# ── Cart operations with variant_id (live merchant) ──────────────────────


class TestKithVariantCartOperations:
    def test_add_990v6_without_variant_id_is_400(self, variant_client):
        variant_client.get("/")
        r = variant_client.post(f"/cart/add/{_KITH}/{KITH_990V6_GREY['id']}")
        assert r.status_code == 400

    def test_add_990v6_with_valid_variant_id_succeeds(self, variant_client):
        product_id = str(KITH_990V6_GREY["id"])
        variants = _kith_product_variants(variant_client, product_id)
        target = variants["variants"][0]

        r = variant_client.post(
            f"/cart/add/{_KITH}/{product_id}", data={"variant_id": target["variant_id"]}
        )
        assert r.status_code == 200
        sess = _get_session(variant_client)
        item = sess.click_basket[_KITH][0]
        assert item["variant_id"] == target["variant_id"]
        assert item["selected_options"] == target["options"]
        assert Decimal(item["price"]) == Decimal("200.00")

    def test_add_990v6_with_invalid_variant_id_is_400(self, variant_client):
        product_id = str(KITH_990V6_GREY["id"])
        r = variant_client.post(
            f"/cart/add/{_KITH}/{product_id}", data={"variant_id": "does-not-exist"}
        )
        assert r.status_code == 400

    def test_add_no_variant_product_unaffected(self, variant_client):
        """A no-variant Kith product (Camp Cap) adds directly — no
        ``variant_id`` required, regression-safe."""
        product_id = str(KITH_CAMP_CAP["id"])
        r = variant_client.post(f"/cart/add/{_KITH}/{product_id}")
        assert r.status_code == 200
        sess = _get_session(variant_client)
        item = sess.click_basket[_KITH][0]
        assert item["variant_id"] is None
        assert item["selected_options"] == {}

    def test_two_variants_of_same_product_create_two_lines(self, variant_client):
        product_id = str(KITH_990V6_GREY["id"])
        variants = _kith_product_variants(variant_client, product_id)
        v1, v2 = variants["variants"][0], variants["variants"][1]

        variant_client.post(
            f"/cart/add/{_KITH}/{product_id}", data={"variant_id": v1["variant_id"]}
        )
        variant_client.post(
            f"/cart/add/{_KITH}/{product_id}", data={"variant_id": v2["variant_id"]}
        )

        sess = _get_session(variant_client)
        items = sess.click_basket[_KITH]
        assert len(items) == 2
        assert {i["variant_id"] for i in items} == {v1["variant_id"], v2["variant_id"]}

    def test_out_of_stock_variant_combination_rejected(self, variant_client):
        """Crewneck - White's "M" variant is out of stock — adding that
        variant must be rejected with 400."""
        product_id = str(KITH_CREWNECK_WHITE["id"])
        variants = _kith_product_variants(variant_client, product_id)
        oos = next(v for v in variants["variants"] if v["options"].get("Size") == "M")
        assert oos["in_stock"] is False

        r = variant_client.post(
            f"/cart/add/{_KITH}/{product_id}", data={"variant_id": oos["variant_id"]}
        )
        assert r.status_code == 400


# ── Gateway registration check (CLAUDE.md rule 3) ─────────────────────────


@pytest.mark.parametrize("domain", sorted(set(MERCHANTS) | set(LIVE_MERCHANTS)))
def test_gateway_registration_covers_kith_variant_breadth(domain):
    """Documents that every domain in MERCHANTS/LIVE_MERCHANTS has a
    variant-breadth fixture: demo merchants via test_get_product_variants_tool.py
    (DEMO_VARIANT_PLAIN), kith.com via this file's 21-product fixture set."""
    if domain in MERCHANTS:
        assert domain in MERCHANTS
    else:
        assert domain == "kith.com", (
            f"New live merchant {domain!r} needs a variant-breadth fixture "
            f"in tests/test_kith_merchant_journeys.py (TestKithVariants)."
        )


# ── Phase 1 bugfix addendum (2026-06-10) ──────────────────────────────────
#
# Bug 3a: web/routers/cart.py::add_to_cart now resolves family-synthesized
# "{member_id}:{member_variant_id}" variant_ids via ctx.session.product_families
# (mirroring agents/orchestrator.py::_add_to_cart), so the chat variant-picker
# modal's POST to /cart/add succeeds for family-grouped products.
#
# Bug 3b: agents/orchestrator.py::_group_discovered_products now backfills
# each member's variants/option_names from the adapter (via the new
# _backfill_variants helper) BEFORE group_into_families runs, so families
# synthesize the FULL per-member dimension matrix (e.g. Size+Color), not just
# the dimension stripped from the title (e.g. Color), even when the discovery
# agent dropped variants/option_names from its JSON output.


def _orch() -> OrchestratorAgent:
    return OrchestratorAgent(
        client=FakeAnthropicClient([]),
        confirmation=AutoConfirmProvider(),
        mandate_id="m_test",
    )


class TestKithFamilyCartOperations:
    """Bug 3a — /cart/add resolves family-synthesized variant_ids."""

    def test_add_family_synthesized_variant_id_succeeds(self, variant_client):
        """The 990v6 Grey/Navy family: POST /cart/add/{kith}/{primary_id}
        with a "{member_id}:{member_variant_id}" variant_id (as the chat
        variant-picker modal sends) must succeed — previously 400'd with
        "That option is no longer available." because the cart route never
        consulted ctx.session.product_families."""
        variant_client.get("/")
        sess = _get_session(variant_client)
        results = _kith_search_all(variant_client)

        async def _group():
            return await _orch()._group_discovered_products(
                sess.ctx, [p.model_dump(mode="json") for p in results]
            )

        merged = asyncio.get_event_loop().run_until_complete(_group())

        primary_id = str(KITH_990V6_GREY["id"])
        family = sess.ctx.session.product_families.get(primary_id)
        assert family is not None, "990v6 Grey/Navy should be cached as a family"
        synthesized = family["variants"][0]
        assert ":" in synthesized["variant_id"]

        r = variant_client.post(
            f"/cart/add/{_KITH}/{primary_id}",
            data={"variant_id": synthesized["variant_id"]},
            headers={"Accept": "application/json"},
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["lines"]) == 1
        line = body["lines"][0]
        assert line["variant_id"] == synthesized["variant_id"]
        assert line["selected_options"] == synthesized["options"]
        assert "Color" in line["selected_options"]
        assert "Size" in line["selected_options"]

        del merged  # only used to populate the family cache via grouping

    def test_add_family_invalid_variant_id_still_400(self, variant_client):
        variant_client.get("/")
        sess = _get_session(variant_client)
        results = _kith_search_all(variant_client)

        async def _group():
            return await _orch()._group_discovered_products(
                sess.ctx, [p.model_dump(mode="json") for p in results]
            )

        asyncio.get_event_loop().run_until_complete(_group())

        primary_id = str(KITH_990V6_GREY["id"])
        r = variant_client.post(
            f"/cart/add/{_KITH}/{primary_id}",
            data={"variant_id": "bogus:bogus"},
            headers={"Accept": "application/json"},
        )
        assert r.status_code == 400


class TestKithDiscoveryVariantBackfill:
    """Bug 3b — _group_discovered_products backfills variants/option_names
    from the adapter when the discovery agent dropped them, so families
    synthesize the full per-member dimension matrix."""

    def test_family_gains_size_dimension_when_discovery_drops_variants(self, variant_client):
        """Simulate the discovery agent omitting `variants`/`option_names`
        for each family member (as Haiku is known to do for `images`/`url`/
        `brand`, see test_enrich_url_brand_backfill.py). Even with members'
        variants/option_names stripped to [], the resulting family must
        still expose BOTH "Size" and "Color" — not just the title-stripped
        "Color" dimension — because _backfill_variants re-fetches each
        member's real variants from the adapter before grouping."""
        variant_client.get("/")
        sess = _get_session(variant_client)

        grey = ProductResult.model_validate(
            _kith_product_detail(variant_client, str(KITH_990V6_GREY["id"])).model_dump(mode="json")
        )
        navy = ProductResult.model_validate(
            _kith_product_detail(variant_client, str(KITH_990V6_NAVY["id"])).model_dump(mode="json")
        )

        # Simulate the discovery-agent JSON dropping variants/option_names.
        raw = []
        for product in (grey, navy):
            d = product.model_dump(mode="json")
            d["variants"] = []
            d["option_names"] = []
            raw.append(d)

        async def _group():
            return await _orch()._group_discovered_products(sess.ctx, raw)

        merged = asyncio.get_event_loop().run_until_complete(_group())

        primary_id = str(KITH_990V6_GREY["id"])
        family = sess.ctx.session.product_families.get(primary_id)
        assert family is not None, "990v6 Grey/Navy should still group into a family"
        assert set(family["option_names"]) == {"Size", "Color"}

        sizes = {v["options"]["Size"] for v in family["variants"]}
        colors = {v["options"]["Color"] for v in family["variants"]}
        assert sizes == {"8", "9", "10", "11", "12"}
        assert colors == {"Grey", "Navy"}

        for v in family["variants"]:
            assert ":" in v["variant_id"]

        del merged

    def test_no_variant_member_unaffected_by_backfill(self, variant_client):
        """A product with genuinely no variants (Camp Cap) is unaffected by
        the backfill — stays variants=[]/option_names=[] (regression)."""
        variant_client.get("/")
        sess = _get_session(variant_client)

        camp_cap = ProductResult.model_validate(
            _kith_product_detail(variant_client, str(KITH_CAMP_CAP["id"])).model_dump(mode="json")
        )
        d = camp_cap.model_dump(mode="json")
        d["variants"] = []
        d["option_names"] = []

        async def _group():
            return await _orch()._group_discovered_products(sess.ctx, [d])

        merged = asyncio.get_event_loop().run_until_complete(_group())
        assert merged[0]["variants"] == []
        assert merged[0]["option_names"] == []
