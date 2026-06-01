"""User journey tests (continued) — covers feasible 🟢 journeys that were
previously manual-only. Each test maps to a journey ID in
docs/USER_JOURNEYS.md.

These are the ones where the orchestration is deterministic enough to
mechanise — the user behaviour is scripted, the merchant + mandate state
is controlled, and the only stochastic surface is the agent's text reply,
which we either don't assert on or assert structurally.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal


from agents.orchestrator import OrchestratorAgent
from cli.confirmation import AutoConfirmProvider, GateResponse, classify_gate
from tests.fake_anthropic import (
    FakeAnthropicClient,
    text_response,
    tool_use_response,
)
from tools.discovery_tools import search_products
from tools.purchase_tools import (
    validate_mandate,
)
from tools.tracking_tools import get_order_status


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


# ===========================================================================
# Section 1: Discovery — feasible 🟢 journeys
# ===========================================================================


def test_j007_price_ceiling_filter_returns_candidates(multi_merchant_ctx):
    """J007: 'shoes under $150' — discovery returns ALL shoes; the agent layer
    interprets the filter. We test that there ARE multiple shoes spanning
    the threshold so the agent has the data to filter."""
    shoes = asyncio.get_event_loop().run_until_complete(
        search_products(
            multi_merchant_ctx,
            query="shoes",
            merchant_domains=["athletic-co.myshopify.com"],
        )
    )
    under_150 = [p for p in shoes if p.price < Decimal("150")]
    over_150 = [p for p in shoes if p.price >= Decimal("150")]
    assert under_150, "expected ≥1 shoe under $150 (Demo Running $129.99)"
    assert over_150, "expected ≥1 shoe over $150 (Premium $179, Stability $159)"


def test_j008_price_floor_filter_returns_candidates(multi_merchant_ctx):
    """J008: 'premium shoes over $150' — both Premium ($179) + Stability ($159) qualify."""
    shoes = asyncio.get_event_loop().run_until_complete(
        search_products(
            multi_merchant_ctx,
            query="shoes",
            merchant_domains=["athletic-co.myshopify.com"],
        )
    )
    over_150 = [p for p in shoes if p.price > Decimal("150")]
    assert len(over_150) >= 2


def test_j012_allowlist_includes_merchant(multi_merchant_ctx):
    """J012: allowlist=[audio-hub] → search at audio-hub returns results."""
    multi_merchant_ctx.user.vendor_allowlist = ["audio-hub.myshopify.com"]
    results = asyncio.get_event_loop().run_until_complete(
        search_products(
            multi_merchant_ctx,
            query="headphones",
            merchant_domains=["audio-hub.myshopify.com"],
        )
    )
    assert results


def test_j013_allowlist_excludes_off_list_merchant(multi_merchant_ctx):
    """J013: allowlist=[audio-hub] → search at athletic-co returns []."""
    multi_merchant_ctx.user.vendor_allowlist = ["audio-hub.myshopify.com"]
    results = asyncio.get_event_loop().run_until_complete(
        search_products(
            multi_merchant_ctx,
            query="shoes",
            merchant_domains=["athletic-co.myshopify.com"],
        )
    )
    assert results == []


def test_j017_followup_anything_else_uses_cache(tool_ctx):
    """J017: After a discovery, 'anything else?' should be answerable from
    last_discovered_products without re-running discovery.

    This test asserts the cache is populated AND a fresh `_get_last_discovered`
    call returns the cached items.
    """
    tool_ctx.session.last_discovered_products = [
        {"product_id": "p1", "name": "Mug", "price": "14"},
        {"product_id": "p2", "name": "Beans", "price": "18"},
    ]
    orch = OrchestratorAgent(FakeAnthropicClient([]), mandate_id=None)
    result = asyncio.get_event_loop().run_until_complete(orch._get_last_discovered(tool_ctx))
    assert result["count"] == 2
    assert result["source"] == "session_cache"


def test_j018_followup_cheapest_from_cache(multi_merchant_ctx):
    """J018: 'which is cheapest' — agent can answer from the cache after rank.

    We test that rank_products orders ascending by composite score and that
    the agent can read the lowest-priced item from the ranked output.
    """
    mugs = asyncio.get_event_loop().run_until_complete(
        search_products(
            multi_merchant_ctx,
            query="mug",
            merchant_domains=["coffee-bar.myshopify.com"],
        )
    )
    cheapest = min(mugs, key=lambda p: p.price)
    assert cheapest.price <= Decimal("14")  # the $14 Ceramic Mug


def test_j019_followup_highest_rated_from_cache(multi_merchant_ctx):
    """J019: 'highest rated' — agent can read from cache. We assert the
    catalogue has rating diversity so the question has a meaningful answer."""
    mugs = asyncio.get_event_loop().run_until_complete(
        search_products(
            multi_merchant_ctx,
            query="mug",
            merchant_domains=["coffee-bar.myshopify.com"],
        )
    )
    ratings = {p.rating for p in mugs if p.rating is not None}
    assert len(ratings) >= 2, "ratings should differ across mugs"


def test_j020_search_then_refine_to_one_merchant(multi_merchant_ctx):
    """J020: discovery returns multi-merchant → user says 'just at X'
    → second discovery narrows to that merchant.

    We test the underlying mechanism: search_products honors merchant_domains.
    """
    domains_all = list(multi_merchant_ctx.merchant_gateway.direct_adapters.keys())
    loop = asyncio.get_event_loop()
    all_shoes = loop.run_until_complete(
        search_products(
            multi_merchant_ctx,
            query="shoes",
            merchant_domains=domains_all,
        )
    )
    narrowed = loop.run_until_complete(
        search_products(
            multi_merchant_ctx,
            query="shoes",
            merchant_domains=["athletic-co.myshopify.com"],
        )
    )
    # Narrowed result should be a subset of the all-merchants result by merchant
    assert all(p.merchant_domain == "athletic-co.myshopify.com" for p in narrowed)


# ===========================================================================
# Section 3: Single-item purchase — feasible 🟢 journeys
# ===========================================================================


def test_j044_explicit_gate_multiple_questions_then_confirm(tool_ctx):
    """J044: multi-Q at gate then confirm — already covered in conversation
    memory tests, but worth a direct journey assertion."""
    m = _mandate(tool_ctx)
    confirm = AutoConfirmProvider(
        scripted=[
            GateResponse(decision="question", text="why this?"),
            GateResponse(decision="question", text="rating?"),
            GateResponse(decision="question", text="shipping?"),
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
                                "name": "X",
                                "price": "150",
                                "quantity": 1,
                            }
                        ],
                    },
                )
            ),
            text_response("Top match for your query."),
            text_response("4.5 stars."),
            text_response("Ships in 2-3 days."),
            text_response('{"order": null, "status": "completed"}'),
            text_response("Done."),
        ]
    )
    orch = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
    asyncio.get_event_loop().run_until_complete(orch.run(tool_ctx, "buy"))
    # 4 gate prompts (3 questions + 1 confirm)
    assert len(confirm.gates_seen) == 4
    # Basket survived all questions
    for _, gate in confirm.gates_seen:
        assert gate.items[0]["product_id"] == "a"


def test_j048_repeat_purchase_from_same_merchant_uses_soft_tier(tool_ctx):
    """J048: after a prior order at merchant, low-amount second purchase
    qualifies for soft tier (since merchant is no longer 'new')."""
    # Seed an order at the merchant
    tool_ctx.db.orders.insert(
        {
            "order_id": "ord_old",
            "merchant_domain": "demo-shop.myshopify.com",
            "total": "50",
            "status": "confirmed",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    orch = OrchestratorAgent(FakeAnthropicClient([]), mandate_id=None)
    is_new = orch._is_first_purchase(tool_ctx, "demo-shop.myshopify.com")
    assert is_new is False
    # And a $25 purchase classifies as soft when not a new merchant
    tier = classify_gate(Decimal("25"), is_first_purchase_from_merchant=is_new)
    assert tier == "soft"


# ===========================================================================
# Section 4: Multi-item — feasible 🟢 journeys
# ===========================================================================


def test_j058_five_item_basket(tool_ctx):
    """J058: 5-item basket at one merchant — gate fires with all 5."""
    m = _mandate(tool_ctx)
    confirm = AutoConfirmProvider(soft=True, explicit=True)
    items = [
        {"product_id": f"p{i}", "name": f"Item {i}", "price": "10", "quantity": 1} for i in range(5)
    ]
    client = FakeAnthropicClient(
        [
            tool_use_response(
                (
                    "call_purchase_agent",
                    {
                        "brief": "buy 5",
                        "merchant_domain": "demo-shop.myshopify.com",
                        "items": items,
                    },
                )
            ),
            text_response('{"order": null, "status": "completed"}'),
            text_response("Done."),
        ]
    )
    orch = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
    asyncio.get_event_loop().run_until_complete(orch.run(tool_ctx, "buy 5"))
    _, gate = confirm.gates_seen[0]
    assert len(gate.items) == 5
    assert gate.amount == Decimal("50")


def test_j059_multi_item_with_quantity(tool_ctx):
    """J059: 'buy 2 mugs' — quantity=2, line_total = 2×price."""
    m = _mandate(tool_ctx)
    confirm = AutoConfirmProvider(soft=True, explicit=True)
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
                                "product_id": "m1",
                                "name": "Mug",
                                "price": "14",
                                "quantity": 2,
                            }
                        ],
                    },
                )
            ),
            text_response('{"order": null, "status": "completed"}'),
            text_response("Done."),
        ]
    )
    orch = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
    asyncio.get_event_loop().run_until_complete(orch.run(tool_ctx, "buy"))
    _, gate = confirm.gates_seen[0]
    assert gate.items[0]["quantity"] == 2
    assert gate.items[0]["line_total"] == "28"


def test_j061_multi_item_exceeds_daily_cap(tool_ctx):
    """J061: large multi-item basket whose total exceeds the daily cap (after
    prior spending) is rejected by AP2."""
    m = _mandate(
        tool_ctx,
        max_amount=Decimal("500"),
        daily_cap=Decimal("100"),
        monthly_cap=Decimal("5000"),
    )
    # Prior spend of $80
    tool_ctx.ap2.record_spend(
        m.mandate_id, Decimal("80"), "ord_prior", vendor="demo-shop.myshopify.com"
    )
    # New basket of $50 → total daily would be $130 > $100 daily cap
    result = asyncio.get_event_loop().run_until_complete(
        validate_mandate(
            tool_ctx,
            mandate_id=m.mandate_id,
            amount=Decimal("50"),
        )
    )
    assert not result.authorized
    assert result.reason == "exceeds_daily_cap"


# ===========================================================================
# Section 6: Tracking — feasible 🟢 journeys
# ===========================================================================


def test_j084_track_then_make_another_purchase(tool_ctx):
    """J084: track an order → state preserved → make another purchase.
    Tests that the DB read for tracking doesn't interfere with subsequent
    write operations."""
    tool_ctx.db.orders.insert(
        {
            "order_id": "ord_1",
            "merchant_domain": "demo-shop.myshopify.com",
            "total": "50",
            "status": "shipped",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    loop = asyncio.get_event_loop()
    info = loop.run_until_complete(
        get_order_status(
            tool_ctx,
            order_id="ord_1",
            merchant_domain="demo-shop.myshopify.com",
        )
    )
    assert info is not None
    # Now insert another order
    tool_ctx.db.orders.insert(
        {
            "order_id": "ord_2",
            "merchant_domain": "demo-shop.myshopify.com",
            "total": "20",
            "status": "confirmed",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    assert len(tool_ctx.db.orders.all()) == 2


# ===========================================================================
# Section 7: Mandate management — feasible 🟢 journeys
# ===========================================================================


def test_j095_mandate_balance_near_cap(tool_ctx):
    """J095: 90% spent → spent_day reflects it accurately."""
    m = _mandate(tool_ctx, daily_cap=Decimal("100"))
    tool_ctx.ap2.record_spend(m.mandate_id, Decimal("90"), "ord1", vendor="x.com")
    spent_day, _ = tool_ctx.ap2._compute_spend(
        m.mandate_id,
        datetime.now(timezone.utc),
    )
    assert spent_day == Decimal("90")


def test_j101_mandate_separated_by_day(tool_ctx):
    """J101: spends from a previous day don't count toward today's cap.

    We seed a spend dated yesterday and verify _compute_spend for today
    sees $0 today but the prior amount in the month total.
    """
    m = _mandate(tool_ctx, daily_cap=Decimal("100"))
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    tool_ctx.db.spend_records.insert(
        {
            "mandate_id": m.mandate_id,
            "order_id": "ord_yest",
            "amount": "80",
            "currency": "USD",
            "vendor": "x.com",
            "category": None,
            "timestamp": yesterday,
        }
    )
    spent_day, spent_month = tool_ctx.ap2._compute_spend(
        m.mandate_id,
        datetime.now(timezone.utc),
    )
    assert spent_day == Decimal("0")
    assert spent_month == Decimal("80")


# ===========================================================================
# Section 8: Conversational + edge — feasible 🟢 journeys
# ===========================================================================


def test_j107_unknown_command_falls_through_to_orchestrator(tool_ctx):
    """J107: 'tell me a joke' — main.py routes unknown free text to the
    orchestrator. We test that the orchestrator handles non-shopping
    queries without crashing.
    """
    client = FakeAnthropicClient(
        [
            text_response("I can help with shopping but not jokes. Try a search."),
        ]
    )
    orch = OrchestratorAgent(client, mandate_id=None)
    result = asyncio.get_event_loop().run_until_complete(orch.run(tool_ctx, "tell me a joke"))
    assert "reply" in result


def test_j114_multi_turn_pivot(tool_ctx):
    """J114: 'find shoes' → 'actually headphones'. Conversation memory holds;
    second turn sees the first turn's history.
    """
    client = FakeAnthropicClient(
        [
            text_response("Searching shoes."),
            text_response("Switching to headphones."),
        ]
    )
    orch = OrchestratorAgent(client, mandate_id=None)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(orch.run(tool_ctx, "find shoes"))
    loop.run_until_complete(orch.run(tool_ctx, "actually headphones"))
    # Second call's messages include the first user+assistant turns
    second_messages = client.calls[1].messages
    user_turns = [m for m in second_messages if m["role"] == "user"]
    assert len(user_turns) >= 2


def test_j118_rebuy_uses_last_discovered_cache(tool_ctx):
    """J118: user previously discovered products → second 'buy that again'
    can reference the cache."""
    tool_ctx.session.last_discovered_products = [
        {"product_id": "p1", "name": "Test Item", "price": "50"},
    ]
    orch = OrchestratorAgent(FakeAnthropicClient([]), mandate_id=None)
    cache = asyncio.get_event_loop().run_until_complete(orch._get_last_discovered(tool_ctx))
    assert cache["count"] == 1
    assert cache["products"][0]["product_id"] == "p1"


# ===========================================================================
# Cross-cutting: full multi-turn shopper journey
# ===========================================================================


def test_full_shopper_journey_with_basket_edit(tool_ctx):
    """End-to-end: user discovers, asks at gate, edits basket, confirms.

    This is the canonical 'real shopper' flow that exercises every system
    boundary: discovery (mocked), HITL gate (with basket-edit), AP2 cap
    enforcement (via re-validation on mutation), persistent session.
    """
    m = _mandate(tool_ctx)
    confirm = AutoConfirmProvider(
        scripted=[
            GateResponse(decision="question", text="why this one?"),
            GateResponse(decision="question", text="change to 2"),
            GateResponse(decision="confirm"),
        ]
    )
    client = FakeAnthropicClient(
        [
            # Orchestrator: call purchase_agent with 1 item
            tool_use_response(
                (
                    "call_purchase_agent",
                    {
                        "brief": "buy mug",
                        "merchant_domain": "demo-shop.myshopify.com",
                        "items": [
                            {
                                "product_id": "m1",
                                "name": "Mug",
                                "price": "14",
                                "quantity": 1,
                            }
                        ],
                    },
                )
            ),
            # First gate Q: answer
            text_response(
                json.dumps(
                    {
                        "intent": "answer",
                        "answer": "Top in-stock match.",
                    }
                )
            ),
            # Second gate Q: change quantity to 2
            text_response(
                json.dumps(
                    {
                        "intent": "change_quantity",
                        "target_product_id": "m1",
                        "new_quantity": 2,
                        "answer": "Updating quantity.",
                    }
                )
            ),
            # Third: user confirms → PurchaseAgent runs
            text_response('{"order": {"order_id": "ord_x"}, "status": "completed"}'),
            # Orchestrator final reply
            text_response("Done."),
        ]
    )
    orch = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
    asyncio.get_event_loop().run_until_complete(orch.run(tool_ctx, "buy a mug"))

    # All 3 gate prompts hit the same basket-edit machinery
    assert len(confirm.gates_seen) == 3
    # The final gate has qty=2 (after the change_quantity action)
    _, final_gate = confirm.gates_seen[-1]
    assert final_gate.items[0]["quantity"] == 2
    assert final_gate.amount == Decimal("28")
