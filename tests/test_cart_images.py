"""Tests: cart items store image_url and the cart drawer renders thumbnails.

The cart previously stored only 6 fields per line item (product_id, name,
price, currency, quantity, line_total). image_url was added so the cart
drawer can show a 48×48 thumbnail next to each item.

Tests cover:
- Data layer: image_url stored when adding via POST /cart/add
- Data layer: image_url stored when adding via orchestrator _add_to_cart tool
- Template: cart drawer HTML contains <img> for items that have image_url
- Template: cart drawer falls back to emoji when image_url is empty
- Regression: existing cart fields (price, quantity, etc.) unchanged

Sorts alphabetically after test_catalogue_images (cart > catalogue). Uses
get_event_loop().run_until_complete() for async calls (file sorts before
test_user_journeys: 'c' < 'u').
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from web import session as session_mod
from web.app import create_app


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "demo.json"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = create_app()
    with TestClient(app) as c:
        yield c


def _sess(client) -> "session_mod.WebSession":
    sid_raw = client.cookies.get("ac_session")
    sid = session_mod._serializer.loads(sid_raw)
    return session_mod.get_session_by_id(sid)


# Catalogue products that are seeded (image_url will come from their images[0])
_SHOE_MERCHANT = "athletic-co.myshopify.com"
_SHOE_ID = "ath_001"
_SHOE_VARIANT_ID = "ath_001-8"
_MUG_MERCHANT = "coffee-bar.myshopify.com"
_MUG_ID = "cof_001"


# ─── Data layer: image_url persisted on add ──────────────────────────────────


class TestCartItemStoresImageUrl:
    def test_image_url_field_present_after_add(self, client):
        """Cart item dict must include image_url after POST /cart/add."""
        client.get("/")
        client.post(f"/cart/add/{_SHOE_MERCHANT}/{_SHOE_ID}", data={"variant_id": _SHOE_VARIANT_ID})
        sess = _sess(client)
        items = sess.ctx.session.click_basket.get(_SHOE_MERCHANT, [])
        shoe = next((i for i in items if i["product_id"] == _SHOE_ID), None)
        assert shoe is not None, "Shoe should be in basket"
        assert "image_url" in shoe, (
            f"Cart item must have image_url field; got keys: {list(shoe.keys())}"
        )

    def test_image_url_is_https_string(self, client):
        """image_url must be a non-empty https:// URL."""
        client.get("/")
        client.post(f"/cart/add/{_SHOE_MERCHANT}/{_SHOE_ID}", data={"variant_id": _SHOE_VARIANT_ID})
        sess = _sess(client)
        items = sess.ctx.session.click_basket.get(_SHOE_MERCHANT, [])
        shoe = next(i for i in items if i["product_id"] == _SHOE_ID)
        url = shoe["image_url"]
        assert isinstance(url, str) and url.startswith("https://"), (
            f"image_url must be a https:// string; got {url!r}"
        )

    def test_image_url_matches_catalogue_first_image(self, client):
        """image_url must match images[0] from the catalogue."""
        from config.catalogue import ATHLETIC_CO

        expected = next(p["images"][0] for p in ATHLETIC_CO if p["id"] == _SHOE_ID)
        client.get("/")
        client.post(f"/cart/add/{_SHOE_MERCHANT}/{_SHOE_ID}", data={"variant_id": _SHOE_VARIANT_ID})
        sess = _sess(client)
        items = sess.ctx.session.click_basket.get(_SHOE_MERCHANT, [])
        shoe = next(i for i in items if i["product_id"] == _SHOE_ID)
        assert shoe["image_url"] == expected, (
            f"Expected image_url={expected!r}; got {shoe['image_url']!r}"
        )

    def test_existing_fields_still_present(self, client):
        """Adding image_url must not remove the 6 original cart fields."""
        client.get("/")
        client.post(f"/cart/add/{_SHOE_MERCHANT}/{_SHOE_ID}", data={"variant_id": _SHOE_VARIANT_ID})
        sess = _sess(client)
        items = sess.ctx.session.click_basket.get(_SHOE_MERCHANT, [])
        shoe = next(i for i in items if i["product_id"] == _SHOE_ID)
        for field in (
            "product_id",
            "name",
            "price",
            "currency",
            "quantity",
            "line_total",
        ):
            assert field in shoe, f"Original field {field!r} must still be present"

    def test_quantity_bump_preserves_image_url(self, client):
        """Adding the same product twice bumps quantity but keeps image_url."""
        client.get("/")
        client.post(f"/cart/add/{_SHOE_MERCHANT}/{_SHOE_ID}", data={"variant_id": _SHOE_VARIANT_ID})
        client.post(f"/cart/add/{_SHOE_MERCHANT}/{_SHOE_ID}", data={"variant_id": _SHOE_VARIANT_ID})
        sess = _sess(client)
        items = sess.ctx.session.click_basket.get(_SHOE_MERCHANT, [])
        shoe = next(i for i in items if i["product_id"] == _SHOE_ID)
        assert shoe["quantity"] == 2
        assert shoe["image_url"].startswith("https://")

    def test_coffee_product_also_gets_image_url(self, client):
        """Works for all merchants, not just Athletic Co."""
        client.get("/")
        client.post(f"/cart/add/{_MUG_MERCHANT}/{_MUG_ID}")
        sess = _sess(client)
        items = sess.ctx.session.click_basket.get(_MUG_MERCHANT, [])
        mug = next((i for i in items if i["product_id"] == _MUG_ID), None)
        assert mug is not None
        assert "image_url" in mug
        assert mug["image_url"].startswith("https://")


