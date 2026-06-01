"""OrchestratorAgent: HITL gate tier classification + cancel/approve paths."""

from __future__ import annotations

import asyncio
from decimal import Decimal


from agents.orchestrator import OrchestratorAgent, StreamingCallbacks
from cli.confirmation import AutoConfirmProvider, classify_gate
from tests.fake_anthropic import FakeAnthropicClient, text_response, tool_use_response


# ─── Tier classifier unit tests (pure function — no orchestration needed) ───


def test_classify_tier_soft_for_small_known_merchant():
    assert classify_gate(Decimal("25")) == "soft"


def test_classify_tier_explicit_for_moderate_amount():
    assert classify_gate(Decimal("150")) == "explicit"


def test_classify_tier_explicit_with_summary_for_large():
    assert classify_gate(Decimal("600")) == "explicit_with_summary"


def test_classify_tier_upgrades_when_confidence_low():
    assert classify_gate(Decimal("25"), confidence_score=0.5) == "explicit_with_summary"


def test_classify_tier_upgrades_for_new_merchant():
    assert classify_gate(Decimal("25"), is_first_purchase_from_merchant=True) == "explicit"


# ─── Orchestrator HITL gate dispatch ────────────────────────────────────────


def test_purchase_gate_approve_runs_subagent(tool_ctx):
    m = tool_ctx.ap2.create_mandate(
        "user_1",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        payment_method_id="pm_test_secret",
    )
    # Orchestrator: makes ONE tool call (call_purchase_agent), then final reply
    # Purchase subagent: returns completed status with one text response
    orch_client = FakeAnthropicClient(
        [
            # Orchestrator turn 1: requests the purchase subagent
            tool_use_response(
                (
                    "call_purchase_agent",
                    {
                        "brief": "buy this",
                        "merchant_domain": "demo-shop.myshopify.com",
                        "items": [
                            {
                                "product_id": "shop_001",
                                "name": "Shoes",
                                "price": "100",
                                "quantity": 1,
                            }
                        ],
                    },
                )
            ),
            # PurchaseAgent runs inside the tool dispatch and consumes one response
            text_response('{"order": null, "status": "completed"}'),
            # Orchestrator turn 2: now produces final reply
            text_response("Done — your order is confirmed."),
        ]
    )
    confirm = AutoConfirmProvider(soft=True, explicit=True)
    orchestrator = OrchestratorAgent(
        orch_client,
        confirmation=confirm,
        mandate_id=m.mandate_id,
    )
    result = asyncio.get_event_loop().run_until_complete(
        orchestrator.run(tool_ctx, "Buy the demo shoes")
    )
    assert result["reply"] == "Done — your order is confirmed."
    # Gate was triggered with explicit tier (amount=100, new merchant)
    assert len(confirm.gates_seen) == 1
    tier, gate = confirm.gates_seen[0]
    assert tier == "explicit"
    assert gate.amount == Decimal("100")
    assert gate.merchant_domain == "demo-shop.myshopify.com"


def test_purchase_gate_deny_returns_cancelled_without_running_subagent(tool_ctx):
    m = tool_ctx.ap2.create_mandate(
        "user_1",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        payment_method_id="pm_test_secret",
    )
    orch_client = FakeAnthropicClient(
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
                                "price": "100",
                                "quantity": 1,
                            }
                        ],
                    },
                )
            ),
            text_response("Cancelled."),
            # NO purchase subagent response queued — if it runs, we'll error
        ]
    )
    confirm = AutoConfirmProvider(soft=False, explicit=False)
    orchestrator = OrchestratorAgent(orch_client, confirmation=confirm, mandate_id=m.mandate_id)
    result = asyncio.get_event_loop().run_until_complete(orchestrator.run(tool_ctx, "Buy shoes"))
    assert result["reply"] == "Cancelled."
    # Subagent was NOT invoked → exactly 2 calls to the client (orchestrator only)
    assert len(orch_client.calls) == 2
    # Cancellation audit row written
    actions = [r["action"] for r in tool_ctx.db.audit_log.all() if r.get("tool") == "hitl_gate"]
    assert any("cancelled" in a for a in actions)


