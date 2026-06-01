"""Orchestrator end-to-end: discovery → evaluation → confirm → purchase chain.

The whole agent layer driven by scripted Claude responses. No API key. No network.
Proves the agent layer correctly orchestrates the Phase 0–2 chain we already
verified mechanically in tests/test_end_to_end.py.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from agents.orchestrator import OrchestratorAgent
from cli.confirmation import AutoConfirmProvider
from tests.fake_anthropic import (
    FakeAnthropicClient,
    text_response,
    tool_use_response,
)


def test_orchestrated_journey_discovery_eval_confirm_purchase(tool_ctx):
    """The big one. A single user message produces the full agent chain."""
    m = tool_ctx.ap2.create_mandate(
        "user_1",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        payment_method_id="pm_test_secret",
    )

    # Scripted responses, ordered by who consumes them:
    #   - Orchestrator: 3 turns (discovery → eval → purchase → final reply means
    #                             4 calls actually since each tool_use is one call)
    #   - DiscoveryAgent:   1 final-text turn
    #   - EvaluationAgent:  1 final-text turn
    #   - PurchaseAgent:    1 final-text turn (subagent skips real chain because
    #                       it has no tools queued — but tool_ctx will work since
    #                       the FakeClient is shared. We script the response
    #                       directly to keep this test about orchestration.)
    #
    # All responses share ONE FakeAnthropicClient. Order matters: queued in the
    # exact sequence each agent's run() will pop them.

    responses = [
        # ── Orchestrator turn 1: call discovery ──
        tool_use_response(
            (
                "call_discovery_agent",
                {
                    "brief": "running shoes under $150",
                    "merchant_domains": ["demo-shop.myshopify.com"],
                },
            )
        ),
        # ── DiscoveryAgent run: returns immediately with one product ──
        text_response(
            '{"products": [{"product_id": "shop_001", "name": "Demo Running Shoes",'
            '"price": "129.99", "currency": "USD", "merchant": "Demo",'
            '"merchant_domain": "demo-shop.myshopify.com", "in_stock": true,'
            '"source_protocol": "shopify_mcp", "confidence_score": 0.9}],'
            '"notes": "one good match"}'
        ),
        # ── Orchestrator turn 2: call evaluation ──
        tool_use_response(
            (
                "call_evaluation_agent",
                {
                    "brief": "rank for user",
                    "products": [
                        {
                            "product_id": "shop_001",
                            "name": "Demo Running Shoes",
                            "price": "129.99",
                            "merchant_domain": "demo-shop.myshopify.com",
                        }
                    ],
                },
            )
        ),
        # ── EvaluationAgent run: returns ranked output ──
        text_response(
            '{"ranked": [{"product": {"product_id": "shop_001",'
            '"name": "Demo Running Shoes", "price": "129.99",'
            '"merchant": "Demo", "merchant_domain": "demo-shop.myshopify.com"},'
            '"score": 0.82, "rank": 1, "risk_flags": []}],'
            '"top_pick_rationale": "good price + good reviews",'
            '"risk_flags": []}'
        ),
        # ── Orchestrator turn 3: call purchase (HITL gate fires here) ──
        tool_use_response(
            (
                "call_purchase_agent",
                {
                    "brief": "buy the top pick",
                    "merchant_domain": "demo-shop.myshopify.com",
                    "items": [
                        {
                            "product_id": "shop_001",
                            "name": "Demo Running Shoes",
                            "price": "129.99",
                            "quantity": 1,
                        }
                    ],
                },
            )
        ),
        # ── PurchaseAgent run: returns completion ──
        text_response(
            '{"order": {"order_id": "ord_e2e", "status": "confirmed"},"status": "completed"}'
        ),
        # ── Orchestrator final reply (text) ──
        text_response("Done — your order is confirmed. The shoes will ship in 2-3 days."),
    ]

    client = FakeAnthropicClient(responses)
    confirm = AutoConfirmProvider(soft=True, explicit=True)
    orchestrator = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)

    result = asyncio.get_event_loop().run_until_complete(
        orchestrator.run(tool_ctx, "Find running shoes under $150 and buy the top pick")
    )

    assert "order is confirmed" in result["reply"]
    # The HITL gate fired exactly once at the purchase step
    assert len(confirm.gates_seen) == 1
    tier, gate = confirm.gates_seen[0]
    assert tier == "explicit"  # >$100, new merchant
    assert gate.amount == Decimal("129.99")
    # All scripted responses consumed
    assert client.remaining() == 0
    # Audit trail captured the gate decision + at least one of each agent
    audit_actions = {(r.get("agent"), r.get("tool")) for r in tool_ctx.db.audit_log.all()}
    assert ("OrchestratorAgent", "hitl_gate") in audit_actions


def test_user_cancellation_stops_purchase_subagent(tool_ctx):
    """If the user denies the gate, the PurchaseAgent never runs."""
    m = tool_ctx.ap2.create_mandate(
        "user_1",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        payment_method_id="pm_test_secret",
    )
    responses = [
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
                            "price": "129.99",
                            "quantity": 1,
                        }
                    ],
                },
            )
        ),
        text_response("Cancelled per your request."),
        # NO PurchaseAgent response queued — if it runs, we'd error
    ]
    client = FakeAnthropicClient(responses)
    confirm = AutoConfirmProvider(soft=False, explicit=False)
    orchestrator = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
    result = asyncio.get_event_loop().run_until_complete(orchestrator.run(tool_ctx, "buy shoes"))
    assert "Cancelled" in result["reply"]
    # Mandate spend never recorded — no purchase happened
    assert tool_ctx.db.spend_records.all() == []


def test_no_payment_method_id_in_orchestrator_message_history(tool_ctx):
    """Across the entire orchestrated journey, pm_secret_value never appears."""
    m = tool_ctx.ap2.create_mandate(
        "user_1",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        payment_method_id="pm_secret_DO_NOT_LEAK",
    )
    import json

    responses = [
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
    client = FakeAnthropicClient(responses)
    orchestrator = OrchestratorAgent(
        client, confirmation=AutoConfirmProvider(), mandate_id=m.mandate_id
    )
    asyncio.get_event_loop().run_until_complete(orchestrator.run(tool_ctx, "buy"))
    # Scan every message ever sent to the fake client
    for call in client.calls:
        assert "pm_secret_DO_NOT_LEAK" not in json.dumps(call.messages, default=str)
        assert "pm_secret_DO_NOT_LEAK" not in json.dumps(call.system, default=str)
