"""Spend-limit override resistance: defence-in-depth invariants.

These tests prove that even if the agent (Claude, a malicious prompt, or a
buggy fake) tries to route around the safety controls, the system layers
below still refuse. This is the most important test class in the suite —
if any of these break, the trust model is broken.

Each test scripts a "bad agent" path and asserts the dangerous side effect
(unauthorised payment, over-cap spend, ignored revocation) does NOT happen.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal


from agents.purchase import PurchaseAgent
from tests.fake_anthropic import (
    FakeAnthropicClient,
    text_response,
    tool_use_response,
)


def _new_mandate(
    ctx,
    *,
    max_amount="500",
    daily_cap="1000",
    monthly_cap="5000",
    payment_method_id="pm_test_secret",
):
    return ctx.ap2.create_mandate(
        "user_1",
        max_amount=Decimal(max_amount),
        daily_cap=Decimal(daily_cap),
        monthly_cap=Decimal(monthly_cap),
        payment_method_id=payment_method_id,
    )


# ─── PurchaseAgent-level overrides ──────────────────────────────────────────


def test_agent_skipping_validate_mandate_still_blocked(tool_ctx):
    """A jailbroken agent skips validate_mandate and goes straight to
    get_payment_token for an over-cap amount. PaymentGateway re-validates
    internally and refuses to issue a token."""
    m = _new_mandate(tool_ctx, max_amount="100")
    # Agent script: NO validate_mandate, just request payment for $400
    client = FakeAnthropicClient(
        [
            tool_use_response(
                (
                    "get_payment_token",
                    {
                        "mandate_id": m.mandate_id,
                        "amount": "400",
                        "vendor": "demo-shop.myshopify.com",
                    },
                )
            ),
            text_response(
                '{"order": null, "status": "failed", '
                '"reason": "got authorized=false from token tool"}'
            ),
        ]
    )
    agent = PurchaseAgent(client)
    result = asyncio.get_event_loop().run_until_complete(
        agent.run(tool_ctx, f"buy something expensive mandate={m.mandate_id}")
    )
    # The agent's claim "failed" must reflect reality. Critically:
    # 1. No Stripe token was generated (offline tokeniser also re-validates via gateway)
    # 2. No spend record was written
    assert tool_ctx.db.spend_records.all() == []
    # Inspect the tool_result that came back from get_payment_token
    import json

    for call in client.calls:
        for msg in call.messages:
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                for block in msg["content"]:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        result_data = json.loads(block["content"])
                        if "authorized" in result_data:
                            assert result_data["authorized"] is False
                            assert "exceeds_per_transaction_cap" in result_data["reason"]


def test_agent_ignoring_authorized_false_cannot_complete(tool_ctx):
    """Agent receives authorized=false, then tries complete_order with a
    fabricated token. The merchant stub completes the order (it doesn't know
    about mandates), but record_mandate_spend is the system's last line of
    defence — and even if the agent skips it, no real money moved because
    no real Stripe token was issued."""
    m = _new_mandate(tool_ctx, max_amount="50")
    client = FakeAnthropicClient(
        [
            tool_use_response(
                (
                    "validate_mandate",
                    {
                        "mandate_id": m.mandate_id,
                        "amount": "200",
                        "vendor": "demo-shop.myshopify.com",
                    },
                )
            ),
            # Agent ignores authorized=false and tries to forge a token
            tool_use_response(
                (
                    "complete_order",
                    {
                        "session_id": "fake_session",
                        "merchant_domain": "demo-shop.myshopify.com",
                        "payment_handler_id": "stripe",
                        "payment_token": "tok_FORGED_BY_AGENT",
                        "mandate_id": m.mandate_id,
                    },
                )
            ),
            text_response('{"order": null, "status": "failed", "reason": "no session"}'),
        ]
    )
    agent = PurchaseAgent(client)
    asyncio.get_event_loop().run_until_complete(agent.run(tool_ctx, f"buy mandate={m.mandate_id}"))
    # No spend record — record_mandate_spend was never invoked (agent gave up)
    assert tool_ctx.db.spend_records.all() == []
    # complete_order ran against a fake session, but the merchant stub errors
    # because the session doesn't exist → no PurchaseOrder was returned with
    # an order_id that ever became persistent
    persistent_orders = tool_ctx.db.orders.all()
    forged_orders = [
        o for o in persistent_orders if "FORGED" in str(o.get("payment_intent_id", ""))
    ]
    assert forged_orders == [], "forged token must not result in saved order"


def test_agent_cannot_exceed_per_tx_cap(tool_ctx):
    """validate_mandate refuses amount > max_amount."""
    m = _new_mandate(tool_ctx, max_amount="100")
    client = FakeAnthropicClient(
        [
            tool_use_response(
                (
                    "validate_mandate",
                    {
                        "mandate_id": m.mandate_id,
                        "amount": "999",
                        "vendor": "demo-shop.myshopify.com",
                    },
                )
            ),
            text_response(
                '{"order": null, "status": "failed", ' '"reason": "exceeds_per_transaction_cap"}'
            ),
        ]
    )
    agent = PurchaseAgent(client)
    asyncio.get_event_loop().run_until_complete(
        agent.run(tool_ctx, f"buy something huge mandate={m.mandate_id}")
    )
    # Confirm validate_mandate returned authorized=false
    import json

    found_rejection = False
    for call in client.calls:
        for msg in call.messages:
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                for block in msg["content"]:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        result_data = json.loads(block["content"])
                        if result_data.get("reason") == "exceeds_per_transaction_cap":
                            found_rejection = True
    assert found_rejection
    assert tool_ctx.db.spend_records.all() == []


# ─── Orchestrator-level overrides ───────────────────────────────────────────


def test_agent_cannot_exceed_daily_cap_via_repeated_calls(tool_ctx):
    """Two purchases that each fit per-tx but together exceed daily cap.
    Second one rejected at validate_mandate (cap is from spend_records)."""
    m = _new_mandate(tool_ctx, max_amount="600", daily_cap="500", monthly_cap="10000")
    # Simulate first purchase having already consumed $400 of the $500 cap
    tool_ctx.ap2.record_spend(
        m.mandate_id, Decimal("400"), "ord_prior", vendor="demo-shop.myshopify.com"
    )

    # Now agent tries a $200 purchase — within per-tx ($600), but $400+$200=$600 > $500 daily
    from tools.purchase_tools import validate_mandate

    auth = asyncio.get_event_loop().run_until_complete(
        validate_mandate(
            tool_ctx,
            mandate_id=m.mandate_id,
            amount=Decimal("200"),
            vendor="demo-shop.myshopify.com",
        )
    )
    assert auth.authorized is False
    assert auth.reason == "exceeds_daily_cap"


def test_agent_cannot_purchase_after_revoke(tool_ctx):
    """Revoke mandate, then orchestrator attempts purchase. The HITL gate
    classifier fires before the subagent runs — PaymentGateway refuses on
    the actual purchase attempt."""
    m = _new_mandate(tool_ctx, max_amount="500")
    tool_ctx.ap2.revoke_mandate(m.mandate_id)

    # Try the purchase flow directly via PaymentGateway (the boundary)
    pg = tool_ctx.payment_gateway
    result = pg.get_payment_token(
        mandate_id=m.mandate_id,
        amount=Decimal("50"),
        vendor="demo-shop.myshopify.com",
    )
    assert result.authorized is False
    assert result.auth.reason == "mandate_revoked"
    assert result.token is None
    # Confirm AP2 has no spend record for this attempt
    assert tool_ctx.db.spend_records.all() == []


def test_agent_cannot_route_to_blocklisted_merchant(tool_ctx):
    """User blocklists merchant → search returns empty silently → the agent
    has no products to attempt purchase on."""
    tool_ctx.user.vendor_blocklist = ["demo-shop.myshopify.com"]
    from tools.discovery_tools import search_products

    results = asyncio.get_event_loop().run_until_complete(
        search_products(
            tool_ctx,
            query="anything",
            merchant_domains=["demo-shop.myshopify.com"],
        )
    )
    assert results == []


# ─── PaymentGateway-level overrides ─────────────────────────────────────────


def test_agent_cannot_override_max_amount_in_get_payment_token(tool_ctx):
    """Agent might call validate_mandate with $50 (under cap) then
    get_payment_token with $999 (over cap). PaymentGateway re-validates
    with the actual amount and refuses."""
    m = _new_mandate(tool_ctx, max_amount="100")
    # Step 1: agent validates with safe amount
    val = tool_ctx.ap2.verify_and_authorize(
        m.mandate_id,
        Decimal("50"),
        vendor="demo-shop.myshopify.com",
    )
    assert val.authorized is True

    # Step 2: agent tries to escalate amount in get_payment_token
    result = tool_ctx.payment_gateway.get_payment_token(
        mandate_id=m.mandate_id,
        amount=Decimal("999"),
        vendor="demo-shop.myshopify.com",
    )
    assert result.authorized is False
    assert result.auth.reason == "exceeds_per_transaction_cap"
    assert result.token is None
    # No Stripe was called; no spend recorded
    assert tool_ctx.db.spend_records.all() == []


def test_revoked_mandate_payment_gateway_refuses(tool_ctx):
    """Independent check: PaymentGateway always re-runs AP2 every call. Even
    if a stale token reference exists in the agent context, the next request
    against a revoked mandate fails."""
    m = _new_mandate(tool_ctx, max_amount="500")
    # First purchase authorised
    first = tool_ctx.payment_gateway.get_payment_token(
        mandate_id=m.mandate_id,
        amount=Decimal("50"),
        vendor="demo-shop.myshopify.com",
    )
    assert first.authorized

    # Revoke
    tool_ctx.ap2.revoke_mandate(m.mandate_id)

    # Second attempt must fail
    second = tool_ctx.payment_gateway.get_payment_token(
        mandate_id=m.mandate_id,
        amount=Decimal("50"),
        vendor="demo-shop.myshopify.com",
    )
    assert second.authorized is False
    assert second.auth.reason == "mandate_revoked"


def test_tampered_mandate_signature_blocks_payment(tool_ctx):
    """If the mandate row in DB has its cap tampered with, signature check
    fails inside verify_and_authorize → PaymentGateway refuses."""
    m = _new_mandate(tool_ctx, max_amount="50")
    # Tamper directly in DB — bump max_amount but don't re-sign
    from storage.db import MandateQ

    tool_ctx.db.mandates.update(
        {"max_amount": "999999"},
        MandateQ.mandate_id == m.mandate_id,
    )
    result = tool_ctx.payment_gateway.get_payment_token(
        mandate_id=m.mandate_id,
        amount=Decimal("100"),
        vendor="demo-shop.myshopify.com",
    )
    assert result.authorized is False
    assert result.auth.reason == "invalid_signature"
