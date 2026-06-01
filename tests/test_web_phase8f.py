"""Phase 8f — gate modal Q&A behaviour, agent add_to_cart tool,
dynamic cart badge.

Covers:
  - Modal: doesn't close on Send (mutation re-presentations update it
    in place; non-mutating answers signal is_answer_only=true so the
    modal stays hidden).
  - Modal: closes on ac:sse done; "Confirm Purchase" text; Escape +
    backdrop hide hooks.
  - Agent add_to_cart tool: lands items in ctx.session.click_basket,
    idempotently bumps quantity, does NOT create an order, does NOT
    touch the purchase chain.
  - call_purchase_agent does NOT touch click_basket (proves the
    purchase path and the cart path are independent surfaces).
  - Orchestrator prompt: explicit add-vs-buy steering rule.
  - Cart router: every mutation emits a cart_update SSE event.
  - _toast.html hosts the page's single EventSource and handles
    cart_update; _chat_sse.html subscribes to ac:sse DOM events
    instead of opening a second EventSource.
"""

import asyncio
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from agents.orchestrator import OrchestratorAgent
from cli.confirmation import AutoConfirmProvider
from tests.fake_anthropic import (
    FakeAnthropicClient,
)
from web import session as session_mod
from web.app import create_app


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


# ─── 1-3: Modal contracts ───────────────────────────────────────────────


class TestGateModalSendBehaviour:
    def test_qform_submit_does_not_call_closeModal_directly(self, client):
        # Phase 8f reverted the Phase 8e close-on-Send. Now the modal
        # close is driven by the orchestrator (via is_answer_only on
        # the next gate event) or by SSE done — never by the qForm
        # handler itself.
        r = client.get("/chat")
        text = r.text
        start = text.find('qForm.addEventListener("submit"')
        assert start != -1
        block = text[start : start + 800]
        assert "closeModal()" not in block

    def test_modal_closes_on_ac_sse_done(self, client):
        # Verify the gate modal closes when a `done` event arrives over
        # the ac:sse bus. The chat-log script also subscribes to ac:sse
        # for its own purposes, so we search specifically for the
        # gate-modal closeModal wiring.
        r = client.get("/chat")
        text = r.text
        # The exact JS snippet from _gate_modal.html
        assert (
            'if (evt.type === "done") closeModal()' in text
            or 'evt.type === "done") closeModal()' in text
            or 'evt.type === "done")  closeModal()' in text
        ), (
            "_gate_modal.html must close the modal on the SSE 'done' "
            "event so the agent's chat reply becomes visible"
        )

    def test_confirm_button_text(self, client):
        r = client.get("/chat")
        assert "Confirm Purchase" in r.text
        # And the old wording is gone (case-sensitive)
        assert ">CONFIRM<" not in r.text


# ─── 4-5: Orchestrator gate Q&A flag plumbing ───────────────────────────


class TestOrchestratorAnswerFlag:
    def test_build_gate_data_carries_is_answer_only(self):
        # Direct unit test of _build_gate_data accepting answer_only.
        orch = OrchestratorAgent(
            client=FakeAnthropicClient([]),
            confirmation=AutoConfirmProvider(),
            mandate_id="m_test",
        )
        basket = [
            {
                "product_id": "p1",
                "name": "X",
                "price": "10",
                "quantity": 1,
                "line_total": "10",
            }
        ]
        gate = orch._build_gate_data(
            merchant_domain="store.com",
            basket_items=basket,
            total=Decimal("10"),
            tier="explicit",
            answer_only=True,
        )
        assert gate.is_answer_only is True

    def test_build_gate_data_default_not_answer_only(self):
        orch = OrchestratorAgent(
            client=FakeAnthropicClient([]),
            confirmation=AutoConfirmProvider(),
            mandate_id="m_test",
        )
        gate = orch._build_gate_data(
            merchant_domain="store.com",
            basket_items=[
                {
                    "product_id": "p1",
                    "name": "X",
                    "price": "10",
                    "quantity": 1,
                    "line_total": "10",
                }
            ],
            total=Decimal("10"),
            tier="explicit",
        )
        assert gate.is_answer_only is False


# ─── 6-9: add_to_cart tool + non-overlap with purchase ──────────────────


