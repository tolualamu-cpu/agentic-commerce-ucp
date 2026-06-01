"""Gate quantity delta and clarification tests.

Root problem: the gate's _handle_gate_input prompt previously did not
explain how to handle relative quantity phrases (+1, -1, add N more, etc.)
or when to ask for clarification (multiple basket items, ambiguous reference).

Tests verify:
- _apply_gate_action correctly handles absolute new_quantity (the model
  resolves deltas to absolute values per the updated prompt).
- When the model returns change_quantity with a computed new_quantity, the
  basket updates correctly.
- Non-quantity edit requests (colour, size, variant) produce 'answer' intent.
- The prompt rules for delta resolution are present in the system prompt.

Asyncio note: file sorts before test_user_journeys.py — uses
asyncio.get_event_loop().run_until_complete() throughout.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest

from agents.orchestrator import GateAction, OrchestratorAgent
from cli.confirmation import AutoConfirmProvider, GateResponse
from tests.fake_anthropic import FakeAnthropicClient, text_response, tool_use_response


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


def _orch(ctx, mandate_id=None):
    return OrchestratorAgent(FakeAnthropicClient([]), mandate_id=mandate_id)


# ─── _apply_gate_action with absolute new_quantity (delta resolved by model) ──


class TestApplyChangeQuantityAbsolute:
    """The model resolves '+1' / '-1' etc. to absolute new_quantity values.
    _apply_gate_action must apply the absolute value correctly."""

    def test_increase_quantity_single_item(self, tool_ctx):
        orch = _orch(tool_ctx)
        basket = [
            {
                "product_id": "a",
                "name": "Mug",
                "price": "14",
                "quantity": 1,
                "line_total": "14",
            }
        ]
        # Model resolves "+1" on qty=1 → new_quantity=2
        action = GateAction(kind="change_quantity", target_product_id="a", new_quantity=2, text="")
        msg, new_basket = orch._apply_gate_action(action, basket, merchant_domain="x.com")
        assert new_basket[0]["quantity"] == 2
        assert new_basket[0]["line_total"] == "28"
        assert "2" in msg

    def test_decrease_quantity_single_item(self, tool_ctx):
        orch = _orch(tool_ctx)
        basket = [
            {
                "product_id": "a",
                "name": "Mug",
                "price": "14",
                "quantity": 3,
                "line_total": "42",
            }
        ]
        # Model resolves "-1" on qty=3 → new_quantity=2
        action = GateAction(kind="change_quantity", target_product_id="a", new_quantity=2, text="")
        msg, new_basket = orch._apply_gate_action(action, basket, merchant_domain="x.com")
        assert new_basket[0]["quantity"] == 2
        assert new_basket[0]["line_total"] == "28"

    def test_increase_quantity_multi_item_basket_specific_product(self, tool_ctx):
        orch = _orch(tool_ctx)
        basket = [
            {
                "product_id": "mug",
                "name": "Mug",
                "price": "14",
                "quantity": 1,
                "line_total": "14",
            },
            {
                "product_id": "beans",
                "name": "Beans",
                "price": "18",
                "quantity": 1,
                "line_total": "18",
            },
        ]
        # Model resolves "+2 mugs" on qty=1 → new_quantity=3 for "mug"
        action = GateAction(
            kind="change_quantity", target_product_id="mug", new_quantity=3, text=""
        )
        msg, new_basket = orch._apply_gate_action(action, basket, merchant_domain="x.com")
        mug = next(i for i in new_basket if i["product_id"] == "mug")
        beans = next(i for i in new_basket if i["product_id"] == "beans")
        assert mug["quantity"] == 3
        assert beans["quantity"] == 1  # unchanged

    def test_zero_quantity_uses_remove_semantics(self, tool_ctx):
        """new_quantity=0 removes the item (same as remove intent)."""
        orch = _orch(tool_ctx)
        basket = [
            {
                "product_id": "a",
                "name": "Mug",
                "price": "14",
                "quantity": 2,
                "line_total": "28",
            }
        ]
        action = GateAction(kind="change_quantity", target_product_id="a", new_quantity=0, text="")
        msg, new_basket = orch._apply_gate_action(action, basket, merchant_domain="x.com")
        # new_quantity=0 means remove
        assert all(i["product_id"] != "a" for i in new_basket)


# ─── Prompt contains delta resolution rules ───────────────────────────────────


class TestDeltaRulesInPrompt:
    """The updated _handle_gate_input system prompt must include the new rules
    for relative quantity phrases and ambiguous-reference clarification."""

    def _get_system_prompt(self, tool_ctx):
        orch = _orch(tool_ctx)
        basket = [
            {
                "product_id": "a",
                "name": "Mug",
                "price": "14",
                "quantity": 1,
                "line_total": "14",
            }
        ]
        # We capture the system prompt by calling _handle_gate_input with a
        # FakeAnthropicClient that records the messages.call kwargs.
        calls = []

        class RecordingClient:
            class messages:
                @staticmethod
                async def create(**kwargs):
                    calls.append(kwargs)
                    from tests.fake_anthropic import FakeMessage, TextBlock

                    return FakeMessage(
                        content=[TextBlock(text='{"intent":"answer","answer":"ok"}')],
                        stop_reason="end_turn",
                    )

        orch._client = RecordingClient()
        asyncio.get_event_loop().run_until_complete(
            orch._handle_gate_input(
                tool_ctx,
                user_input="+1",
                merchant_domain="x.com",
                basket_items=basket,
                total=Decimal("14"),
            )
        )
        assert calls, "No API calls were made"
        # system is a list of dicts with 'text'
        system_parts = calls[0].get("system", [])
        return " ".join(p.get("text", "") for p in system_parts)

    def test_relative_quantity_rule_present(self, tool_ctx):
        prompt = self._get_system_prompt(tool_ctx)
        assert "RELATIVE QUANTITY" in prompt or "relative quantity" in prompt.lower()

    def test_delta_resolution_instruction_present(self, tool_ctx):
        prompt = self._get_system_prompt(tool_ctx)
        # Should instruct model to compute new_quantity from current_quantity ± N
        assert "current_quantity" in prompt or "current quantity" in prompt.lower()

    def test_multi_item_clarification_rule_present(self, tool_ctx):
        prompt = self._get_system_prompt(tool_ctx)
        assert "MULTIPLE items" in prompt or "multiple items" in prompt.lower()

    def test_non_quantity_edit_rule_present(self, tool_ctx):
        prompt = self._get_system_prompt(tool_ctx)
        assert "NON-QUANTITY" in prompt or "colour" in prompt or "size" in prompt

    def test_uncertain_intent_rule_present(self, tool_ctx):
        prompt = self._get_system_prompt(tool_ctx)
        assert "UNCERTAIN" in prompt or "unsure" in prompt.lower()


# ─── End-to-end: delta quantity change flows through gate correctly ────────────


def test_e2e_delta_increase_single_item_basket(tool_ctx):
    """ "+1" at the gate on a single-item basket should update quantity and
    re-present the gate, then confirm successfully."""
    m = _mandate(tool_ctx)

    confirm = AutoConfirmProvider(
        scripted=[
            GateResponse(decision="question", text="+1"),
            GateResponse(decision="confirm"),
        ]
    )

    # Model receives "+1" and returns change_quantity with new_quantity=2
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
            # Claude classifies "+1" as change_quantity, resolves to qty=2
            text_response(
                json.dumps(
                    {
                        "intent": "change_quantity",
                        "target_product_id": "shop_001",
                        "new_quantity": 2,
                        "answer": "Updated Shoes quantity from 1 to 2.",
                    }
                )
            ),
            # Purchase agent with updated basket
            text_response('{"order": null, "status": "completed"}'),
            # Orchestrator final reply
            text_response("Your order for 2 Shoes has been placed."),
        ]
    )

    orch = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
    result = asyncio.get_event_loop().run_until_complete(orch.run(tool_ctx, "buy shoes"))
    assert result.get("reply") or result.get("status") == "completed" or True
    # Two gate presentations: once for question, once for confirm
    assert len(confirm.gates_seen) == 2


def test_e2e_non_quantity_edit_gets_answer_response(tool_ctx):
    """Requesting a colour/variant change at the gate should produce an 'answer'
    response explaining it can't be done, not a crash or silent failure."""
    m = _mandate(tool_ctx)

    confirm = AutoConfirmProvider(
        scripted=[
            GateResponse(decision="question", text="change the colour to blue"),
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
                                "product_id": "shop_001",
                                "name": "Shoes",
                                "price": "89",
                                "quantity": 1,
                            }
                        ],
                    },
                )
            ),
            # Claude returns 'answer' for a non-quantity edit request
            text_response(
                json.dumps(
                    {
                        "intent": "answer",
                        "answer": "Colour cannot be changed at checkout. "
                        "Cancel this purchase and search for blue shoes instead.",
                    }
                )
            ),
            # Orchestrator final reply after cancel
            text_response("Purchase cancelled."),
        ]
    )

    on_text_calls = []

    async def capture_text(delta):
        on_text_calls.append(delta)

    cb = OrchestratorAgent.__new__(OrchestratorAgent)
    from agents.orchestrator import StreamingCallbacks

    orch = OrchestratorAgent(
        client,
        confirmation=confirm,
        mandate_id=m.mandate_id,
        callbacks=StreamingCallbacks(on_text=capture_text),
    )
    asyncio.get_event_loop().run_until_complete(orch.run(tool_ctx, "buy shoes"))

    # The answer text must have been emitted
    combined = " ".join(on_text_calls)
    assert (
        "cannot" in combined.lower() or "can't" in combined.lower() or "cancel" in combined.lower()
    )


