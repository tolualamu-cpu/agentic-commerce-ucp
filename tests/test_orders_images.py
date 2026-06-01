"""Tests: orders list and order detail pages render product images.

Order pages were previously plain text (name × qty, price). They now:
- orders.html: shows a thumbnail for the first item of each order
- order_detail.html: shows a 56×56 thumbnail per item alongside name/qty/price

Images are enriched on-the-fly in account.py via _enrich_products_with_images()
which fetches from the in-memory Shopify stub adapters.

Covers all three merchants and various edge cases.
Sorts after test_user_journeys (w > u) — asyncio.run() is safe here.
"""

from __future__ import annotations

from datetime import datetime, timezone

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


def _seed_order(sess, order_id, merchant, items, amount="129.99", status="confirmed"):
    """Write a fake order directly to the session DB for testing."""
    row = {
        "order_id": order_id,
        "merchant_domain": merchant,
        "amount": amount,
        "status": status,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "items": items,
        "currency": "USD",
        "mandate_id": "m_test",
        "payment_intent_id": None,
        "tracking_number": None,
        "estimated_delivery": None,
    }
    sess.ctx.db.orders.insert(row)
    return row


def _shoe_item():
    return {
        "product_id": "ath_001",
        "name": "Demo Running Shoes",
        "price": "129.99",
        "quantity": 1,
        "merchant_domain": "athletic-co.myshopify.com",
        "attributes": {},
        "item_id": None,
    }


def _headphone_item():
    return {
        "product_id": "aud_002",
        "name": "Noise-Cancelling Headphones",
        "price": "249.00",
        "quantity": 1,
        "merchant_domain": "audio-hub.myshopify.com",
        "attributes": {},
        "item_id": None,
    }


def _mug_item():
    return {
        "product_id": "cof_001",
        "name": "Ceramic Coffee Mug",
        "price": "14.00",
        "quantity": 2,
        "merchant_domain": "coffee-bar.myshopify.com",
        "attributes": {},
        "item_id": None,
    }


# ─── Orders list page ───────────────────────────────────────────────────────


class TestOrdersListThumbnails:
    def test_orders_list_loads(self, client):
        client.get("/")
        r = client.get("/orders")
        assert r.status_code == 200

    def test_empty_orders_list_no_error(self, client):
        client.get("/")
        r = client.get("/orders")
        assert r.status_code == 200
        assert "No orders yet" in r.text

    def test_orders_list_shows_thumbnail_for_shoe_order(self, client):
        client.get("/")
        sess = _sess(client)
        _seed_order(sess, "ord_shoe_001", "athletic-co.myshopify.com", [_shoe_item()])
        r = client.get("/orders")
        assert r.status_code == 200
        assert "<img" in r.text, "Orders list must render <img> for shoe order thumbnail"

    def test_orders_list_shows_thumbnail_for_headphone_order(self, client):
        client.get("/")
        sess = _sess(client)
        _seed_order(sess, "ord_hp_001", "audio-hub.myshopify.com", [_headphone_item()])
        r = client.get("/orders")
        assert r.status_code == 200
        assert "<img" in r.text

    def test_orders_list_shows_thumbnail_for_mug_order(self, client):
        client.get("/")
        sess = _sess(client)
        _seed_order(sess, "ord_mug_001", "coffee-bar.myshopify.com", [_mug_item()])
        r = client.get("/orders")
        assert r.status_code == 200
        assert "<img" in r.text

    def test_orders_list_shows_unsplash_url(self, client):
        client.get("/")
        sess = _sess(client)
        _seed_order(sess, "ord_shoe_002", "athletic-co.myshopify.com", [_shoe_item()])
        r = client.get("/orders")
        assert "unsplash.com" in r.text, "Orders list thumbnail must reference Unsplash image URL"

    def test_orders_list_shows_bag_fallback_when_no_items(self, client):
        client.get("/")
        sess = _sess(client)
        _seed_order(sess, "ord_empty_001", "athletic-co.myshopify.com", [])
        r = client.get("/orders")
        assert r.status_code == 200
        # 🛍️ emoji replaced by the branded Carto bag image.
        assert "Carto%20Shopping%20bag%20with%20logo.png" in r.text

    def test_orders_list_still_shows_order_id(self, client):
        client.get("/")
        sess = _sess(client)
        _seed_order(sess, "ord_regression_001", "athletic-co.myshopify.com", [_shoe_item()])
        r = client.get("/orders")
        assert "ord_regression_001" in r.text

    def test_orders_list_multiple_orders(self, client):
        client.get("/")
        sess = _sess(client)
        _seed_order(sess, "ord_multi_001", "athletic-co.myshopify.com", [_shoe_item()])
        _seed_order(sess, "ord_multi_002", "audio-hub.myshopify.com", [_headphone_item()])
        _seed_order(sess, "ord_multi_003", "coffee-bar.myshopify.com", [_mug_item()])
        r = client.get("/orders")
        assert r.status_code == 200
        assert r.text.count("<img") >= 3, "Three orders with items should each show a thumbnail"


