"""Tests: the cart page reflects purchase completion.

Bug being guarded against: clicking "Review purchase" POSTs to /chat with
hx-swap="none", so the cart page never updated on its own. The orchestrator
runs (often behind the gate modal) and, on success, purges the bought items
from the basket server-side — but the user stared at an unchanged cart and
could not tell the purchase had completed.

The fix wraps the cart body in `#cart-content` (carrying a data-item-count)
and adds a client listener that, on the SSE "done" frame, re-fetches /cart,
swaps the content in place, and toasts when the cart shrank.

These tests cover the *server-rendered scaffolding* the JS depends on:
  - #cart-content + data-item-count reflect the real basket count
  - the refresh script (ac:sse "done" listener) is present on the page
  - after the basket is emptied (the post-purchase server state), GET /cart
    renders item_count 0 and the empty-state Browse CTA — i.e. the content
    the swap pulls in.

Coverage spans all three merchants and a cross-merchant basket, per the
project's diverse-journey rule.

Sorts BEFORE test_user_journeys.py (c < u): synchronous TestClient only,
no asyncio.run() — the event loop is never created or closed here.
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


def _add(client, merchant, product_id, qty=1, variant_id=None):
    data = {"quantity": qty}
    if variant_id:
        data["variant_id"] = variant_id
    return client.post(f"/cart/add/{merchant}/{product_id}", data=data)


# product_id -> variant_id for catalogue products that carry variants/options
_VARIANT_IDS = {
    "ath_001": "ath_001-8",
    "ath_006": "ath_006-8",
    "aud_001": "aud_001-Black",
    "aud_002": "aud_002-Black",
    "cof_003": "cof_003-12oz",
}


ATH = "athletic-co.myshopify.com"
AUD = "audio-hub.myshopify.com"
COF = "coffee-bar.myshopify.com"


# ─── Scaffolding the live-refresh JS relies on ──────────────────────────────


class TestCartContentScaffolding:
    def test_cart_page_has_cart_content_wrapper(self, client):
        client.get("/")
        r = client.get("/cart")
        assert r.status_code == 200
        assert 'id="cart-content"' in r.text
        assert "data-item-count=" in r.text

    def test_empty_cart_reports_zero_count(self, client):
        client.get("/")
        r = client.get("/cart")
        assert 'data-item-count="0"' in r.text
        # Empty-state CTA present so a post-purchase swap shows it.
        assert "Nothing in your cart yet." in r.text
        assert "Browse products" in r.text

    def test_refresh_listener_present(self, client):
        client.get("/")
        r = client.get("/cart")
        # The page wires a "done" SSE listener that re-fetches /cart.
        assert "ac:sse" in r.text
        assert '"done"' in r.text
        assert 'fetch("/cart"' in r.text

    @pytest.mark.parametrize(
        "merchant,pid",
        [
            (ATH, "ath_001"),
            (AUD, "aud_001"),
            (COF, "cof_001"),
        ],
    )
    def test_count_reflects_single_merchant_basket(self, client, merchant, pid):
        client.get("/")
        _add(client, merchant, pid, qty=2, variant_id=_VARIANT_IDS.get(pid))
        r = client.get("/cart")
        assert 'data-item-count="2"' in r.text
        # Items present means the empty-state CTA is hidden.
        assert "Nothing in your cart yet." not in r.text

    def test_count_reflects_cross_merchant_basket(self, client):
        client.get("/")
        _add(client, ATH, "ath_001", qty=1, variant_id=_VARIANT_IDS["ath_001"])
        _add(client, AUD, "aud_001", qty=1, variant_id=_VARIANT_IDS["aud_001"])
        _add(client, COF, "cof_001", qty=3)
        r = client.get("/cart")
        # 1 + 1 + 3 = 5 items across three merchants.
        assert 'data-item-count="5"' in r.text


# ─── Post-purchase server state (what the swap pulls in) ─────────────────────


class TestPostPurchaseState:
    @pytest.mark.parametrize(
        "merchant,pid",
        [
            (ATH, "ath_006"),  # ~mid-price athletic
            (AUD, "aud_002"),  # noise-cancelling headphones
            (COF, "cof_003"),  # single-origin beans
        ],
    )
    def test_emptied_basket_renders_empty_state(self, client, merchant, pid):
        """Simulate the server side of a completed purchase: the
        orchestrator purges bought items from click_basket. The cart page
        the refresh fetch pulls in must then show the empty state."""
        client.get("/")
        _add(client, merchant, pid, qty=1, variant_id=_VARIANT_IDS.get(pid))
        # Confirm it's non-empty first.
        assert 'data-item-count="1"' in client.get("/cart").text

        # Mimic _purge_purchased_from_cart clearing the merchant bucket.
        sess = _sess(client)
        sess.click_basket.pop(merchant, None)

        r = client.get("/cart")
        assert 'data-item-count="0"' in r.text
        assert "Nothing in your cart yet." in r.text
        assert "Browse products" in r.text

    def test_partial_purchase_keeps_other_merchant_items(self, client):
        """A purchase clears only the purchased merchant's bucket; items
        from a different merchant remain so the cart isn't shown empty."""
        client.get("/")
        _add(client, ATH, "ath_001", qty=1, variant_id=_VARIANT_IDS["ath_001"])
        _add(client, COF, "cof_001", qty=2)
        assert 'data-item-count="3"' in client.get("/cart").text

        # Athletic items bought & purged; coffee remains.
        sess = _sess(client)
        sess.click_basket.pop(ATH, None)

        r = client.get("/cart")
        assert 'data-item-count="2"' in r.text
        assert "Nothing in your cart yet." not in r.text
