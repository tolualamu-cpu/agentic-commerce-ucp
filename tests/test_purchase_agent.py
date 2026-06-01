"""PurchaseAgent: full chain orchestration + payment-isolation invariant."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

from agents.purchase import PurchaseAgent
from tests.fake_anthropic import FakeAnthropicClient, text_response, tool_use_response


def _new_mandate(ctx):
    return ctx.ap2.create_mandate(
        "user_1",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        payment_method_id="pm_test_secret",
    )


def test_full_purchase_chain_via_agent(tool_ctx):
    m = _new_mandate(tool_ctx)
    merchant = "demo-shop.myshopify.com"

    client = FakeAnthropicClient(
        [
            tool_use_response(
                (
                    "validate_mandate",
                    {
                        "mandate_id": m.mandate_id,
                        "amount": "100",
                        "vendor": merchant,
                        "category": "running",
                    },
                )
            ),
            tool_use_response(
                (
                    "create_checkout_session",
                    {"merchant_domain": merchant, "mandate_id": m.mandate_id},
                )
            ),
            # We don't know the session_id yet — the agent will use whatever the tool returned.
            # For deterministic tests, we use a fixed session via the next tool's inputs.
            tool_use_response(
                (
                    "update_checkout_session",
                    {
                        "session_id": "PLACEHOLDER",  # real fake will use this as-is
                        "merchant_domain": merchant,
                        "items": [
                            {
                                "product_id": "shop_001",
                                "name": "Shoes",
                                "price": "100",
                                "quantity": 1,
                            }
                        ],
                        "mandate_id": m.mandate_id,
                    },
                )
            ),
            tool_use_response(
                (
                    "get_payment_token",
                    {
                        "mandate_id": m.mandate_id,
                        "amount": "108",
                        "vendor": merchant,
                        "merchant_domain": merchant,
                    },
                )
            ),
            tool_use_response(
                (
                    "complete_order",
                    {
                        "session_id": "PLACEHOLDER",
                        "merchant_domain": merchant,
                        "payment_handler_id": "stripe",
                        "payment_token": "tok_test_xyz",
                        "mandate_id": m.mandate_id,
                    },
                )
            ),
            tool_use_response(("save_order", {"order": {"order_id": "ord_x"}})),
            tool_use_response(
                (
                    "record_mandate_spend",
                    {
                        "mandate_id": m.mandate_id,
                        "amount": "108",
                        "order_id": "ord_x",
                        "vendor": merchant,
                    },
                )
            ),
            text_response('{"order": null, "status": "completed"}'),
        ]
    )
    agent = PurchaseAgent(client)
    result = asyncio.get_event_loop().run_until_complete(
        agent.run(tool_ctx, f"purchase mandate={m.mandate_id}")
    )
    assert result["status"] == "completed"
    # Mandate validated FIRST
    assert client.dispatched_tool_names()[0] == "validate_mandate"


def test_purchase_agent_never_sees_payment_method_id(tool_ctx):
    """No tool call argument should mention the raw payment_method_id."""
    m = _new_mandate(tool_ctx)
    client = FakeAnthropicClient(
        [
            tool_use_response(
                (
                    "get_payment_token",
                    {
                        "mandate_id": m.mandate_id,
                        "amount": "50",
                        "vendor": "demo-shop.myshopify.com",
                    },
                )
            ),
            text_response('{"order": null, "status": "completed"}'),
        ]
    )
    agent = PurchaseAgent(client)
    asyncio.get_event_loop().run_until_complete(agent.run(tool_ctx, "go"))

    # Critical: across every tool call argument the agent emitted, the raw
    # payment_method_id must never appear. This proves the agent doesn't
    # accidentally receive it via tool results either.
    for rec in client.calls:
        blob = json.dumps(rec.messages, default=str)
        assert "pm_test_secret" not in blob


def test_advertises_all_purchase_tools(tool_ctx):
    client = FakeAnthropicClient([text_response('{"order": null, "status": "completed"}')])
    agent = PurchaseAgent(client)
    asyncio.get_event_loop().run_until_complete(agent.run(tool_ctx, "x"))
    advertised = {t["name"] for t in client.calls[0].tools}
    assert {
        "validate_mandate",
        "create_checkout_session",
        "update_checkout_session",
        "get_payment_token",
        "complete_order",
        "save_order",
        "record_mandate_spend",
        "audit_log",
    } <= advertised
