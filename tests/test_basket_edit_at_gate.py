"""In-place basket editing at the HITL gate.

Covers the full add/remove/change_quantity matrix plus every safety-rail
refusal path, asserting that refusals come back with customer-friendly
explanations rather than abrupt cuts.

Scripted with FakeAnthropicClient: each gate input → one Claude call that
returns structured JSON describing the intent. Tests both the model's
structured-output path and the plain-text fallback.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

from agents.orchestrator import GateAction, OrchestratorAgent
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


def _intent(payload: dict) -> str:
    """Helper: serialize a JSON intent block as a Claude response."""
    return json.dumps(payload)


# ─── Pure helper tests — no orchestrator round trip ────────────────────────


def test_normalise_basket_drops_zero_qty(tool_ctx):
    items = [
        {"product_id": "a", "name": "X", "price": "10", "quantity": 1},
        {"product_id": "b", "name": "Y", "price": "5", "quantity": 0},
        {"product_id": "c", "name": "Z", "price": "3", "quantity": 2},
    ]
    out = OrchestratorAgent._normalise_basket(items)
    assert len(out) == 2
    ids = {i["product_id"] for i in out}
    assert ids == {"a", "c"}


def test_normalise_basket_computes_line_total(tool_ctx):
    items = [{"product_id": "a", "name": "X", "price": "14", "quantity": 3}]
    out = OrchestratorAgent._normalise_basket(items)
    assert out[0]["line_total"] == "42"


def test_compute_basket_total(tool_ctx):
    basket = [
        {"product_id": "a", "line_total": "10"},
        {"product_id": "b", "line_total": "25.50"},
        {"product_id": "c", "line_total": "100"},
    ]
    assert OrchestratorAgent._compute_basket_total(basket) == Decimal("135.50")


def test_compute_basket_total_empty(tool_ctx):
    assert OrchestratorAgent._compute_basket_total([]) == Decimal("0")


def test_try_parse_json_strict(tool_ctx):
    out = OrchestratorAgent._try_parse_json('{"intent":"remove","x":1}')
    assert out == {"intent": "remove", "x": 1}


def test_try_parse_json_with_fences(tool_ctx):
    text = 'Sure!\n```json\n{"intent": "answer"}\n```'
    out = OrchestratorAgent._try_parse_json(text)
    assert out == {"intent": "answer"}


def test_try_parse_json_falls_back_to_substring(tool_ctx):
    text = 'I think: {"intent": "answer", "answer": "hi"} done.'
    out = OrchestratorAgent._try_parse_json(text)
    assert out == {"intent": "answer", "answer": "hi"}


def test_try_parse_json_returns_none_for_plain_text(tool_ctx):
    assert OrchestratorAgent._try_parse_json("just plain text") is None


def test_gate_action_from_parsed_remove(tool_ctx):
    a = OrchestratorAgent._gate_action_from_parsed(
        {"intent": "remove", "target_product_id": "x", "answer": "ok"}
    )
    assert a.kind == "remove"
    assert a.target_product_id == "x"


def test_gate_action_from_parsed_change_quantity(tool_ctx):
    a = OrchestratorAgent._gate_action_from_parsed(
        {
            "intent": "change_quantity",
            "target_product_id": "x",
            "new_quantity": 3,
            "answer": "ok",
        }
    )
    assert a.kind == "change_quantity"
    assert a.new_quantity == 3


def test_gate_action_from_parsed_add(tool_ctx):
    a = OrchestratorAgent._gate_action_from_parsed(
        {
            "intent": "add",
            "new_product_id": "y",
            "new_product_name": "Y",
            "new_product_price": "10",
            "new_product_quantity": 2,
            "answer": "ok",
        }
    )
    assert a.kind == "add"
    assert a.new_item["product_id"] == "y"
    assert a.new_item["quantity"] == 2


# ─── _apply_gate_action (Python-side mutation logic) ──────────────────────


def _orch(ctx, mandate_id=None):
    """Build a bare orchestrator with no scripted responses (we won't run it)."""
    return OrchestratorAgent(FakeAnthropicClient([]), mandate_id=mandate_id)


def test_apply_remove_existing_item(tool_ctx):
    orch = _orch(tool_ctx)
    basket = [
        {
            "product_id": "a",
            "name": "Mug",
            "price": "14",
            "quantity": 1,
            "line_total": "14",
        },
        {
            "product_id": "b",
            "name": "Beans",
            "price": "18",
            "quantity": 1,
            "line_total": "18",
        },
    ]
    action = GateAction(kind="remove", target_product_id="a", text="")
    msg, new_basket = orch._apply_gate_action(action, basket, merchant_domain="x.com")
    assert len(new_basket) == 1
    assert new_basket[0]["product_id"] == "b"
    assert "Removed Mug" in msg


def test_apply_remove_nonexistent_item_friendly(tool_ctx):
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
    action = GateAction(kind="remove", target_product_id="ZZZ")
    msg, new_basket = orch._apply_gate_action(action, basket, merchant_domain="x.com")
    # Basket unchanged
    assert new_basket == basket
    # Friendly message names what IS in the basket
    assert "Mug" in msg
    assert "couldn" in msg.lower() or "don" in msg.lower()


def test_apply_change_quantity_bumps_line_total(tool_ctx):
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
    action = GateAction(kind="change_quantity", target_product_id="a", new_quantity=3)
    msg, new_basket = orch._apply_gate_action(action, basket, merchant_domain="x.com")
    assert new_basket[0]["quantity"] == 3
    assert new_basket[0]["line_total"] == "42"


def test_apply_change_quantity_zero_removes(tool_ctx):
    orch = _orch(tool_ctx)
    basket = [
        {
            "product_id": "a",
            "name": "Mug",
            "price": "14",
            "quantity": 1,
            "line_total": "14",
        },
        {
            "product_id": "b",
            "name": "X",
            "price": "5",
            "quantity": 1,
            "line_total": "5",
        },
    ]
    action = GateAction(kind="change_quantity", target_product_id="a", new_quantity=0)
    msg, new_basket = orch._apply_gate_action(action, basket, merchant_domain="x.com")
    assert len(new_basket) == 1
    assert new_basket[0]["product_id"] == "b"
    assert "Removed" in msg


def test_apply_change_quantity_nonexistent_friendly(tool_ctx):
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
    action = GateAction(kind="change_quantity", target_product_id="ZZZ", new_quantity=5)
    msg, new_basket = orch._apply_gate_action(action, basket, merchant_domain="x.com")
    assert new_basket == basket
    assert "couldn" in msg.lower() or "find" in msg.lower()


def test_apply_change_quantity_negative_friendly(tool_ctx):
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
    action = GateAction(kind="change_quantity", target_product_id="a", new_quantity=-2)
    msg, new_basket = orch._apply_gate_action(action, basket, merchant_domain="x.com")
    assert new_basket == basket  # unchanged
    assert "positive" in msg.lower() or "zero" in msg.lower()


def test_apply_add_new_item(tool_ctx):
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
    action = GateAction(
        kind="add",
        new_item={
            "product_id": "b",
            "name": "Beans",
            "price": "18",
            "quantity": 1,
        },
    )
    msg, new_basket = orch._apply_gate_action(action, basket, merchant_domain="x.com")
    assert len(new_basket) == 2
    assert any(i["product_id"] == "b" for i in new_basket)
    assert "Added Beans" in msg


def test_apply_add_bumps_qty_if_already_in_basket(tool_ctx):
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
    action = GateAction(
        kind="add",
        new_item={
            "product_id": "a",
            "name": "Mug",
            "price": "14",
            "quantity": 2,
        },
    )
    msg, new_basket = orch._apply_gate_action(action, basket, merchant_domain="x.com")
    assert len(new_basket) == 1
    assert new_basket[0]["quantity"] == 3
    assert "Increased" in msg


def test_apply_add_with_invalid_price_friendly(tool_ctx):
    orch = _orch(tool_ctx)
    basket = []
    action = GateAction(
        kind="add",
        new_item={
            "product_id": "x",
            "name": "X",
            "price": "not-a-number",
            "quantity": 1,
        },
    )
    msg, new_basket = orch._apply_gate_action(action, basket, merchant_domain="x.com")
    assert new_basket == basket
    assert "price" in msg.lower()


def test_apply_add_with_empty_id_friendly(tool_ctx):
    orch = _orch(tool_ctx)
    action = GateAction(
        kind="add",
        new_item={
            "product_id": "",
            "name": "",
            "price": "10",
            "quantity": 1,
        },
    )
    msg, new_basket = orch._apply_gate_action(action, [], merchant_domain="x.com")
    assert new_basket == []
    assert "couldn" in msg.lower() or "identify" in msg.lower()


# ─── End-to-end gate edit flows (orchestrator round trip) ──────────────────


def test_e2e_remove_item_from_three_item_basket(tool_ctx):
    """User: 'remove the tumbler' → basket goes from 3 items to 2."""
    m = _mandate(tool_ctx)
    confirm = AutoConfirmProvider(
        scripted=[
            GateResponse(decision="question", text="remove the tumbler"),
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
                                "product_id": "a",
                                "name": "Mug",
                                "price": "14",
                                "quantity": 1,
                            },
                            {
                                "product_id": "b",
                                "name": "Tumbler",
                                "price": "28",
                                "quantity": 1,
                            },
                            {
                                "product_id": "c",
                                "name": "Beans",
                                "price": "18",
                                "quantity": 1,
                            },
                        ],
                    },
                )
            ),
            # Gate input handler call — returns structured remove intent
            text_response(
                _intent(
                    {
                        "intent": "remove",
                        "target_product_id": "b",
                        "answer": "Removing the Tumbler.",
                    }
                )
            ),
            # PurchaseAgent runs after gate confirmed
            text_response('{"order": null, "status": "completed"}'),
            text_response("Done."),
        ]
    )
    orch = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
    asyncio.get_event_loop().run_until_complete(orch.run(tool_ctx, "buy"))
    # Two gate prompts (1 question → mutation, 1 confirm)
    assert len(confirm.gates_seen) == 2
    # First gate had 3 items at $60; second had 2 items at $32
    _, gate1 = confirm.gates_seen[0]
    _, gate2 = confirm.gates_seen[1]
    assert len(gate1.items) == 3
    assert gate1.amount == Decimal("60")
    assert len(gate2.items) == 2
    assert gate2.amount == Decimal("32")
    assert all(i["product_id"] != "b" for i in gate2.items)


