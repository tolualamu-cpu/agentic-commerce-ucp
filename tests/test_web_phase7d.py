"""Phase 7d — WebsocketConfirmProvider + /gate/ws + modal.

Two layers of tests:

1. Provider unit tests: enqueue a gate on outbox via _present, push a
   reply onto inbox, assert the GateResponse trichotomy is honoured.
2. WS integration: connect to /gate/ws, push a gate.open event onto the
   provider's outbox, assert the browser receives it; send a reply
   over WS, assert it lands on the inbox.
"""

import asyncio
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from cli.confirmation import GateData
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


def _gate(amount: str = "42.00") -> GateData:
    return GateData(
        merchant_domain="coffee-bar.myshopify.com",
        amount=Decimal(amount),
        currency="USD",
        item_summary="Mug × 1",
        items=[
            {
                "product_id": "cof_001",
                "name": "Mug",
                "quantity": 1,
                "price": "12.00",
                "line_total": "12.00",
            }
        ],
        full_summary=None,
        risk_flags=[],
        confidence_score=0.9,
    )


class TestProviderProtocol:
    def test_implements_confirmation_provider(self):
        p = WebsocketConfirmProvider()
        # Duck-typing check against the Protocol (ConfirmationProvider
        # is not runtime_checkable, so we verify method signatures).
        assert callable(getattr(p, "soft_confirm", None))
        assert callable(getattr(p, "explicit_confirm", None))


class TestProviderRoundTrip:
    """The provider drains stale inbox entries at the START of every gate
    (defends against WS-reconnect-triggered cancels leaking in). So these
    tests must start the gate first, then push the reply, rather than
    pre-loading the inbox."""

    def test_confirm_resolves_to_confirm(self):
        async def go():
            p = WebsocketConfirmProvider()
            task = asyncio.create_task(p.explicit_confirm(_gate()))
            # Wait for the gate to be open (drain has happened)
            await asyncio.wait_for(p.outbox.get(), timeout=1.0)
            await p.inbox.put({"decision": "confirm"})
            return await task

        resp = asyncio.run(go())
        assert resp.decision == "confirm"

    def test_cancel_resolves_to_cancel(self):
        async def go():
            p = WebsocketConfirmProvider()
            task = asyncio.create_task(p.explicit_confirm(_gate()))
            await asyncio.wait_for(p.outbox.get(), timeout=1.0)
            await p.inbox.put({"decision": "cancel"})
            return await task

        assert asyncio.run(go()).decision == "cancel"

    def test_question_passes_text(self):
        async def go():
            p = WebsocketConfirmProvider()
            task = asyncio.create_task(p.explicit_confirm(_gate()))
            await asyncio.wait_for(p.outbox.get(), timeout=1.0)
            await p.inbox.put({"decision": "question", "text": "remove 1"})
            return await task

        resp = asyncio.run(go())
        assert resp.decision == "question"
        assert resp.text == "remove 1"

    def test_stale_inbox_is_drained_at_gate_start(self):
        """Regression test for the stale-cancel bug: a leftover reply in
        the inbox from a previous gate (or a WS reconnect that pushed a
        synthetic cancel) MUST NOT be consumed by the next gate."""

        async def go():
            p = WebsocketConfirmProvider()
            # Poison the inbox with a stale cancel
            await p.inbox.put({"decision": "cancel"})
            # Now start a fresh gate; drain should clear the stale reply
            task = asyncio.create_task(p.explicit_confirm(_gate()))
            await asyncio.wait_for(p.outbox.get(), timeout=1.0)
            # Put the REAL reply; gate must see this, not the stale one
            await p.inbox.put({"decision": "confirm"})
            return await task

        resp = asyncio.run(go())
        assert resp.decision == "confirm", (
            "drain failed: stale cancel was consumed instead of fresh confirm"
        )

    def test_gate_event_published_to_outbox(self):
        """When explicit_confirm is awaiting, the outbox holds a gate.open."""

        async def go():
            p = WebsocketConfirmProvider()
            # Don't pre-load — we want to inspect outbox before reply lands.
            task = asyncio.create_task(p.explicit_confirm(_gate()))
            evt = await asyncio.wait_for(p.outbox.get(), timeout=1.0)
            # Now release the gate so the task can complete
            await p.inbox.put({"decision": "cancel"})
            await task
            return evt

        evt = asyncio.run(go())
        assert evt["type"] == "gate.open"
        assert evt["tier"] == "explicit"
        assert evt["gate"]["merchant_domain"] == "coffee-bar.myshopify.com"
        assert "amount" in evt["gate"]


class TestWebSocketBridge:
    def test_gate_event_pushed_over_ws(self, client):
        """Pushing onto outbox surfaces as a frame on /gate/ws."""
        # Establish session
        client.get("/")
        sid_raw = client.cookies.get("ac_session")
        sid = session_mod._serializer.loads(sid_raw)
        sess = session_mod.get_session_by_id(sid)

        with client.websocket_connect("/gate/ws") as ws:
            # Push an event onto the provider's outbox
            async def push():
                await sess.gate_provider.outbox.put(
                    {
                        "type": "gate.open",
                        "tier": "explicit",
                        "gate": {"merchant_domain": "coffee-bar.myshopify.com"},
                    }
                )

            asyncio.run(push())
            evt = ws.receive_json()
            assert evt["type"] == "gate.open"

    def test_browser_reply_lands_on_inbox(self, client):
        """A WS message from client should end up on the provider's inbox."""
        client.get("/")
        sid = session_mod._serializer.loads(client.cookies.get("ac_session"))
        sess = session_mod.get_session_by_id(sid)

        with client.websocket_connect("/gate/ws") as ws:
            ws.send_json({"decision": "confirm"})

            # Pull from the inbox to confirm round-trip
            async def pop():
                return await asyncio.wait_for(
                    sess.gate_provider.inbox.get(),
                    timeout=2.0,
                )

            msg = asyncio.run(pop())
            assert msg["decision"] == "confirm"

    def test_disconnect_leaves_inbox_clean(self, client):
        """Closing the WS does NOT push anything onto the inbox.

        Earlier design pushed a synthetic ``{decision:"cancel"}`` on every
        disconnect so a deadlocked orchestrator would unblock. That design
        leaked stale cancels into the next gate's inbox during normal
        page-nav-triggered reconnects, and the next gate consumed the
        stale cancel before the user could click CONFIRM. The provider's
        ``GATE_REPLY_TIMEOUT_S`` and per-gate inbox drain are the proper
        backstops for actual deadlock; routine disconnects must not
        leave residue.
        """
        client.get("/")
        sid = session_mod._serializer.loads(client.cookies.get("ac_session"))
        sess = session_mod.get_session_by_id(sid)

        with client.websocket_connect("/gate/ws"):
            pass  # immediate disconnect

        # Nothing pending on inbox after a clean disconnect. We poke the
        # private slot rather than the lazy property because Py3.9's
        # asyncio.Queue() init requires a running event loop, which the
        # main test thread doesn't have here.
        inbox = sess.gate_provider._inbox
        assert inbox is None or inbox.empty(), "disconnect must not poison the inbox queue"


class TestModalRender:
    def test_modal_in_base_template(self, client):
        r = client.get("/")
        assert 'id="gate-modal"' in r.text
        assert 'id="gate-confirm"' in r.text
        assert 'id="gate-cancel"' in r.text
        assert 'id="gate-question"' in r.text
