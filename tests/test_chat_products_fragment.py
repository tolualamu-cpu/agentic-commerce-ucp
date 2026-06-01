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
        client.post("/cart/add/athletic-co.myshopify.com/ath_001")
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