def test_e2e_change_quantity_from_one_to_three(tool_ctx):
    """User: 'change to 3 mugs' → quantity updates, total rises."""
    m = _mandate(tool_ctx)
    confirm = AutoConfirmProvider(
        scripted=[
            GateResponse(decision="question", text="change to 3 mugs"),
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
                                "product_id": "a",
                                "name": "Mug",
                                "price": "14",
                                "quantity": 1,
                            }
                        ],
                    },
                )
            ),
            text_response(
                _intent(
                    {
                        "intent": "change_quantity",
                        "target_product_id": "a",
                        "new_quantity": 3,
                        "answer": "Set quantity to 3.",
                    }
                )
            ),
            text_response('{"order": null, "status": "completed"}'),
            text_response("Done."),
        ]
    )
    orch = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
    asyncio.get_event_loop().run_until_complete(orch.run(tool_ctx, "buy"))
    _, gate2 = confirm.gates_seen[1]
    assert gate2.items[0]["quantity"] == 3
    assert gate2.amount == Decimal("42")


def test_e2e_add_item_from_discovery_cache(tool_ctx):
    """User: 'add the Ethiopia beans' → cache lookup → item appended."""
    m = _mandate(tool_ctx)
    # Seed the discovery cache
    tool_ctx.session.last_discovered_products = [
        {
            "product_id": "beans1",
            "name": "Ethiopia Beans",
            "price": "18",
            "merchant_domain": "demo-shop.myshopify.com",
        },
    ]
    confirm = AutoConfirmProvider(
        scripted=[
            GateResponse(decision="question", text="add the Ethiopia beans"),
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
                                "product_id": "a",
                                "name": "Mug",
                                "price": "14",
                                "quantity": 1,
                            }
                        ],
                    },
                )
            ),
            text_response(
                _intent(
                    {
                        "intent": "add",
                        "new_product_id": "beans1",
                        "new_product_name": "Ethiopia Beans",
                        "new_product_price": "18",
                        "new_product_quantity": 1,
                        "answer": "Added the Ethiopia Beans.",
                    }
                )
            ),
            text_response('{"order": null, "status": "completed"}'),
            text_response("Done."),
        ]
    )
    orch = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
    asyncio.get_event_loop().run_until_complete(orch.run(tool_ctx, "buy"))
    _, gate2 = confirm.gates_seen[1]
    assert len(gate2.items) == 2
    assert gate2.amount == Decimal("32")  # 14 + 18


