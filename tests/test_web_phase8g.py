"""Phase 8g — chat→gate input routing + get_cart_contents tool.

Fixes the deadlock root cause: today's POST /chat acquires
``sess.orchestrator_lock`` for the entire run. If the orchestrator is
blocked inside ``confirmation.explicit_confirm`` awaiting gate input,
a SECOND POST /chat would block on the lock forever. Meanwhile the
gate's inbox stays empty because the WS bridge is the only feeder.
Phase 8g unifies the input: when the gate provider is awaiting input,
POST /chat routes the user's text onto the inbox as a question.

Also adds a read-only ``get_cart_contents`` tool so the agent can
resolve references like "buy them" or "purchase what's in my cart"
without guessing from discovery results.
"""

import asyncio
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from agents.orchestrator import OrchestratorAgent
from cli.confirmation import AutoConfirmProvider
from tests.fake_anthropic import FakeAnthropicClient
from web import session as session_mod
from web.app import create_app
from web.gate_provider import WebsocketConfirmProvider


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


# ─── Awaiting-input flag on the provider ────────────────────────────────


class TestGateProviderAwaitingFlag:
    def test_default_state_is_not_awaiting(self):
        p = WebsocketConfirmProvider()
        assert p.awaiting_input is False

    def test_flag_set_during_present_cleared_after(self):
        """Verify the flag transitions: False → True (while inbox.get
        blocks) → False (after a reply lands)."""
        from cli.confirmation import GateData

        gate = GateData(
            merchant_domain="store.com",
            amount=Decimal("10"),
            currency="USD",
            item_summary="x",
            items=[
                {
                    "product_id": "p",
                    "name": "X",
                    "price": "10",
                    "quantity": 1,
                    "line_total": "10",
                }
            ],
        )

        async def go():
            p = WebsocketConfirmProvider()
            states = []
            task = asyncio.create_task(p.explicit_confirm(gate))
            # Drain the queued gate.open so the provider proceeds to
            # the inbox.get() await.
            await asyncio.wait_for(p.outbox.get(), timeout=1.0)
            # Now wait briefly for the flag to be set
            for _ in range(20):
                if p.awaiting_input:
                    break
                await asyncio.sleep(0.01)
            states.append(("during_await", p.awaiting_input))
            # Send a reply; the task should unblock and clear the flag.
            await p.inbox.put({"decision": "confirm"})
            await task
            states.append(("after_reply", p.awaiting_input))
            return states

        states = asyncio.run(go())
        assert states[0] == ("during_await", True)
        assert states[1] == ("after_reply", False)


# ─── POST /chat routes to gate inbox when active ────────────────────────


class TestChatRoutesToGateWhenActive:
    def test_post_chat_routes_to_gate_inbox(self, client):
        # Establish session and force the gate provider into the
        # "awaiting input" state directly (no real orchestrator run
        # needed for this contract test).
        client.get("/")
        sess = _sess(client)
        sess.gate_provider.awaiting_input = True

        r = client.post("/chat", data={"message": "remove 1"})
        assert r.status_code == 202
        body = r.json()
        assert body["status"] == "routed_to_gate", f"expected routing to gate; got {body}"

        # The message should be on the gate inbox, not consumed by a
        # new orchestrator run.
        async def pop():
            return await asyncio.wait_for(
                sess.gate_provider.inbox.get(),
                timeout=1.0,
            )

        msg = asyncio.run(pop())
        assert msg == {"decision": "question", "text": "remove 1"}

    def test_post_chat_echoes_user_into_sse_when_routed(self, client):
        client.get("/")
        sess = _sess(client)
        sess.gate_provider.awaiting_input = True

        client.post("/chat", data={"message": "what's the total?"})

        # The chat UI should still see the user's own line (echoed
        # onto the SSE queue) so the message appears in their thread.
        async def drain():
            return await asyncio.wait_for(
                sess.sse_queue.get(),
                timeout=1.0,
            )

        evt = asyncio.run(drain())
        assert evt["type"] == "user"
        assert evt["data"]["text"] == "what's the total?"

    def test_post_chat_does_not_route_when_gate_inactive(self, client):
        # When awaiting_input is False (no active gate), POST /chat
        # behaves normally — accepts the message and starts orchestrator.
        client.get("/")
        sess = _sess(client)
        sess.gate_provider.awaiting_input = False

        r = client.post("/chat", data={"message": "find me coffee mugs"})
        assert r.status_code == 202
        body = r.json()
        # accepted (normal run) OR offline (no API key) — both are
        # acceptable non-route outcomes.
        assert body["status"] in (
            "accepted",
            "offline",
        ), f"expected normal flow; got {body}"
        # And the gate inbox stays empty
        assert sess.gate_provider._inbox is None or sess.gate_provider._inbox.empty()


# ─── get_cart_contents tool ─────────────────────────────────────────────


class TestGetCartContentsTool:
    def _orch(self):
        return OrchestratorAgent(
            client=FakeAnthropicClient([]),
            confirmation=AutoConfirmProvider(),
            mandate_id="m_test",
        )

    def test_empty_cart_returns_is_empty(self, tool_ctx):
        result = asyncio.run(self._orch()._get_cart_contents(tool_ctx))
        assert result["is_empty"] is True
        assert result["items"] == []
        assert result["item_count"] == 0

    def test_returns_items_after_add(self, tool_ctx):
        orch = self._orch()
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
        asyncio.run(
            orch._add_to_cart(
                tool_ctx,
                product_id="ath_007",
                merchant_domain="athletic-co.myshopify.com",
                quantity=1,
                name="Shoes",
                price="159.00",
            )
        )

        result = asyncio.run(orch._get_cart_contents(tool_ctx))
        assert result["is_empty"] is False
        assert result["item_count"] == 3
        # Decimal: 2 * 12 + 1 * 159 = 183.00
        assert result["subtotal"] == "183.00"
        # Items tagged with merchant_domain
        merchants = {it["merchant_domain"] for it in result["items"]}
        assert merchants == {"coffee-bar.myshopify.com", "athletic-co.myshopify.com"}

    def test_tool_is_registered_with_orchestrator(self):
        from agents.orchestrator import OrchestratorAgent

        orch = OrchestratorAgent(
            client=FakeAnthropicClient([]),
            confirmation=AutoConfirmProvider(),
            mandate_id="m_test",
        )
        names = {spec.name for spec in orch.tool_specs}
        assert "get_cart_contents" in names

    def test_does_not_mutate_cart(self, tool_ctx):
        # Read-only invariant: calling get_cart_contents N times must
        # leave click_basket exactly as it was.
        orch = self._orch()
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
        before = dict(tool_ctx.session.click_basket)
        for _ in range(3):
            asyncio.run(orch._get_cart_contents(tool_ctx))
        after = dict(tool_ctx.session.click_basket)
        assert after == before


# ─── Orchestrator prompt: get_cart_contents steering ────────────────────


class TestOrchestratorPromptCartGuidance:
    def test_prompt_mentions_get_cart_contents(self):
        from agents.prompts import ORCHESTRATOR_TEMPLATE

        assert "get_cart_contents" in ORCHESTRATOR_TEMPLATE

    def test_prompt_steers_buy_them_to_cart_lookup(self):
        from agents.prompts import ORCHESTRATOR_TEMPLATE

        # The rule about "buy them / purchase those" calling
        # get_cart_contents first must be in the prompt.
        text = ORCHESTRATOR_TEMPLATE.lower()
        assert "buy them" in text or "purchase those" in text or "buy what's in" in text