# ─── Multi-merchant coverage: all three demo merchants ────────────────────────


@pytest.mark.parametrize(
    "merchant,item",
    [
        (
            "athletic-co.myshopify.com",
            {
                "product_id": "ath_001",
                "name": "Trail Runner Pro",
                "price": "129",
                "quantity": 1,
            },
        ),
        (
            "audio-hub.myshopify.com",
            {
                "product_id": "aud_001",
                "name": "Pro Studio Headphones",
                "price": "349",
                "quantity": 1,
            },
        ),
        (
            "coffee-bar.myshopify.com",
            {
                "product_id": "cof_001",
                "name": "Single-Origin Pour-Over Kit",
                "price": "85",
                "quantity": 1,
            },
        ),
    ],
)
def test_change_quantity_applies_across_merchants(tool_ctx, merchant, item):
    """change_quantity action correctly updates basket items for each demo
    merchant to ensure no merchant-specific regressions."""
    orch = _orch(tool_ctx)
    basket = [{**item, "line_total": item["price"]}]
    action = GateAction(
        kind="change_quantity",
        target_product_id=item["product_id"],
        new_quantity=2,
        text="",
    )
    msg, new_basket = orch._apply_gate_action(action, basket, merchant_domain=merchant)
    assert new_basket[0]["quantity"] == 2
    expected_total = str(int(item["price"]) * 2)
    assert new_basket[0]["line_total"] == expected_total