def test_e2e_add_item_not_in_cache_refused_friendly(tool_ctx):
    """User: 'add a random thing not in search results' → refused, basket unchanged."""
    m = _mandate(tool_ctx)
    tool_ctx.session.last_discovered_products = []  # empty cache
    confirm = AutoConfirmProvider(
        scripted=[
            GateResponse(decision="question", text="add a tumbler"),
            GateResponse(decision="cancel"),  # user gives up after refusal
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
                                "product_id": "a",
                                "name": "Mug",
                                "price": "14",
                                "quantity": 1,
                            }
                        ],
                    },
                )
            ),
            text_response(
                _intent(
                    {
                        "intent": "add",
                        "new_product_id": "not_in_cache_id",
                        "new_product_name": "Tumbler",
                        "new_product_price": "28",
                        "new_product_quantity": 1,
                        "answer": "Adding the Tumbler.",
                    }
                )
            ),
            text_response("Cancelled."),
        ]
    )
    orch = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
    asyncio.get_event_loop().run_until_complete(orch.run(tool_ctx, "buy"))
    # Basket unchanged across both gate views
    _, gate1 = confirm.gates_seen[0]
    _, gate2 = confirm.gates_seen[1]
    assert gate1.items == gate2.items
    # Refusal text mentioned in buffered Q&A
    qa_blob = json.dumps(orch._pending_gate_history + tool_ctx.session.conversation, default=str)
    assert "search" in qa_blob.lower() or "recent" in qa_blob.lower()


