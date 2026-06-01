"""Gate question-loop tests (single-item AND multi-item).

The user asks a question at the CONFIRM prompt → orchestrator answers →
re-presents the gate → user confirms → purchase proceeds. The basket is
preserved across the Q&A cycle.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from agents.orchestrator import OrchestratorAgent
from cli.confirmation import AutoConfirmProvider, GateResponse
from tests.fake_anthropic import (
    FakeAnthropicClient,
    text_response,
    tool_use_response,
)


def _mandate(ctx, **kw):
    defaults = dict(
        user_id="user_1",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        payment_method_id="pm_test_secret",
    )
    defaults.update(kw)
    return ctx.ap2.create_mandate(**defaults)


# ── 1. Single-item: question at gate → answer → re-present → confirm ────────


def test_gate_question_loops_back_to_gate__single_item(tool_ctx):
    m = _mandate(tool_ctx)
    # Scripted user behaviour at the gate:
    #   1st prompt: asks a question
    #   2nd prompt: types CONFIRM
    confirm = AutoConfirmProvider(
        scripted=[
            GateResponse(decision="question", text="why this item?"),
            GateResponse(decision="confirm"),
        ]
    )
    client = FakeAnthropicClient(
        [
            # Orchestrator's first turn: call purchase agent (gate fires)
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
            # The gate prompt at attempt #1 yields a question → orchestrator
            # calls _answer_question_at_gate (one Claude turn) → re-prompt
            text_response("It's the only running shoe currently in stock."),
            # Gate fires again; user CONFIRMs; PurchaseAgent runs (one final-text)
            text_response('{"order": null, "status": "completed"}'),
            # Orchestrator's final reply
            text_response("Done."),
        ]
    )
    orchestrator = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)

    asyncio.get_event_loop().run_until_complete(orchestrator.run(tool_ctx, "buy shoes"))
    # Two gate prompts happened (1st question, 2nd confirm)
    assert len(confirm.gates_seen) == 2
    # Audit captured both the question and the eventual approval
    audit_actions = [
        r["action"] for r in tool_ctx.db.audit_log.all() if r.get("tool") == "hitl_gate"
    ]
    # gate_input covers questions, basket edits, and refusals
    assert any("gate_input" in a for a in audit_actions)
    assert any("approved" in a for a in audit_actions)


# ── 2. Multi-item: same flow with a 3-item basket ───────────────────────────


def test_gate_question_loops_back_to_gate__multi_item(tool_ctx):
    m = _mandate(tool_ctx)
    confirm = AutoConfirmProvider(
        scripted=[
            GateResponse(decision="question", text="why these three?"),
            GateResponse(decision="confirm"),
        ]
    )
    items = [
        {"product_id": "p1", "name": "Mug", "price": "14", "quantity": 1},
        {"product_id": "p2", "name": "Tumbler", "price": "28", "quantity": 1},
        {"product_id": "p3", "name": "Beans", "price": "18", "quantity": 1},
    ]
    client = FakeAnthropicClient(
        [
            tool_use_response(
                (
                    "call_purchase_agent",
                    {
                        "brief": "buy basket",
                        "merchant_domain": "demo-shop.myshopify.com",
                        "items": items,
                    },
                )
            ),
            text_response("All three were the top in-stock matches per your query."),
            text_response('{"order": null, "status": "completed"}'),
            text_response("Done."),
        ]
    )
    orchestrator = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
    asyncio.get_event_loop().run_until_complete(orchestrator.run(tool_ctx, "buy basket"))

    # Both gate prompts saw the SAME basket (3 items, $60 total)
    for _, gate in confirm.gates_seen:
        assert gate.amount == Decimal("60")
        assert len(gate.items) == 3


# ── 3. Single-item: basket survives across Q&A ──────────────────────────────


def test_gate_question_does_not_lose_basket__single_item(tool_ctx):
    m = _mandate(tool_ctx)
    confirm = AutoConfirmProvider(
        scripted=[
            GateResponse(decision="question", text="anything else available?"),
            GateResponse(decision="question", text="what's the rating?"),
            GateResponse(decision="confirm"),
        ]
    )
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
                                "price": "100",
                                "quantity": 1,
                            }
                        ],
                    },
                )
            ),
            text_response("That's the only in-stock option."),
            text_response("Rating is 4.5 stars."),
            text_response('{"order": null, "status": "completed"}'),
            text_response("Done."),
        ]
    )
    orchestrator = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
    asyncio.get_event_loop().run_until_complete(orchestrator.run(tool_ctx, "buy"))
    # 3 gate prompts (2 questions + 1 confirm). Every gate had the same item.
    assert len(confirm.gates_seen) == 3
    for _, gate in confirm.gates_seen:
        assert len(gate.items) == 1
        assert gate.items[0]["product_id"] == "shop_001"


# ── 4. Multi-item: basket survives across Q&A ───────────────────────────────


def test_gate_question_does_not_lose_basket__multi_item(tool_ctx):
    m = _mandate(tool_ctx)
    confirm = AutoConfirmProvider(
        scripted=[
            GateResponse(decision="question", text="why these three?"),
            GateResponse(decision="question", text="any cheaper alternatives?"),
            GateResponse(decision="confirm"),
        ]
    )
    items = [
        {"product_id": "p1", "name": "Mug", "price": "14", "quantity": 1},
        {"product_id": "p2", "name": "Tumbler", "price": "28", "quantity": 1},
        {"product_id": "p3", "name": "Beans", "price": "18", "quantity": 1},
    ]
    client = FakeAnthropicClient(
        [
            tool_use_response(
                (
                    "call_purchase_agent",
                    {
                        "brief": "buy",
                        "merchant_domain": "demo-shop.myshopify.com",
                        "items": items,
                    },
                )
            ),
            text_response("Top in-stock matches."),
            text_response("None cheaper that match your criteria."),
            text_response('{"order": null, "status": "completed"}'),
            text_response("Done."),
        ]
    )
    orchestrator = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
    asyncio.get_event_loop().run_until_complete(orchestrator.run(tool_ctx, "buy"))
    assert len(confirm.gates_seen) == 3
    for _, gate in confirm.gates_seen:
        assert len(gate.items) == 3
        assert gate.amount == Decimal("60")


# ── 5. Empty input still cancels at explicit gate ──────────────────────────


def test_gate_empty_input_still_cancels(tool_ctx):
    m = _mandate(tool_ctx)
    confirm = AutoConfirmProvider(
        scripted=[
            GateResponse(decision="cancel"),
        ]
    )
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
                                "product_id": "p1",
                                "name": "X",
                                "price": "100",
                                "quantity": 1,
                            }
                        ],
                    },
                )
            ),
            text_response("Cancelled."),
        ]
    )
    orchestrator = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
    asyncio.get_event_loop().run_until_complete(orchestrator.run(tool_ctx, "buy"))
    # No spend recorded
    assert tool_ctx.db.spend_records.all() == []


# ── 6. Cancel words still cancel (regression) ──────────────────────────────


def test_gate_cancel_words_still_cancel(tool_ctx):
    """Verifies the RichConfirmProvider classifies cancel-tokens correctly.
    Tested via the classifier itself rather than the orchestrator (we already
    have orchestrator-level cancellation coverage)."""
    from cli.display import RichConfirmProvider

    for word in ("no", "cancel", "stop", "abort", ""):
        result = RichConfirmProvider._classify(word, soft=False)
        assert result.decision == "cancel", f"{word!r} should cancel"


# ── 7. Max question loop bounds — prevents infinite loop ────────────────────


def test_gate_question_loop_bounded_by_max_questions(tool_ctx):
    """If user keeps asking questions forever, orchestrator gives up and
    treats it as a cancel after MAX_GATE_QUESTIONS attempts."""
    m = _mandate(tool_ctx)
    confirm = AutoConfirmProvider(
        scripted=[GateResponse(decision="question", text=f"q{i}") for i in range(20)]
    )
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
                                "product_id": "p1",
                                "name": "X",
                                "price": "10",
                                "quantity": 1,
                            }
                        ],
                    },
                )
            )
        ]
        + [text_response(f"answer{i}") for i in range(15)]
        + [text_response("Gave up.")]
    )
    orchestrator = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
    orchestrator.MAX_GATE_QUESTIONS = 3  # shrink for fast test
    asyncio.get_event_loop().run_until_complete(orchestrator.run(tool_ctx, "buy"))
    # After MAX_GATE_QUESTIONS questions (+1 final attempt = 4 prompts), cancels
    assert len(confirm.gates_seen) <= 4
    assert tool_ctx.db.spend_records.all() == []
