"""Unit + integration tests for POST /chat/products-fragment.

The endpoint accepts a JSON body {"products": [...]} and returns a server-
rendered HTML partial containing one _chat_product_card.html per product.
"""

from __future__ import annotations

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


_SHOE_PRODUCT = {
    "product_id": "ath_001",
    "name": "Demo Running Shoes",
    "description": "Lightweight road running shoes. Cushioned midsole.",
    "price": "129.99",
    "currency": "USD",
    "merchant": "Athletic Co",
    "merchant_domain": "athletic-co.myshopify.com",
    "rating": 4.5,
    "review_count": 240,
    "in_stock": True,
    "images": [
        "https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=800&q=80",
    ],
    "attributes": {"category": "running"},
    "source_protocol": "stub",
    "confidence_score": 1.0,
    "shipping_estimate": None,
    "shipping_cost": None,
    "url": None,
}

_HEADPHONE_PRODUCT = {
    "product_id": "aud_002",
    "name": "Noise-Cancelling Headphones",
    "description": "Active noise cancelling. 30h battery. Bluetooth 5.3.",
    "price": "249.00",
    "currency": "USD",
    "merchant": "Audio Hub",
    "merchant_domain": "audio-hub.myshopify.com",
    "rating": 4.6,
    "review_count": 1820,
    "in_stock": True,
    "images": [
        "https://images.unsplash.com/photo-1484704849700-f032a568e944?w=800&q=80",
    ],
    "attributes": {"category": "electronics"},
    "source_protocol": "stub",
    "confidence_score": 1.0,
    "shipping_estimate": None,
    "shipping_cost": None,
    "url": None,
}

# Kith-shaped product (real live-Shopify merchant) — every previous fixture
# in this file has url=None, so the new "Buy on {merchant}" link conditional
# path in _chat_product_card.html had ZERO test coverage before this fixture
# was added. The bug class this guards against: rendering a product with an
# external url that produces malformed HTML or breaks innerHTML injection.
_KITH_PRODUCT = {
    "product_id": "8286509301888",
    "name": "Kith Crinkled Nylon Ugo Shirt",
    "description": "Lightweight nylon button-up. Relaxed fit. 100% nylon.",
    "price": "185.00",
    "currency": "USD",
    "merchant": "Kith",
    "merchant_domain": "kith.com",
    "rating": None,
    "review_count": None,
    "in_stock": True,
    "images": [
        "https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=800&q=80",
    ],
    "attributes": {"category": "shirt"},
    "source_protocol": "shopify_storefront",
    "confidence_score": 1.0,
    "shipping_estimate": None,
    "shipping_cost": None,
    "url": "https://kith.com/products/crinkled-nylon-ugo-shirt",
}


# ─── Basic response tests ────────────────────────────────────────────────────


class TestProductsFragmentBasic:
    def test_returns_200_for_valid_products(self, client):
        client.get("/")  # establish session
        r = client.post("/chat/products-fragment", json={"products": [_SHOE_PRODUCT]})
        assert r.status_code == 200

    def test_content_type_is_html(self, client):
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_SHOE_PRODUCT]})
        assert "text/html" in r.headers["content-type"]

    def test_empty_products_returns_empty_html(self, client):
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": []})
        assert r.status_code == 200
        assert r.text.strip() == ""

    def test_missing_products_key_returns_empty(self, client):
        client.get("/")
        r = client.post("/chat/products-fragment", json={})
        assert r.status_code == 200
        assert r.text.strip() == ""

    def test_malformed_json_returns_empty(self, client):
        client.get("/")
        r = client.post(
            "/chat/products-fragment",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 200
        assert r.text.strip() == ""


# ─── Card content tests ──────────────────────────────────────────────────────


class TestProductsFragmentContent:
    def test_product_name_appears_in_html(self, client):
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_SHOE_PRODUCT]})
        assert "Demo Running Shoes" in r.text

    def test_product_price_appears(self, client):
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_SHOE_PRODUCT]})
        assert "129.99" in r.text

    def test_product_description_appears(self, client):
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_SHOE_PRODUCT]})
        assert "Cushioned midsole" in r.text

    def test_product_image_rendered_when_available(self, client):
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_SHOE_PRODUCT]})
        assert "<img" in r.text
        assert "unsplash.com" in r.text

    def test_product_image_fallback_when_no_images(self, client):
        client.get("/")
        no_image = dict(_SHOE_PRODUCT, images=[])
        r = client.post("/chat/products-fragment", json={"products": [no_image]})
        # 🛍️ emoji replaced by the branded Carto bag image.
        assert "Carto%20Shopping%20bag%20with%20logo.png" in r.text

    def test_multiple_products_all_rendered(self, client):
        client.get("/")
        r = client.post(
            "/chat/products-fragment",
            json={"products": [_SHOE_PRODUCT, _HEADPHONE_PRODUCT]},
        )
        assert "Demo Running Shoes" in r.text
        assert "Noise-Cancelling Headphones" in r.text

    def test_add_to_cart_button_present_for_in_stock(self, client):
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_SHOE_PRODUCT]})
        assert "cart-toggle-btn" in r.text
        assert "Add to cart" in r.text

    def test_no_cart_button_for_out_of_stock(self, client):
        client.get("/")
        oos = dict(_SHOE_PRODUCT, in_stock=False)
        r = client.post("/chat/products-fragment", json={"products": [oos]})
        assert "cart-toggle-btn" not in r.text

    def test_rating_and_review_count_shown(self, client):
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_SHOE_PRODUCT]})
        assert "4.5" in r.text
        assert "240" in r.text

    def test_card_has_chat_product_card_class(self, client):
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_SHOE_PRODUCT]})
        assert "chat-product-card" in r.text

    def test_card_has_data_attributes(self, client):
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_SHOE_PRODUCT]})
        assert 'data-merchant="athletic-co.myshopify.com"' in r.text
        assert 'data-product-id="ath_001"' in r.text

    def test_product_detail_link_present(self, client):
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_SHOE_PRODUCT]})
        assert "/product/athletic-co.myshopify.com/ath_001" in r.text