# ─── Order detail page ───────────────────────────────────────────────────────


class TestOrderDetailImages:
    def test_order_detail_loads_for_shoe_order(self, client):
        client.get("/")
        sess = _sess(client)
        _seed_order(sess, "ord_det_shoe", "athletic-co.myshopify.com", [_shoe_item()])
        r = client.get("/orders/ord_det_shoe")
        assert r.status_code == 200

    def test_order_detail_renders_img_for_athletic_co_item(self, client):
        client.get("/")
        sess = _sess(client)
        _seed_order(sess, "ord_det_ath", "athletic-co.myshopify.com", [_shoe_item()])
        r = client.get("/orders/ord_det_ath")
        assert r.status_code == 200
        assert "<img" in r.text, "Order detail must render <img> for shoe item"

    def test_order_detail_renders_img_for_audio_hub_item(self, client):
        client.get("/")
        sess = _sess(client)
        _seed_order(sess, "ord_det_aud", "audio-hub.myshopify.com", [_headphone_item()])
        r = client.get("/orders/ord_det_aud")
        assert r.status_code == 200
        assert "<img" in r.text

    def test_order_detail_renders_img_for_coffee_bar_item(self, client):
        client.get("/")
        sess = _sess(client)
        _seed_order(sess, "ord_det_cof", "coffee-bar.myshopify.com", [_mug_item()])
        r = client.get("/orders/ord_det_cof")
        assert r.status_code == 200
        assert "<img" in r.text

    def test_order_detail_shows_unsplash_url(self, client):
        client.get("/")
        sess = _sess(client)
        _seed_order(sess, "ord_det_url", "athletic-co.myshopify.com", [_shoe_item()])
        r = client.get("/orders/ord_det_url")
        assert "unsplash.com" in r.text

    def test_order_detail_shows_item_name(self, client):
        client.get("/")
        sess = _sess(client)
        _seed_order(sess, "ord_det_name", "athletic-co.myshopify.com", [_shoe_item()])
        r = client.get("/orders/ord_det_name")
        assert "Demo Running Shoes" in r.text

    def test_order_detail_shows_item_quantity(self, client):
        client.get("/")
        sess = _sess(client)
        _seed_order(sess, "ord_det_qty", "coffee-bar.myshopify.com", [_mug_item()])
        r = client.get("/orders/ord_det_qty")
        assert "2" in r.text  # quantity of mug item

    def test_order_detail_shows_item_price(self, client):
        client.get("/")
        sess = _sess(client)
        _seed_order(sess, "ord_det_price", "athletic-co.myshopify.com", [_shoe_item()])
        r = client.get("/orders/ord_det_price")
        assert "129.99" in r.text

    def test_order_detail_multi_item_all_get_thumbnails(self, client):
        client.get("/")
        sess = _sess(client)
        _seed_order(
            sess,
            "ord_det_multi",
            "athletic-co.myshopify.com",
            [
                _shoe_item(),
                dict(
                    _shoe_item(),
                    product_id="ath_007",
                    name="Stability Running Shoes",
                    price="159.00",
                ),
            ],
        )
        r = client.get("/orders/ord_det_multi")
        assert r.status_code == 200
        # Both items should get thumbnails (at least 2 img tags)
        assert r.text.count("<img") >= 2, "Multi-item order should render a thumbnail for each item"

    def test_order_detail_no_img_shows_bag_fallback(self, client):
        """Item with unknown product_id → no image → Carto bag fallback."""
        client.get("/")
        sess = _sess(client)
        unknown_item = {
            "product_id": "unknown_999",
            "name": "Unknown Product",
            "price": "50.00",
            "quantity": 1,
            "merchant_domain": "athletic-co.myshopify.com",
            "attributes": {},
            "item_id": None,
        }
        _seed_order(sess, "ord_det_unknown", "athletic-co.myshopify.com", [unknown_item])
        r = client.get("/orders/ord_det_unknown")
        assert r.status_code == 200
        assert "Carto%20Shopping%20bag%20with%20logo.png" in r.text

    def test_order_detail_not_found_returns_orders_page(self, client):
        client.get("/")
        r = client.get("/orders/nonexistent_order_id")
        assert r.status_code == 404

    def test_order_detail_regression_order_id_shown(self, client):
        client.get("/")
        sess = _sess(client)
        _seed_order(sess, "ord_det_reg", "audio-hub.myshopify.com", [_headphone_item()])
        r = client.get("/orders/ord_det_reg")
        assert "ord_det_reg" in r.text

    def test_order_detail_regression_merchant_shown(self, client):
        client.get("/")
        sess = _sess(client)
        _seed_order(sess, "ord_det_merch", "audio-hub.myshopify.com", [_headphone_item()])
        r = client.get("/orders/ord_det_merch")
        assert "audio-hub.myshopify.com" in r.text
