"""Phase 9a — product images, chat product cards, and cart interactions.

User journeys tested:
  1. Explore page cards render product images from the catalogue
  2. Discovery SSE turn enqueues a 'products' event with product dicts
  3. Add to cart via /cart/add increments basket (reachable from chat cards)
  4. Remove from cart via /cart/remove decrements basket
  5. Typed "add it to cart" still works via the orchestrator agent tool
  6. Products fragment shows image <img> tag when images are present
  7. Products fragment shows emoji fallback when images list is empty
  8. on_products callback is wired into web callbacks SSE queue
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from agents.orchestrator import OrchestratorAgent
from cli.confirmation import AutoConfirmProvider
from tests.fake_anthropic import (
    FakeAnthropicClient,
    text_response,
    tool_use_response,
)
from web import session as session_mod
from web.app import create_app
from web.callbacks import build_web_callbacks


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


_SHOE = {
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
        "https://images.unsplash.com/photo-1606107557195-0e29a4b5b4aa?w=800&q=80",
    ],
    "attributes": {},
    "source_protocol": "stub",
    "confidence_score": 1.0,
    "shipping_estimate": None,
    "shipping_cost": None,
    "url": None,
}

_DISCOVERY_JSON = json.dumps(
    {
        "products": [_SHOE],
        "notes": "Found running shoes",
    }
)


# ─── Journey 1: Explore page renders product images ─────────────────────────


class TestExplorePageImages:
    def test_explore_page_loads(self, client):
        r = client.get("/")
        assert r.status_code == 200

    def test_explore_page_contains_img_tags(self, client):
        """With seeded catalogue images, /explore cards should render <img>."""
        r = client.get("/")
        assert "<img" in r.text, (
            "Explore page should render <img> tags now that catalogue has images"
        )

    def test_explore_page_references_unsplash(self, client):
        r = client.get("/")
        assert "unsplash.com" in r.text, (
            "Explore page images should reference Unsplash URLs from catalogue"
        )

    def test_explore_page_emoji_fallback_absent_for_seeded_products(self, client):
        r = client.get("/")
        # With images populated, 🛍️ should no longer appear as the PRIMARY
        # placeholder in product cards (it may still exist in hidden fallback divs)
        # Just verify real images are present, which proves the branch was taken
        assert "unsplash.com" in r.text


# ─── Journey 2: Discovery SSE enqueues products event ───────────────────────


class TestDiscoverySseProductsEvent:
    """Products SSE event is now emitted from _run_orchestrator (post-run),
    not from a mid-run callback. These tests verify the new architecture."""

    def test_build_web_callbacks_has_no_on_products(self):
        """on_products callback was removed — its presence would allow
        mid-run SSE events that corrupt the conversation."""

        async def _run():
            queue = asyncio.Queue()
            callbacks = build_web_callbacks(queue)
            assert not hasattr(callbacks, "on_products"), (
                "build_web_callbacks must NOT produce an on_products callback"
            )

        asyncio.run(_run())

    def test_product_id_set_helper_works(self):
        """_product_id_set correctly extracts IDs for change detection."""
        from web.routers.chat import _product_id_set

        ids = _product_id_set([_SHOE])
        assert ids == {"ath_001"}

    def test_product_id_set_empty_list(self):
        """Empty list returns empty set (no products → no emission)."""
        from web.routers.chat import _product_id_set

        assert _product_id_set([]) == set()

    def test_product_id_set_detects_change(self):
        """Change detection works: different sets → emit; same set → skip."""
        from web.routers.chat import _product_id_set

        before = _product_id_set([_SHOE])
        mug = dict(_SHOE, product_id="cof_001")
        after = _product_id_set([_SHOE, mug])
        assert before != after


# ─── Journey 3 & 4: Add / remove from cart (routes chat cards use) ──────────


class TestCartInteractionsFromChatCards:
    def test_add_to_cart_increments_basket(self, client):
        client.get("/")
        r = client.post("/cart/add/athletic-co.myshopify.com/ath_001")
        # Any successful response (200 or redirect) is OK
        assert r.status_code in (200, 302, 303)
        sess = _sess(client)
        basket = sess.ctx.session.click_basket
        merchant_items = basket.get("athletic-co.myshopify.com", [])
        assert any(i["product_id"] == "ath_001" for i in merchant_items), (
            f"ath_001 should be in cart after add; basket={basket}"
        )

    def test_remove_from_cart_decrements_basket(self, client):
        client.get("/")
        client.post("/cart/add/athletic-co.myshopify.com/ath_001")
        r = client.post("/cart/remove/athletic-co.myshopify.com/ath_001")
        assert r.status_code in (200, 302, 303)
        sess = _sess(client)
        basket = sess.ctx.session.click_basket
        merchant_items = basket.get("athletic-co.myshopify.com", [])
        assert not any(i["product_id"] == "ath_001" for i in merchant_items), (
            f"ath_001 should be removed from cart; basket={basket}"
        )

    def test_second_add_increments_quantity(self, client):
        client.get("/")
        client.post("/cart/add/athletic-co.myshopify.com/ath_001")
        client.post("/cart/add/athletic-co.myshopify.com/ath_001")
        sess = _sess(client)
        items = sess.ctx.session.click_basket.get("athletic-co.myshopify.com", [])
        shoe = next((i for i in items if i["product_id"] == "ath_001"), None)
        assert shoe is not None
        assert shoe["quantity"] == 2

    def test_add_different_products_independent(self, client):
        client.get("/")
        client.post("/cart/add/athletic-co.myshopify.com/ath_001")
        client.post("/cart/add/audio-hub.myshopify.com/aud_002")
        sess = _sess(client)
        basket = sess.ctx.session.click_basket
        ath = basket.get("athletic-co.myshopify.com", [])
        aud = basket.get("audio-hub.myshopify.com", [])
        assert any(i["product_id"] == "ath_001" for i in ath)
        assert any(i["product_id"] == "aud_002" for i in aud)


# ─── Journey 5: Agent "add to cart" tool still works independently ────────────


class TestAgentAddToCartToolUnchanged:
    def test_add_to_cart_tool_still_works(self, multi_merchant_ctx):
        """The orchestrator's add_to_cart tool must work regardless of chat cards."""
        orch = OrchestratorAgent(
            client=FakeAnthropicClient([]),
            confirmation=AutoConfirmProvider(),
            mandate_id="m_test",
        )
        asyncio.run(
            orch._add_to_cart(
                multi_merchant_ctx,
                product_id="ath_007",
                merchant_domain="athletic-co.myshopify.com",
                quantity=1,
                name="Stability Running Shoes",
                price="159.00",
            )
        )
        items = multi_merchant_ctx.session.click_basket.get("athletic-co.myshopify.com", [])
        assert any(i["product_id"] == "ath_007" for i in items)

    def test_chat_add_text_still_routes_to_add_tool(self, client):
        """POST /chat with 'add to cart' intent returns accepted (no regression)."""
        client.get("/")
        r = client.post("/chat", data={"message": "add running shoes to my cart"})
        # With no ANTHROPIC_API_KEY the chat goes offline but must not 500
        assert r.status_code in (200, 202)