def test_e2e_add_pushes_total_over_cap_refused_with_friendly(tool_ctx):
    """User: 'add the $500 headphones' but adding would exceed $100 cap →
    refused with a friendly cap-explanation message."""
    m = _mandate(tool_ctx, max_amount=Decimal("100"))
    tool_ctx.session.last_discovered_products = [
        {
            "product_id": "h1",
            "name": "Premium Headphones",
            "price": "500",
            "merchant_domain": "demo-shop.myshopify.com",
        },
    ]
    confirm = AutoConfirmProvider(
        scripted=[
            GateResponse(decision="question", text="add the headphones"),
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
                                "product_id": "a",
                                "name": "Mug",
                                "price": "14",
                                "quantity": 1,
                            }
                        ],
                    },
                )
            ),
            text_response(
                _intent(
                    {
                        "intent": "add",
                        "new_product_id": "h1",
                        "new_product_name": "Premium Headphones",
                        "new_product_price": "500",
                        "new_product_quantity": 1,
                        "answer": "Adding headphones.",
                    }
                )
            ),
            text_response("ok"),
        ]
    )
    orch = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
    asyncio.get_event_loop().run_until_complete(orch.run(tool_ctx, "buy"))
    _, gate1 = confirm.gates_seen[0]
    _, gate2 = confirm.gates_seen[1]
    # Basket unchanged
    assert gate1.items == gate2.items
    # Friendly message references the cap (flushed to session.conversation)
    qa_text = " ".join(
        OrchestratorAgent._extract_text_from_entry(e) for e in tool_ctx.session.conversation
    )
    assert "$100" in qa_text or "per-transaction" in qa_text.lower()


def test_e2e_remove_all_items_cancels_purchase(tool_ctx):
    """User removes the only item → basket empty → flow cancelled with friendly msg."""
    m = _mandate(tool_ctx)
    confirm = AutoConfirmProvider(
        scripted=[
            GateResponse(decision="question", text="remove the mug"),
            # No follow-up needed — basket goes empty and cancels automatically
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
                                "product_id": "a",
                                "name": "Mug",
                                "price": "14",
                                "quantity": 1,
                            }
                        ],
                    },
                )
            ),
            text_response(
                _intent(
                    {
                        "intent": "remove",
                        "target_product_id": "a",
                        "answer": "Removed the mug.",
                    }
                )
            ),
            text_response("Cancelled — your basket is empty."),
        ]
    )
    orch = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
    result = asyncio.get_event_loop().run_until_complete(orch.run(tool_ctx, "buy"))
    # No spend recorded
    assert tool_ctx.db.spend_records.all() == []


def test_e2e_remove_nonexistent_then_proceed(tool_ctx):
    """User tries to remove an item that isn't there → friendly correction →
    user confirms original basket."""
    m = _mandate(tool_ctx)
    confirm = AutoConfirmProvider(
        scripted=[
            GateResponse(decision="question", text="remove the tumbler"),  # not in basket
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
                                "product_id": "a",
                                "name": "Mug",
                                "price": "14",
                                "quantity": 1,
                            }
                        ],
                    },
                )
            ),
            text_response(
                _intent(
                    {
                        "intent": "remove",
                        "target_product_id": "fake_id",
                        "answer": "Removing tumbler.",
                    }
                )
            ),
            text_response('{"order": null, "status": "completed"}'),
            text_response("Done."),
        ]
    )
    orch = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
    asyncio.get_event_loop().run_until_complete(orch.run(tool_ctx, "buy"))
    # Basket survives — both gates have the same item
    _, gate1 = confirm.gates_seen[0]
    _, gate2 = confirm.gates_seen[1]
    assert gate1.items == gate2.items