def test_no_mandate_id_blocks_purchase(tool_ctx):
    orch_client = FakeAnthropicClient(
        [
            tool_use_response(
                (
                    "call_purchase_agent",
                    {
                        "brief": "buy",
                        "merchant_domain": "demo-shop.myshopify.com",
                        "product_id": "shop_001",
                        "name": "Shoes",
                        "price": "50",
                        "amount": "50",
                    },
                )
            ),
            text_response("No mandate; can't buy."),
        ]
    )
    confirm = AutoConfirmProvider()
    orchestrator = OrchestratorAgent(orch_client, confirmation=confirm, mandate_id=None)
    asyncio.get_event_loop().run_until_complete(orchestrator.run(tool_ctx, "buy"))
    # Gate was never reached — no mandate is a hard short-circuit
    assert confirm.gates_seen == []


def test_streaming_callbacks_fire_for_subagent_calls(tool_ctx):
    m = tool_ctx.ap2.create_mandate(
        "user_1",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        payment_method_id="pm_test_secret",
    )
    starts: list[str] = []
    ends: list[str] = []
    gates: list[str] = []

    async def on_start(name, args):
        starts.append(name)

    async def on_end(name, result):
        ends.append(name)

    async def on_gate(tier, data):
        gates.append(tier)

    cb = StreamingCallbacks(on_tool_start=on_start, on_tool_end=on_end, on_gate=on_gate)

    orch_client = FakeAnthropicClient(
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
                                "price": "100",
                                "quantity": 1,
                            }
                        ],
                    },
                )
            ),
            text_response('{"order": null, "status": "completed"}'),
            text_response("done"),
        ]
    )
    orchestrator = OrchestratorAgent(orch_client, callbacks=cb, mandate_id=m.mandate_id)
    asyncio.get_event_loop().run_until_complete(orchestrator.run(tool_ctx, "buy"))
    assert starts == ["call_purchase_agent"]
    assert ends == ["call_purchase_agent"]
    assert gates == ["explicit"]


def test_conversation_persists_across_runs(tool_ctx):
    """Two consecutive run() calls share history.

    First message → orchestrator replies. Second message → the call to Claude
    must include BOTH user turns plus the prior assistant turn, so the model
    can refer back to context from earlier in the conversation.
    """
    orch_client = FakeAnthropicClient(
        [
            text_response("Found one shoe: Demo Running Shoes $129.99."),
            text_response("Yes that's the one you saw, $129.99."),
        ]
    )
    orchestrator = OrchestratorAgent(orch_client, mandate_id=None)
    loop = asyncio.get_event_loop()

    loop.run_until_complete(orchestrator.run(tool_ctx, "find running shoes"))
    loop.run_until_complete(orchestrator.run(tool_ctx, "what was the price?"))

    # Inspect the second call: messages should contain both user turns AND
    # the assistant's first reply (otherwise Claude has no memory of "the shoe")
    second_call_msgs = orch_client.calls[1].messages
    user_turns = [m for m in second_call_msgs if m["role"] == "user"]
    assistant_turns = [m for m in second_call_msgs if m["role"] == "assistant"]
    assert len(user_turns) == 2
    assert len(assistant_turns) == 1
    # The session's persistent conversation was used
    assert len(tool_ctx.session.conversation) >= 3


def test_history_cap_prevents_unbounded_growth(tool_ctx):
    """After MAX_HISTORY_ENTRIES, oldest turns get dropped."""
    # Queue many text-response turns
    orch_client = FakeAnthropicClient([text_response(f"reply {i}") for i in range(25)])
    orchestrator = OrchestratorAgent(orch_client, mandate_id=None)
    # Shrink the cap so the test is fast and obvious
    orchestrator.MAX_HISTORY_ENTRIES = 6

    loop = asyncio.get_event_loop()
    for i in range(25):
        loop.run_until_complete(orchestrator.run(tool_ctx, f"turn {i}"))

    # History should have been trimmed
    assert len(tool_ctx.session.conversation) <= 6