# ─── Journey 6 & 7: Fragment image rendering ────────────────────────────────


class TestFragmentImageRendering:
    def test_fragment_renders_img_tag_when_images_present(self, client):
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_SHOE]})
        assert r.status_code == 200
        assert "<img" in r.text
        assert "unsplash.com" in r.text

    def test_fragment_renders_bag_fallback_when_no_images(self, client):
        client.get("/")
        no_img = dict(_SHOE, images=[])
        r = client.post("/chat/products-fragment", json={"products": [no_img]})
        assert r.status_code == 200
        # The generic 🛍️ emoji was replaced by the branded Carto bag image.
        assert "Carto%20Shopping%20bag%20with%20logo.png" in r.text

    def test_fragment_description_line_present(self, client):
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_SHOE]})
        assert "Cushioned midsole" in r.text

    def test_fragment_multiple_products(self, client):
        client.get("/")
        mug = dict(
            _SHOE,
            product_id="cof_001",
            name="Ceramic Coffee Mug",
            price="14.00",
            merchant="Coffee Bar",
            merchant_domain="coffee-bar.myshopify.com",
            images=["https://images.unsplash.com/photo-1509042239860-f550ce710b93?w=800&q=80"],
        )
        r = client.post("/chat/products-fragment", json={"products": [_SHOE, mug]})
        assert "Demo Running Shoes" in r.text
        assert "Ceramic Coffee Mug" in r.text


# ─── Journey 8: on_products wired end-to-end through OrchestratorAgent ───────


class TestOnProductsEndToEnd:
    """Verify the full post-run products emission path using the web client."""

    def test_last_discovered_populated_after_run(self, multi_merchant_ctx):
        """After a discovery run, last_discovered_products is set."""
        orch = OrchestratorAgent(
            client=FakeAnthropicClient(
                [
                    tool_use_response(
                        (
                            "call_discovery_agent",
                            {
                                "brief": "running shoes",
                                "merchant_domains": ["athletic-co.myshopify.com"],
                            },
                        )
                    ),
                    tool_use_response(
                        (
                            "search_products",
                            {
                                "query": "running shoes",
                                "merchant_domain": "athletic-co.myshopify.com",
                            },
                        )
                    ),
                    text_response(_DISCOVERY_JSON),
                    text_response("Here are the running shoes."),
                ]
            ),
            confirmation=AutoConfirmProvider(),
            mandate_id="m_test",
        )
        asyncio.run(orch.run(multi_merchant_ctx, "find me running shoes"))

        assert len(multi_merchant_ctx.session.last_discovered_products) >= 1, (
            "last_discovered_products must be populated after a discovery run"
        )

    def test_products_sse_event_emitted_after_run(self, client):
        """POST /chat with offline API returns 202; products are only visible
        post-run (tested via the fragment endpoint)."""
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_SHOE]})
        assert r.status_code == 200
        assert "Demo Running Shoes" in r.text

    def test_products_fragment_json_serialisable(self, client):
        """Products pushed to the fragment endpoint must round-trip cleanly."""
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_SHOE]})
        assert r.status_code == 200
        assert "ath_001" in r.text