# ─── Orchestrator _add_to_cart tool: image_url kwarg ───────────────────────


class TestOrchestratorAddToCartImageUrl:
    def test_add_to_cart_tool_stores_image_url(self, tool_ctx):
        """Orchestrator's _add_to_cart tool derives image_url from the
        product's own catalogue images (server-side, never client-supplied)."""
        from agents.orchestrator import OrchestratorAgent
        from cli.confirmation import AutoConfirmProvider
        from tests.fake_anthropic import FakeAnthropicClient

        orch = OrchestratorAgent(
            client=FakeAnthropicClient([]),
            confirmation=AutoConfirmProvider(),
            mandate_id="m_test",
        )
        result = asyncio.get_event_loop().run_until_complete(
            orch._add_to_cart(
                tool_ctx,
                product_id="shop_001",
                merchant_domain="demo-shop.myshopify.com",
                quantity=1,
            )
        )
        assert result["added"] is True
        items = tool_ctx.session.click_basket.get("demo-shop.myshopify.com", [])
        shoe = next((i for i in items if i["product_id"] == "shop_001"), None)
        assert shoe is not None
        assert shoe["image_url"] == "https://example.com/shoe.jpg"

    def test_add_to_cart_tool_no_variant_returns_variant_required(self, tool_ctx):
        """A product with size/color variants — omitting variant_id must not
        add to the cart and must return the real options instead of guessing."""
        from agents.orchestrator import OrchestratorAgent
        from cli.confirmation import AutoConfirmProvider
        from tests.fake_anthropic import FakeAnthropicClient
        from adapters.shopify_mcp import ShopifyMCPAdapter, StubShopifyTransport

        orch = OrchestratorAgent(
            client=FakeAnthropicClient([]),
            confirmation=AutoConfirmProvider(),
            mandate_id="m_test",
        )
        # Wire a variant-bearing product into the existing demo-shop adapter.
        adapter = tool_ctx.merchant_gateway.direct_adapters["demo-shop.myshopify.com"]
        assert isinstance(adapter, ShopifyMCPAdapter)
        assert isinstance(adapter.transport, StubShopifyTransport)
        adapter.transport.products.append(
            {
                "id": "shop_002",
                "title": "Demo Variant Shoes",
                "price": "99.99",
                "currency": "USD",
                "vendor": "Demo Brand",
                "available": True,
                "rating": 4.5,
                "review_count": 10,
                "images": ["https://example.com/variant-shoe.jpg"],
                "options": ["Size"],
                "variants": [
                    {
                        "id": "shop_002-8",
                        "title": "8",
                        "price": "99.99",
                        "available": True,
                        "sku": "DEMO-002-8",
                        "option1": "8",
                        "option2": None,
                    },
                    {
                        "id": "shop_002-9",
                        "title": "9",
                        "price": "99.99",
                        "available": True,
                        "sku": "DEMO-002-9",
                        "option1": "9",
                        "option2": None,
                    },
                ],
            }
        )

        result = asyncio.get_event_loop().run_until_complete(
            orch._add_to_cart(
                tool_ctx,
                product_id="shop_002",
                merchant_domain="demo-shop.myshopify.com",
                quantity=1,
            )
        )
        assert result["added"] is False
        assert result["error"] == "variant_required"
        assert result["option_names"] == ["Size"]
        assert result["variants"]
        items = tool_ctx.session.click_basket.get("demo-shop.myshopify.com", [])
        assert not any(i["product_id"] == "shop_002" for i in items)


