"""Bubble-end SSE event tests.

Verifies that:
- build_web_callbacks() wires on_bubble_end onto the StreamingCallbacks.
- The orchestrator fires on_bubble_end after every on_text call inside the
  gate Q&A loop so that intermediate gate responses and the final purchase
  confirmation appear in separate chat bubbles.
- The SSE queue receives events in the correct order: text → bubble_end.

Asyncio note: file sorts before test_user_journeys.py — uses
asyncio.get_event_loop().run_until_complete() throughout.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal


from agents.orchestrator import OrchestratorAgent, StreamingCallbacks
from cli.confirmation import AutoConfirmProvider, GateResponse
from tests.fake_anthropic import FakeAnthropicClient, text_response, tool_use_response
from web.callbacks import build_web_callbacks


# ─── build_web_callbacks wires on_bubble_end ─────────────────────────────────


def test_build_web_callbacks_has_on_bubble_end():
    """build_web_callbacks must expose a non-None on_bubble_end callback."""

    async def _run():
        queue = asyncio.Queue()
        cb = build_web_callbacks(queue)
        assert cb.on_bubble_end is not None, "build_web_callbacks must set on_bubble_end"

    asyncio.get_event_loop().run_until_complete(_run())


def test_on_bubble_end_pushes_bubble_end_event():
    """Calling on_bubble_end() pushes {'type': 'bubble_end', 'data': {}}."""

    async def _run():
        queue = asyncio.Queue()
        cb = build_web_callbacks(queue)
        await cb.on_bubble_end()
        evt = queue.get_nowait()
        assert evt["type"] == "bubble_end"
        assert evt["data"] == {}

    asyncio.get_event_loop().run_until_complete(_run())


def test_on_text_followed_by_bubble_end_in_queue():
    """on_text + on_bubble_end must produce text event then bubble_end in order."""

    async def _run():
        queue = asyncio.Queue()
        cb = build_web_callbacks(queue)
        await cb.on_text("hello")
        await cb.on_bubble_end()

        first = queue.get_nowait()
        second = queue.get_nowait()
        assert first["type"] == "text"
        assert first["data"]["delta"] == "hello"
        assert second["type"] == "bubble_end"

    asyncio.get_event_loop().run_until_complete(_run())


# ─── StreamingCallbacks dataclass includes the field ─────────────────────────


def test_streaming_callbacks_has_on_bubble_end_field():
    cb = StreamingCallbacks()
    assert hasattr(cb, "on_bubble_end")
    assert cb.on_bubble_end is None  # default is None


def test_streaming_callbacks_on_bubble_end_callable():
    async def my_bubble_end():
        pass

    cb = StreamingCallbacks(on_bubble_end=my_bubble_end)
    assert cb.on_bubble_end is my_bubble_end


# ─── Orchestrator fires bubble_end after gate Q&A on_text calls ──────────────


def _mandate(ctx, **kw):
    defaults = dict(
        user_id="user_1",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        payment_method_id="pm_test",
    )
    defaults.update(kw)
    return ctx.ap2.create_mandate(**defaults)


def test_bubble_end_emitted_after_gate_qa_on_text(tool_ctx):
    """When the gate Q&A loop fires on_text for a response, on_bubble_end must
    follow in the same callback sequence so the SSE queue contains the events
    in the right order for the client to separate bubbles."""
    m = _mandate(tool_ctx)

    # Script: user asks a question at the gate, then confirms
    confirm = AutoConfirmProvider(
        scripted=[
            GateResponse(decision="question", text="what is the return policy?"),
            GateResponse(decision="confirm"),
        ]
    )

    events = []

    async def capture_text(delta):
        events.append(("text", delta))

    async def capture_bubble_end():
        events.append(("bubble_end", None))

    cb = StreamingCallbacks(on_text=capture_text, on_bubble_end=capture_bubble_end)

    client = FakeAnthropicClient(
        [
            tool_use_response(
                (
                    "call_purchase_agent",
                    {
                        "brief": "buy",
                        "merchant_domain": "demo-shop.myshopify.com",
                        "items": [
                            {
                                "product_id": "shop_001",
                                "name": "Shoes",
                                "price": "89",
                                "quantity": 1,
                            }
                        ],
                    },
                )
            ),
            # Gate Q&A answer from Claude (answer to "what is the return policy?")
            text_response("Returns are accepted within 30 days."),
            # Purchase agent response
            text_response('{"order": null, "status": "completed"}'),
            # Orchestrator final reply
            text_response("Your order is confirmed."),
        ]
    )

    orch = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id, callbacks=cb)

    asyncio.get_event_loop().run_until_complete(orch.run(tool_ctx, "buy shoes"))

    # After the gate Q&A response text, bubble_end must follow immediately
    # (may not be the very first event, but text → bubble_end pairs must exist)
    text_then_end_pairs = [
        (events[i], events[i + 1])
        for i in range(len(events) - 1)
        if events[i][0] == "text" and events[i + 1][0] == "bubble_end"
    ]
    assert len(text_then_end_pairs) >= 1, (
        f"Expected at least one text→bubble_end pair in events; got {events}"
    )


def test_bubble_end_not_emitted_when_no_callback_set(tool_ctx):
    """If on_bubble_end is None, the orchestrator must not raise an exception."""
    m = _mandate(tool_ctx)

    confirm = AutoConfirmProvider(
        scripted=[
            GateResponse(decision="question", text="any discount?"),
            GateResponse(decision="confirm"),
        ]
    )

    cb = StreamingCallbacks(on_text=None, on_bubble_end=None)

    client = FakeAnthropicClient(
        [
            tool_use_response(
                (
                    "call_purchase_agent",
                    {
                        "brief": "buy",
                        "merchant_domain": "demo-shop.myshopify.com",
                        "items": [
                            {
                                "product_id": "shop_001",
                                "name": "Shoes",
                                "price": "89",
                                "quantity": 1,
                            }
                        ],
                    },
                )
            ),
            text_response("No active discounts at the moment."),
            text_response('{"order": null, "status": "completed"}'),
            text_response("Order placed."),
        ]
    )

    orch = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id, callbacks=cb)

    # Must not raise
    asyncio.get_event_loop().run_until_complete(orch.run(tool_ctx, "buy"))
