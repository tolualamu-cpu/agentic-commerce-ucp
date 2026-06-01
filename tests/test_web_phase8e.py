"""Phase 8e — save_order coercion, cart page shell, gate modal Send close.

Covers:
  - save_order now accepts a dict OR a PurchaseOrder; coerces dicts so
    chat-driven orders land in db.orders.
  - GET /cart renders the full site shell (header nav + drawer); HTMX
    swap requests still return the bare drawer partial.
  - Product cards still wire window.__bumpCart on Add to Cart.
  - Gate modal closes on Send so the user can see the agent's reply
    stream into the chat.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from models.order import OrderStatus, PurchaseOrder
from models.product import CartItem
from storage.db import OrderQ
from tools.purchase_tools import save_order
from web.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "demo.json"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = create_app()
    with TestClient(app) as c:
        yield c


def _sample_order_dict(
    merchant="athletic-co.myshopify.com",
    order_id="ord_test_8e",
    mandate_id="mandate_demo",
) -> dict:
    return {
        "order_id": order_id,
        "session_id": "cart_test_8e",
        "merchant_domain": merchant,
        "items": [
            {
                "product_id": "ath_007",
                "name": "Stability Running Shoes",
                "price": "159.00",
                "quantity": 2,
                "currency": "USD",
                "merchant_domain": merchant,
            },
        ],
        "total": "318.00",
        "currency": "USD",
        "status": "confirmed",
        "mandate_id": mandate_id,
        "payment_intent_id": "pi_test_xyz",
        "tracking_number": None,
        "estimated_delivery": "2-3 days",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


# ─── save_order coercion ────────────────────────────────────────────────


class TestSaveOrderCoerction:
    def test_dict_order_is_coerced_and_persisted(self, tool_ctx):
        payload = _sample_order_dict()
        result = asyncio.run(save_order(tool_ctx, order=payload))
        assert result == {"saved": True, "order_id": payload["order_id"]}
        # Row landed in db.orders
        row = tool_ctx.db.orders.get(OrderQ.order_id == payload["order_id"])
        assert row is not None
        assert row["merchant_domain"] == payload["merchant_domain"]
        assert row["status"] == "confirmed"

    def test_pydantic_instance_still_works(self, tool_ctx):
        order = PurchaseOrder(
            order_id="ord_test_pyd",
            session_id="cart_pyd",
            merchant_domain="athletic-co.myshopify.com",
            items=[
                CartItem(
                    product_id="ath_007",
                    name="Stability Running Shoes",
                    price=Decimal("159.00"),
                    quantity=1,
                    currency="USD",
                    merchant_domain="athletic-co.myshopify.com",
                )
            ],
            total=Decimal("159.00"),
            currency="USD",
            status=OrderStatus.CONFIRMED,
            mandate_id="mandate_demo",
            created_at=datetime.now(timezone.utc),
        )
        result = asyncio.run(save_order(tool_ctx, order=order))
        assert result == {"saved": True, "order_id": "ord_test_pyd"}
        row = tool_ctx.db.orders.get(OrderQ.order_id == "ord_test_pyd")
        assert row is not None

    def test_invalid_dict_returns_friendly_error(self, tool_ctx):
        # Missing required fields — model_validate raises
        payload = {"order_id": "ord_broken"}
        result = asyncio.run(save_order(tool_ctx, order=payload))
        assert result["saved"] is False
        assert result["reason"] == "invalid_order_payload"
        # No row written
        row = tool_ctx.db.orders.get(OrderQ.order_id == "ord_broken")
        assert row is None


# ─── Cart page shell ────────────────────────────────────────────────────


class TestCartPageShell:
    def test_get_cart_renders_full_page(self, client):
        # No HX-Request header → full page with site shell
        r = client.get("/cart")
        assert r.status_code == 200
        # Header nav from base.html (Explore + Chat tabs + logo text)
        assert 'href="/"' in r.text
        assert 'href="/chat"' in r.text
        assert "Agentic" in r.text  # logo text
        # And the drawer markup is in there too
        assert 'id="cart-drawer"' in r.text

    def test_get_cart_with_hx_request_returns_bare_partial(self, client):
        r = client.get("/cart", headers={"HX-Request": "true"})
        assert r.status_code == 200
        # Drawer present
        assert 'id="cart-drawer"' in r.text
        # But NO base.html shell (no <html>/<head>, no Explore tab link)
        assert "<html" not in r.text.lower()
        # The 'Explore' tab text from base.html nav is NOT in the partial
        assert ">Explore<" not in r.text


# ─── Cart badge wiring ──────────────────────────────────────────────────


class TestProductCardCartBumpHook:
    def test_card_invokes_bump_cart_after_request(self, client):
        # Search renders product cards
        r = client.get("/search?q=running")
        assert r.status_code == 200
        # The card's add-to-cart form must call window.__bumpCart() in
        # its hx-on::after-request handler so the badge increments
        # immediately on click.
        assert "window.__bumpCart" in r.text
        # And the function itself must be defined on every page
        # (provided by _toast.html). Search has the toast partial via
        # base.html include.
        assert "window.__bumpCart = function" in r.text


# ─── Gate modal Send → question dispatch ────────────────────────────────
# Phase 8e originally closed the modal on every Send. Phase 8f reverted
# that: the orchestrator now signals via ``is_answer_only`` whether the
# next gate event should reopen the modal (mutation) or keep it hidden
# (non-mutating answer). The test below was rewritten to verify the new
# contract — qForm dispatches the question over WS, but does NOT itself
# close the modal. Close is driven server-side via ac:sse done.


class TestGateModalSendDispatchesQuestion:
    def test_send_handler_dispatches_question(self, client):
        r = client.get("/chat")
        assert r.status_code == 200
        # The handler still sends the user's text to the gate WS.
        text = r.text
        assert 'decision: "question"' in text, "question dispatch should still be wired"

    def test_send_handler_does_not_call_closeModal_directly(self, client):
        # Phase 8f: the qForm submit handler no longer calls
        # closeModal() — mutations leave the modal open, non-mutating
        # answers close it via the ac:sse done listener.
        r = client.get("/chat")
        text = r.text
        # Find the qForm submit handler block — bounded by qForm
        # addEventListener("submit", and the next addEventListener
        # or function definition.
        start = text.find('qForm.addEventListener("submit"')
        assert start != -1, "qForm submit handler block must be present"
        # Look at the next ~600 characters which contain the handler body
        block = text[start : start + 800]
        assert 'decision: "question"' in block
        assert "closeModal()" not in block, (
            "Phase 8f reverted the close-on-send behaviour; closeModal "
            "should not appear inside the qForm submit handler block"
        )