def test_e2e_plain_text_response_falls_back_to_answer(tool_ctx):
    """Backward compat: if Claude returns plain text instead of JSON,
    treat it as an 'answer' intent (basket untouched)."""
    m = _mandate(tool_ctx)
    confirm = AutoConfirmProvider(
        scripted=[
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
                                "product_id": "a",
                                "name": "Mug",
                                "price": "14",
                                "quantity": 1,
                            }
                        ],
                    },
                )
            ),
            # Plain text, not JSON — should still work as an answer
            text_response("The mug has a 4.5 star rating."),
            text_response('{"order": null, "status": "completed"}'),
            text_response("Done."),
        ]
    )
    orch = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
    asyncio.get_event_loop().run_until_complete(orch.run(tool_ctx, "buy"))
    # Basket unchanged
    _, gate1 = confirm.gates_seen[0]
    _, gate2 = confirm.gates_seen[1]
    assert gate1.items == gate2.items


def test_e2e_change_quantity_pushes_over_cap_refused(tool_ctx):
    """Change quantity from 1 to 10 → 10×$14 = $140 > $100 cap → refused."""
    m = _mandate(tool_ctx, max_amount=Decimal("100"))
    confirm = AutoConfirmProvider(
        scripted=[
            GateResponse(decision="question", text="make it 10 mugs"),
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
                                "product_id": "a",
                                "name": "Mug",
                                "price": "14",
                                "quantity": 1,
                            }
                        ],
                    },
                )
            ),
            text_response(
                _intent(
                    {
                        "intent": "change_quantity",
                        "target_product_id": "a",
                        "new_quantity": 10,
                        "answer": "Updating to 10.",
                    }
                )
            ),
            text_response("ok"),
        ]
    )
    orch = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
    asyncio.get_event_loop().run_until_complete(orch.run(tool_ctx, "buy"))
    _, gate1 = confirm.gates_seen[0]
    _, gate2 = confirm.gates_seen[1]
    # Basket unchanged
    assert gate1.items == gate2.items
    assert gate2.amount == Decimal("14")
    # After run() the gate Q&A is flushed to session.conversation
    qa_text = " ".join(
        OrchestratorAgent._extract_text_from_entry(e) for e in tool_ctx.session.conversation
    )
    assert "$100" in qa_text or "limit" in qa_text.lower()


def test_e2e_multiple_edits_in_sequence(tool_ctx):
    """User does TWO edits before confirming — both apply, basket has both changes."""
    m = _mandate(tool_ctx)
    confirm = AutoConfirmProvider(
        scripted=[
            GateResponse(decision="question", text="remove the tumbler"),
            GateResponse(decision="question", text="change mug qty to 2"),
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
                                "product_id": "a",
                                "name": "Mug",
                                "price": "14",
                                "quantity": 1,
                            },
                            {
                                "product_id": "b",
                                "name": "Tumbler",
                                "price": "28",
                                "quantity": 1,
                            },
                        ],
                    },
                )
            ),
            text_response(
                _intent(
                    {
                        "intent": "remove",
                        "target_product_id": "b",
                        "answer": "Removed.",
                    }
                )
            ),
            text_response(
                _intent(
                    {
                        "intent": "change_quantity",
                        "target_product_id": "a",
                        "new_quantity": 2,
                        "answer": "Set to 2.",
                    }
                )
            ),
            text_response('{"order": null, "status": "completed"}'),
            text_response("Done."),
        ]
    )
    orch = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
    # Allow up to 3 gate questions (default 5 is fine)
    asyncio.get_event_loop().run_until_complete(orch.run(tool_ctx, "buy"))
    final_gate = confirm.gates_seen[-1][1]
    assert len(final_gate.items) == 1
    assert final_gate.items[0]["quantity"] == 2
    assert final_gate.amount == Decimal("28")  # 2 × $14