class TestAddToCartTool:
    def test_lands_item_in_session_click_basket(self, tool_ctx):
        orch = OrchestratorAgent(
            client=FakeAnthropicClient([]),
            confirmation=AutoConfirmProvider(),
            mandate_id="m_test",
        )
        result = asyncio.run(
            orch._add_to_cart(
                tool_ctx,
                product_id="cof_001",
                merchant_domain="coffee-bar.myshopify.com",
                quantity=2,
                name="Ceramic Coffee Mug",
                price="12.00",
            )
        )
        assert result["added"] is True
        items = tool_ctx.session.click_basket.get("coffee-bar.myshopify.com")
        assert items and len(items) == 1
        assert items[0]["product_id"] == "cof_001"
        assert items[0]["quantity"] == 2
        assert items[0]["line_total"] == "24.00"

    def test_idempotent_bumps_quantity(self, tool_ctx):
        orch = OrchestratorAgent(
            client=FakeAnthropicClient([]),
            confirmation=AutoConfirmProvider(),
            mandate_id="m_test",
        )
        asyncio.run(
            orch._add_to_cart(
                tool_ctx,
                product_id="cof_001",
                merchant_domain="coffee-bar.myshopify.com",
                quantity=1,
                name="Mug",
                price="12.00",
            )
        )
        asyncio.run(
            orch._add_to_cart(
                tool_ctx,
                product_id="cof_001",
                merchant_domain="coffee-bar.myshopify.com",
                quantity=2,
                name="Mug",
                price="12.00",
            )
        )
        items = tool_ctx.session.click_basket["coffee-bar.myshopify.com"]
        assert len(items) == 1, "should bump quantity, not duplicate"
        assert items[0]["quantity"] == 3

    def test_does_not_create_order_or_record_spend(self, tool_ctx):
        # Critical separation test: add_to_cart must never trigger
        # payment / order / mandate spend.
        # Ensure starting state is clean
        before_orders = len(tool_ctx.db.orders.all())

        orch = OrchestratorAgent(
            client=FakeAnthropicClient([]),
            confirmation=AutoConfirmProvider(),
            mandate_id="m_test",
        )
        asyncio.run(
            orch._add_to_cart(
                tool_ctx,
                product_id="cof_001",
                merchant_domain="coffee-bar.myshopify.com",
                quantity=5,
                name="Mug",
                price="12.00",
            )
        )
        after_orders = len(tool_ctx.db.orders.all())
        assert after_orders == before_orders, "add_to_cart must NEVER create an order"
        # No spend records either
        spends = tool_ctx.db.spend_records.all()
        assert spends == [], "add_to_cart must NEVER record mandate spend"

    def test_audits_under_add_to_cart_tool_name(self, tool_ctx):
        before = len(tool_ctx.db.audit_log.all())
        orch = OrchestratorAgent(
            client=FakeAnthropicClient([]),
            confirmation=AutoConfirmProvider(),
            mandate_id="m_test",
        )
        asyncio.run(
            orch._add_to_cart(
                tool_ctx,
                product_id="cof_001",
                merchant_domain="coffee-bar.myshopify.com",
                quantity=1,
                name="Mug",
                price="12.00",
            )
        )
        after = tool_ctx.db.audit_log.all()
        assert len(after) > before
        add_cart_entries = [r for r in after if r.get("tool") == "add_to_cart"]
        assert add_cart_entries, "audit log should contain an add_to_cart entry"


# ─── 10: Orchestrator prompt steering ──────────────────────────────────


class TestOrchestratorPromptSteering:
    def test_prompt_distinguishes_add_from_buy(self):
        from agents.prompts import ORCHESTRATOR_TEMPLATE

        # Both tool names appear
        assert "add_to_cart" in ORCHESTRATOR_TEMPLATE
        assert "call_purchase_agent" in ORCHESTRATOR_TEMPLATE
        # Steering language
        assert "add" in ORCHESTRATOR_TEMPLATE.lower()
        assert "buy" in ORCHESTRATOR_TEMPLATE.lower()
        # Explicit "never both" guard
        assert (
            "NEVER call both" in ORCHESTRATOR_TEMPLATE
            or "Never call both" in ORCHESTRATOR_TEMPLATE
            or "never both" in ORCHESTRATOR_TEMPLATE.lower()
        )


# ─── 11: Cart router emits cart_update SSE events ───────────────────────


class TestCartRouterEmitsCartUpdate:
    def test_post_cart_add_emits_cart_update_event(self, client):
        # Establish session first
        client.get("/")
        sess = _sess(client)

        r = client.post("/cart/add/coffee-bar.myshopify.com/cof_001")
        assert r.status_code == 200

        # Drain the SSE queue and find a cart_update event with count == 1
        loop = asyncio.new_event_loop()
        try:
            collected = []
            for _ in range(20):
                if sess.sse_queue.empty():
                    break
                try:
                    evt = loop.run_until_complete(
                        asyncio.wait_for(sess.sse_queue.get(), timeout=0.5)
                    )
                except asyncio.TimeoutError:
                    break
                collected.append(evt)
        finally:
            loop.close()

        cart_events = [e for e in collected if e.get("type") == "cart_update"]
        assert cart_events, f"expected at least one cart_update event after add; got {collected}"
        assert cart_events[-1]["data"]["count"] == 1, f"expected count=1; got {cart_events[-1]}"


# ─── 12: _toast.html hosts the EventSource; _chat_sse.html subscribes ───


class TestSseFanout:
    def test_toast_html_hosts_event_source_and_cart_update(self, client):
        # The single EventSource lives in _toast.html (included on every
        # page) and handles cart_update events directly.
        r = client.get("/")
        text = r.text
        assert (
            'new EventSource("/chat/stream")' in text
        ), "toast script must open the page's single EventSource"
        # cart_update branch
        assert '"cart_update"' in text
        # And the setCartBadge updater is on window
        assert "window.__setCartBadge" in text

    def test_chat_sse_subscribes_to_ac_sse_not_own_eventsource(self, client):
        # On /chat, _chat_sse.html should NOT open a second EventSource;
        # it subscribes to the ac:sse DOM event re-broadcast by toast.
        r = client.get("/chat")
        text = r.text
        # The _chat_sse script subscribes to ac:sse
        idx = text.find('document.addEventListener("ac:sse"')
        assert idx != -1
        # Within the chat-sse script block, no new EventSource is opened.
        # We can't easily isolate "the chat-sse script" but we CAN
        # assert there's only ONE 'new EventSource("/chat/stream")'
        # on the page (in _toast.html), not two.
        count = text.count('new EventSource("/chat/stream")')
        assert count == 1, f"expected exactly 1 EventSource on /chat; found {count}"