# ─── Cart state reflection ───────────────────────────────────────────────────


class TestProductsFragmentCartState:
    def test_in_cart_state_when_item_already_in_basket(self, client):
        """Button shows 'In cart ✕' when the item is in the session basket."""
        client.get("/")
        # Add to cart first
        client.post("/cart/add/athletic-co.myshopify.com/ath_001", data={"variant_id": "ath_001-8"})
        # Now render the fragment — should reflect cart state
        r = client.post("/chat/products-fragment", json={"products": [_SHOE_PRODUCT]})
        assert "In cart" in r.text
        assert 'data-in-cart="true"' in r.text

    def test_not_in_cart_state_for_new_item(self, client):
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_SHOE_PRODUCT]})
        assert 'data-in-cart="false"' in r.text

    def test_non_dict_items_are_skipped_gracefully(self, client):
        client.get("/")
        r = client.post(
            "/chat/products-fragment",
            json={"products": [_SHOE_PRODUCT, None, "bad", 42]},
        )
        assert r.status_code == 200
        assert "Demo Running Shoes" in r.text


# ─── External-URL ("Buy on {merchant}") tests — Kith real-merchant path ───
#
# Pins the regression where products with `url=https://…` produced cards
# that failed to render live in the chat (the bug user reported). Every
# fixture above this point has url=None, so the conditional rendering of
# the external-merchant badge had no coverage prior to these tests.


class TestProductsFragmentKithExternalUrl:
    def test_kith_product_card_renders_with_url(self, client):
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_KITH_PRODUCT]})
        assert r.status_code == 200
        assert "chat-product-card" in r.text
        assert "Kith Crinkled Nylon Ugo Shirt" in r.text

    def test_kith_card_has_buy_on_merchant_link(self, client):
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_KITH_PRODUCT]})
        assert "Buy on Kith" in r.text
        assert "https://kith.com/products/crinkled-nylon-ugo-shirt" in r.text

    def test_kith_buy_link_opens_new_tab(self, client):
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_KITH_PRODUCT]})
        # The Buy-on link MUST have target="_blank" — clicking it opens the
        # merchant's site in a new tab instead of leaving the chat session.
        # Search the window around the Buy on text to be robust against
        # whitespace and attribute ordering changes.
        idx = r.text.index("Buy on Kith")
        window = r.text[max(0, idx - 500) : idx]
        assert 'target="_blank"' in window
        assert 'rel="noopener"' in window

    def test_kith_buy_link_uses_css_class_not_inline_js(self, client):
        """The Buy-on link MUST use class='ucp-buy-badge' (CSS hover), NOT
        inline onmouseenter/onmouseleave JS handlers.

        Why this matters: when the fragment HTML is injected into the chat
        log via `placeholder.innerHTML = html`, any inline JS handlers add
        risk (CSP, browser extension blocking, etc.). The CSS-class approach
        is innerHTML-injection-safe.
        """
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_KITH_PRODUCT]})
        assert "ucp-buy-badge" in r.text
        assert "onmouseenter" not in r.text
        assert "onmouseleave" not in r.text

    def test_demo_product_has_no_buy_on_link(self, client):
        """Demo merchants (Athletic Co, Audio Hub, Coffee Bar) have url=None
        and must NOT show a Buy on link — preserves the demo flow."""
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_SHOE_PRODUCT]})
        assert "Buy on" not in r.text

    def test_kith_card_html_parses_without_error(self, client):
        """The rendered card HTML must parse cleanly so `placeholder.innerHTML`
        injection in _chat_sse.html produces a valid DOM tree (the previous
        inline-JS version was a parse hazard)."""
        from html.parser import HTMLParser

        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_KITH_PRODUCT]})
        # If HTMLParser raises, the test fails — guarantees innerHTML-safe HTML.
        parser = HTMLParser()
        parser.feed(r.text)
        parser.close()

    def test_mixed_demo_and_kith_both_render(self, client):
        """Cross-merchant baskets — demo (no url) + Kith (with url) both
        render and only the Kith card gets the Buy-on link."""
        client.get("/")
        r = client.post(
            "/chat/products-fragment",
            json={"products": [_SHOE_PRODUCT, _KITH_PRODUCT]},
        )
        assert "Demo Running Shoes" in r.text
        assert "Kith Crinkled Nylon Ugo Shirt" in r.text
        # Buy on Kith for the Kith card; Buy on Athletic Co should NOT appear
        # because _SHOE_PRODUCT has url=None.
        assert r.text.count("Buy on Kith") == 1
        assert "Buy on Athletic" not in r.text