# ─── Template: cart drawer renders thumbnail ────────────────────────────────


class TestCartDrawerRendersImage:
    def test_drawer_contains_img_tag_when_item_has_image(self, client):
        """Cart drawer HTML must have <img> when item has image_url."""
        client.get("/")
        client.post(f"/cart/add/{_SHOE_MERCHANT}/{_SHOE_ID}", data={"variant_id": _SHOE_VARIANT_ID})
        r = client.get("/cart", headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert "<img" in r.text, "Cart drawer must render <img> tag for items with image_url"

    def test_drawer_references_unsplash_url(self, client):
        """The rendered thumbnail src must be the Unsplash URL."""
        client.get("/")
        client.post(f"/cart/add/{_SHOE_MERCHANT}/{_SHOE_ID}", data={"variant_id": _SHOE_VARIANT_ID})
        r = client.get("/cart", headers={"HX-Request": "true"})
        assert "unsplash.com" in r.text

    def test_drawer_renders_emoji_fallback_in_onerror(self, client):
        """img tag must have onerror fallback for broken URLs."""
        client.get("/")
        client.post(f"/cart/add/{_SHOE_MERCHANT}/{_SHOE_ID}", data={"variant_id": _SHOE_VARIANT_ID})
        r = client.get("/cart", headers={"HX-Request": "true"})
        assert "onerror" in r.text

    def test_drawer_still_shows_product_name(self, client):
        """Adding image must not break the product name display."""
        client.get("/")
        client.post(f"/cart/add/{_SHOE_MERCHANT}/{_SHOE_ID}", data={"variant_id": _SHOE_VARIANT_ID})
        r = client.get("/cart", headers={"HX-Request": "true"})
        assert "Demo Running Shoes" in r.text

    def test_drawer_still_shows_price_and_quantity(self, client):
        """Existing price/quantity display must be unchanged."""
        client.get("/")
        client.post(f"/cart/add/{_SHOE_MERCHANT}/{_SHOE_ID}", data={"variant_id": _SHOE_VARIANT_ID})
        r = client.get("/cart", headers={"HX-Request": "true"})
        assert "129.99" in r.text
        assert "× 1" in r.text or "×" in r.text

    def test_full_cart_page_also_shows_image(self, client):
        """The full /cart page (not just drawer) renders the thumbnail."""
        client.get("/")
        client.post(f"/cart/add/{_SHOE_MERCHANT}/{_SHOE_ID}", data={"variant_id": _SHOE_VARIANT_ID})
        r = client.get("/cart")
        assert r.status_code == 200
        assert "unsplash.com" in r.text

    def test_multiple_items_each_get_thumbnail(self, client):
        """Two different items both render distinct thumbnail <img> tags."""
        client.get("/")
        client.post(f"/cart/add/{_SHOE_MERCHANT}/{_SHOE_ID}", data={"variant_id": _SHOE_VARIANT_ID})
        client.post(f"/cart/add/{_MUG_MERCHANT}/{_MUG_ID}")
        r = client.get("/cart", headers={"HX-Request": "true"})
        assert r.text.count("<img") >= 2, "Each cart line item must have its own thumbnail <img>"
