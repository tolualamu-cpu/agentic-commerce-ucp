"""Phase 8h — confirm/cancel via chat + post-purchase cart purge.

Two regressions surfaced after Phase 8g unified chat→gate routing:

  - Phase 8g routed EVERY chat message as ``decision: "question"``,
    so a user typing "CONFIRM" in chat could never approve the gate.
    Fix: parse the chat text for confirm / cancel intent and emit the
    matching ``decision`` field.
  - Items the user bought via the purchase flow lingered in
    ``click_basket`` (the cart badge kept showing them and re-ordering
    "what's in my cart" would double-buy). Fix: purge purchased
    product_ids from the basket after the orchestrator's
    ``call_purchase_agent`` returns ``status="completed"``.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from agents.orchestrator import OrchestratorAgent
from cli.confirmation import AutoConfirmProvider
from tests.fake_anthropic import FakeAnthropicClient
from web import session as session_mod
from web.app import create_app
from web.routers.chat import _classify_gate_intent


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


# ─── Intent classifier ──────────────────────────────────────────────────


class TestClassifyGateIntent:
    @pytest.mark.parametrize(
        "text",
        [
            "CONFIRM",
            "confirm",
            " Confirm ",
            "confirm.",
            "ok confirm",
            "yes buy",
            "yes buy it",
            "buy it now",
            "approve",
            "proceed",
            "go ahead",
        ],
    )
    def test_confirm_intents(self, text):
        result = _classify_gate_intent(text)
        assert result == {"decision": "confirm"}, (
            f"{text!r} should classify as confirm; got {result}"
        )

    @pytest.mark.parametrize(
        "text",
        [
            "cancel",
            "CANCEL",
            "Cancel ",
            "cancel.",
            "no",
            "no thanks",
            "stop",
            "abort",
            "don't buy",
            "nevermind",
        ],
    )
    def test_cancel_intents(self, text):
        result = _classify_gate_intent(text)
        assert result == {"decision": "cancel"}, f"{text!r} should classify as cancel; got {result}"

    @pytest.mark.parametrize(
        "text",
        [
            "remove 1",
            "what's the total?",
            "add 1 mug",
            "tell me about premium shoes",
            "1",  # numeric resolver input — orchestrator handles
            "i mean stability shoes",
        ],
    )
    def test_question_intents(self, text):
        result = _classify_gate_intent(text)
        assert result["decision"] == "question"
        assert result["text"] == text

    def test_empty_string(self):
        result = _classify_gate_intent("")
        assert result == {"decision": "question", "text": ""}


# ─── Chat-routed CONFIRM actually resolves the gate ─────────────────────


class TestChatConfirmRoutesAsDecision:
    def test_routed_confirm_yields_confirm_decision(self, client):
        client.get("/")
        sess = _sess(client)
        sess.gate_provider.awaiting_input = True

        r = client.post("/chat", data={"message": "CONFIRM"})
        assert r.status_code == 202
        body = r.json()
        assert body["decision"] == "confirm", (
            f"expected the chat router to classify CONFIRM as a confirm decision; got {body}"
        )

        # And the message lands on the gate inbox as decision=confirm
        async def pop():
            return await asyncio.wait_for(
                sess.gate_provider.inbox.get(),
                timeout=1.0,
            )

        msg = asyncio.run(pop())
        assert msg == {"decision": "confirm"}

    def test_routed_cancel_yields_cancel_decision(self, client):
        client.get("/")
        sess = _sess(client)
        sess.gate_provider.awaiting_input = True

        r = client.post("/chat", data={"message": "cancel"})
        assert r.json()["decision"] == "cancel"

        async def pop():
            return await asyncio.wait_for(
                sess.gate_provider.inbox.get(),
                timeout=1.0,
            )

        msg = asyncio.run(pop())
        assert msg == {"decision": "cancel"}

    def test_routed_question_stays_a_question(self, client):
        client.get("/")
        sess = _sess(client)
        sess.gate_provider.awaiting_input = True

        r = client.post("/chat", data={"message": "what is the total?"})
        assert r.json()["decision"] == "question"

        async def pop():
            return await asyncio.wait_for(
                sess.gate_provider.inbox.get(),
                timeout=1.0,
            )

        msg = asyncio.run(pop())
        assert msg == {"decision": "question", "text": "what is the total?"}


# ─── Post-purchase cart purge ───────────────────────────────────────────


class TestPurgePurchasedFromCart:
    def _orch(self):
        return OrchestratorAgent(
            client=FakeAnthropicClient([]),
            confirmation=AutoConfirmProvider(),
            mandate_id="m_test",
        )

    def test_removes_only_purchased_product_ids(self, multi_merchant_ctx):
        tool_ctx = multi_merchant_ctx
        # Seed two items in the cart from the same merchant
        orch = self._orch()
        asyncio.run(
            orch._add_to_cart(
                tool_ctx,
                product_id="cof_001",
                merchant_domain="coffee-bar.myshopify.com",
                quantity=1,
            )
        )
        asyncio.run(
            orch._add_to_cart(
                tool_ctx,
                product_id="cof_002",
                merchant_domain="coffee-bar.myshopify.com",
                quantity=1,
                variant_id="cof_002-16oz",
            )
        )
        before = list(tool_ctx.session.click_basket["coffee-bar.myshopify.com"])
        assert len(before) == 2

        # User buys ONLY cof_001
        orch._purge_purchased_from_cart(
            tool_ctx,
            "coffee-bar.myshopify.com",
            [{"product_id": "cof_001", "name": "Mug", "quantity": 1, "price": "12.00"}],
        )

        remaining = tool_ctx.session.click_basket.get("coffee-bar.myshopify.com", [])
        ids = {it["product_id"] for it in remaining}
        assert ids == {"cof_002"}, f"only cof_001 should have been purged; remaining: {ids}"

    def test_drops_empty_merchant_bucket(self, multi_merchant_ctx):
        tool_ctx = multi_merchant_ctx
        orch = self._orch()
        asyncio.run(
            orch._add_to_cart(
                tool_ctx,
                product_id="cof_001",
                merchant_domain="coffee-bar.myshopify.com",
                quantity=1,
            )
        )
        # Buy the only item from that merchant
        orch._purge_purchased_from_cart(
            tool_ctx,
            "coffee-bar.myshopify.com",
            [{"product_id": "cof_001", "name": "Mug", "quantity": 1, "price": "12.00"}],
        )
        # Empty bucket should be removed (no dangling {merchant: []})
        assert "coffee-bar.myshopify.com" not in tool_ctx.session.click_basket

    def test_does_not_touch_other_merchant_baskets(self, multi_merchant_ctx):
        tool_ctx = multi_merchant_ctx
        orch = self._orch()
        asyncio.run(
            orch._add_to_cart(
                tool_ctx,
                product_id="cof_001",
                merchant_domain="coffee-bar.myshopify.com",
                quantity=1,
            )
        )
        asyncio.run(
            orch._add_to_cart(
                tool_ctx,
                product_id="ath_007",
                merchant_domain="athletic-co.myshopify.com",
                quantity=1,
                variant_id="ath_007-8-Standard",
            )
        )
        orch._purge_purchased_from_cart(
            tool_ctx,
            "coffee-bar.myshopify.com",
            [{"product_id": "cof_001", "name": "Mug", "quantity": 1, "price": "12.00"}],
        )
        # Athletic Co's bucket should be intact
        athletic = tool_ctx.session.click_basket.get("athletic-co.myshopify.com", [])
        assert any(it["product_id"] == "ath_007" for it in athletic)

    def test_no_op_when_purchased_items_not_in_cart(self, multi_merchant_ctx):
        tool_ctx = multi_merchant_ctx
        # User bought something they never added to cart — purge must
        # not crash and the empty cart stays empty.
        orch = self._orch()
        orch._purge_purchased_from_cart(
            tool_ctx,
            "coffee-bar.myshopify.com",
            [{"product_id": "cof_001", "name": "Mug", "quantity": 1, "price": "12.00"}],
        )
        assert tool_ctx.session.click_basket == {}

    def test_notifier_pushes_cart_update_after_purge(self, multi_merchant_ctx):
        tool_ctx = multi_merchant_ctx
        # Wire a notifier so we can capture the cart_update event
        received = []
        tool_ctx.cart_event_notifier = lambda evt: received.append(evt)

        orch = self._orch()
        asyncio.run(
            orch._add_to_cart(
                tool_ctx,
                product_id="cof_001",
                merchant_domain="coffee-bar.myshopify.com",
                quantity=2,
            )
        )
        received.clear()  # ignore the add's notification

        orch._purge_purchased_from_cart(
            tool_ctx,
            "coffee-bar.myshopify.com",
            [{"product_id": "cof_001", "name": "Mug", "quantity": 2, "price": "12.00"}],
        )
        # Notifier fired with the new count (0 since everything purged)
        assert received, "purge should fire a cart_update event"
        last = received[-1]
        assert last["type"] == "cart_update"
        assert last["data"]["count"] == 0