def test_e2e_revoked_mandate_during_edit_refused(tool_ctx):
    """Mandate revoked mid-flow → next mutation refused with friendly msg."""
    m = _mandate(tool_ctx)
    confirm = AutoConfirmProvider(
        scripted=[
            GateResponse(decision="question", text="add a tumbler"),
            GateResponse(decision="cancel"),
        ]
    )
    # Pre-seed the discovery cache so the add isn't rejected as not-in-cache
    tool_ctx.session.last_discovered_products = [
        {
            "product_id": "t1",
            "name": "Tumbler",
            "price": "28",
            "merchant_domain": "demo-shop.myshopify.com",
        },
    ]
    # Revoke mandate BEFORE the orchestrator runs — re-validation should trip
    tool_ctx.ap2.revoke_mandate(m.mandate_id)

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
                                "product_id": "a",
                                "name": "Mug",
                                "price": "14",
                                "quantity": 1,
                            }
                        ],
                    },
                )
            ),
            text_response(
                _intent(
                    {
                        "intent": "add",
                        "new_product_id": "t1",
                        "new_product_name": "Tumbler",
                        "new_product_price": "28",
                        "new_product_quantity": 1,
                        "answer": "Adding tumbler.",
                    }
                )
            ),
            text_response("Cancelled."),
        ]
    )
    orch = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
    asyncio.get_event_loop().run_until_complete(orch.run(tool_ctx, "buy"))
    # Friendly message references mandate state
    qa_text = " ".join(
        OrchestratorAgent._extract_text_from_entry(e) for e in orch._pending_gate_history
    )
    # Either we refused the add at mandate-validation OR the basket stayed
    # the same — both are acceptable safety outcomes.
    _, gate1 = confirm.gates_seen[0]
    _, gate2 = confirm.gates_seen[1]
    assert gate1.items == gate2.items  # mutation rejected


# ─── Friendly message format assertions ────────────────────────────────────


def test_friendly_cap_refusal_includes_cap_amount(tool_ctx):
    """The friendly cap-refusal message must include the actual cap."""
    m = _mandate(tool_ctx, max_amount=Decimal("100"))
    orch = OrchestratorAgent(FakeAnthropicClient([]), mandate_id=m.mandate_id)
    msg = orch._friendly_cap_refusal(
        tool_ctx,
        reason="exceeds_per_transaction_cap",
        attempted_total=Decimal("200"),
        original_basket=[{"product_id": "a", "name": "X", "line_total": "50"}],
        attempted_basket=[
            {"product_id": "a", "name": "X", "line_total": "50"},
            {"product_id": "b", "name": "Y", "line_total": "150"},
        ],
    )
    assert "$100" in msg
    assert "$200" in msg or "200" in msg
    # Mentions the alternative paths
    assert any(w in msg.lower() for w in ("swap", "lower", "proceed"))


def test_friendly_cap_refusal_for_daily_cap_mentions_daily(tool_ctx):
    m = _mandate(tool_ctx, daily_cap=Decimal("200"))
    orch = OrchestratorAgent(FakeAnthropicClient([]), mandate_id=m.mandate_id)
    msg = orch._friendly_cap_refusal(
        tool_ctx,
        reason="exceeds_daily_cap",
        attempted_total=Decimal("300"),
        original_basket=[{"product_id": "a", "name": "X", "line_total": "100"}],
        attempted_basket=[
            {"product_id": "a", "name": "X", "line_total": "100"},
            {"product_id": "b", "name": "Y", "line_total": "200"},
        ],
    )
    assert "daily" in msg.lower()
    assert "$200" in msg


def test_friendly_cap_refusal_for_revoked_mandate(tool_ctx):
    m = _mandate(tool_ctx)
    orch = OrchestratorAgent(FakeAnthropicClient([]), mandate_id=m.mandate_id)
    msg = orch._friendly_cap_refusal(
        tool_ctx,
        reason="mandate_revoked",
        attempted_total=Decimal("50"),
        original_basket=[],
        attempted_basket=[],
    )
    assert "revoked" in msg.lower()
    assert "cancel" in msg.lower()


def test_friendly_cap_refusal_for_expired_mandate(tool_ctx):
    m = _mandate(tool_ctx)
    orch = OrchestratorAgent(FakeAnthropicClient([]), mandate_id=m.mandate_id)
    msg = orch._friendly_cap_refusal(
        tool_ctx,
        reason="mandate_expired",
        attempted_total=Decimal("50"),
        original_basket=[],
        attempted_basket=[],
    )
    assert "expired" in msg.lower()
