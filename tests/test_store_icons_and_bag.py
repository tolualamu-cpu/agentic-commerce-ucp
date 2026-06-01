"""Tests: branded store iconography + Carto shopping-bag image fallback.

Two visual changes are covered here:

1. Store tiles ("Shop by store" on Explore) no longer show the generic 🏬
   building emoji. Each merchant renders a brand-representative SVG icon
   (running shoe → Athletic Co, headphones → Audio Hub, coffee cup →
   Coffee Bar) via `_store_icon.html`, with a storefront fallback for any
   unknown merchant slug.

2. Every product/order placeholder that used the generic 🛍️ shopping-bag
   emoji now renders the branded Carto bag image
   (`/static/artifacts/Carto Shopping bag with logo.png`) via
   `_carto_bag.html`. The 🛒 cart emoji is intentionally left untouched.

Spans all three merchants and the cart/orders/chat/product-detail surfaces.

This file sorts BEFORE test_user_journeys.py (s < u), so per the project
asyncio rule it must NOT call asyncio.run(). It uses the synchronous
TestClient only — no event loop is created or closed here.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from web import session as session_mod
from web.app import create_app

BAG_IMG = "Carto%20Shopping%20bag%20with%20logo.png"


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
    from storage.db import OrderQ  # noqa: F401 — parity with other suites

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


_SHOE = {
    "product_id": "ath_001",
    "name": "Road Runner Shoe",
    "price": "120.00",
    "merchant_domain": "athletic-co.myshopify.com",
    "description": "Cushioned midsole for daily miles.",
    "rating": 4.6,
    "review_count": 210,
    "in_stock": True,
    "images": ["https://images.unsplash.com/photo-1234567890?w=800&q=80"],
}


# ─── Store iconography ──────────────────────────────────────────────────────


class TestStoreIcons:
    def test_explore_has_no_building_emoji(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "🏬" not in r.text, "Generic building emoji must be replaced by brand iconography"

    def test_explore_renders_brand_svgs(self, client):
        r = client.get("/")
        # Each of the three seeded merchants should produce an inline SVG
        # tile in the "Shop by store" row.
        assert "Shop by store" in r.text
        assert r.text.count("<svg") >= 3, "Expected one brand SVG icon per merchant tile"

    def test_all_three_merchant_names_present(self, client):
        r = client.get("/")
        # Display names derived from domain slugs.
        assert "Athletic Co" in r.text
        assert "Audio Hub" in r.text
        assert "Coffee Bar" in r.text

    def test_store_icon_partial_keys_off_slug(self):
        """Direct render of _store_icon.html selects the right icon per slug.

        Renders the partial in isolation (no HTTP) and checks a slug-specific
        comment is emitted, proving the conditional picks the brand icon.
        """
        app = create_app()
        env = app.state.templates.env
        tmpl = env.get_template("_store_icon.html")

        athletic = tmpl.render(m="athletic-co.myshopify.com")
        audio = tmpl.render(m="audio-hub.myshopify.com")
        coffee = tmpl.render(m="coffee-bar.myshopify.com")
        unknown = tmpl.render(m="widgets-r-us.myshopify.com")

        assert 'data-icon="shoe"' in athletic
        assert 'data-icon="headphones"' in audio
        assert 'data-icon="coffee"' in coffee
        # Unknown merchant → neutral storefront fallback, never a brand icon.
        assert 'data-icon="storefront"' in unknown


# ─── Carto bag image fallback ───────────────────────────────────────────────


class TestCartoBagFallback:
    def test_chat_fragment_no_image_uses_bag(self, client):
        client.get("/")
        no_img = dict(_SHOE, images=[])
        r = client.post("/chat/products-fragment", json={"products": [no_img]})
        assert r.status_code == 200
        assert BAG_IMG in r.text
        assert "🛍️" not in r.text

    def test_chat_fragment_with_image_still_embeds_bag_fallback(self, client):
        """Even when an image is present, the hidden onerror fallback should
        reference the branded bag (not the emoji), so a broken image swaps
        to the Carto bag rather than 🛍️."""
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_SHOE]})
        assert r.status_code == 200
        assert "unsplash.com" in r.text
        assert BAG_IMG in r.text
        assert "🛍️" not in r.text

    def test_orders_empty_order_uses_bag(self, client):
        client.get("/")
        sess = _sess(client)
        _seed_order(sess, "ord_empty_bag", "audio-hub.myshopify.com", [])
        r = client.get("/orders")
        assert r.status_code == 200
        assert BAG_IMG in r.text
        assert "🛍️" not in r.text

    def test_order_detail_unknown_item_uses_bag(self, client):
        client.get("/")
        sess = _sess(client)
        unknown_item = {
            "product_id": "unknown_777",
            "name": "Mystery Item",
            "price": "42.00",
            "quantity": 1,
            "merchant_domain": "coffee-bar.myshopify.com",
            "attributes": {},
            "item_id": None,
        }
        _seed_order(sess, "ord_bag_detail", "coffee-bar.myshopify.com", [unknown_item])
        r = client.get("/orders/ord_bag_detail")
        assert r.status_code == 200
        assert BAG_IMG in r.text
        assert "🛍️" not in r.text

    def test_bag_artifact_file_exists(self):
        """The branded bag PNG must actually be present on disk so the
        /static reference resolves."""
        from pathlib import Path
        from web.app import STATIC_DIR

        bag = Path(STATIC_DIR) / "artifacts" / "Carto Shopping bag with logo.png"
        assert bag.exists(), f"Missing bag artifact at {bag}"

    def test_cart_emoji_left_untouched(self, client):
        """The 🛒 cart emoji in the navbar must remain — only the 🛍️ bag was
        replaced."""
        r = client.get("/")
        assert "🛒" in r.text
