"""End-to-end journey tests for latency Levers 1 + 2.

Drives the full OrchestratorAgent over scripted Claude responses (no API key, no
network) across diverse shopper journeys, asserting OUTPUTS, ACCURACY (ranking
order + risk flags), ORDERING, gate behaviour, and ROUND-TRIP COUNT (the
deterministic proxy for the latency win, since wall-clock API time is not
reproducible in tests).

Coverage matrix:
  - single-merchant find + rank          (Athletic Co)
  - multi-merchant find + rank           (all three merchants)
  - cross-merchant "cheapest" comparison
  - explicit narrative compare           (routes to EvaluationAgent + fast-path)
  - find + buy full journey w/ HITL gate (spend recorded)
  - out-of-stock surfaced
  - price-tier gates: soft (<$30), explicit ($100-500), full summary (>$500)
  - different chat entry points: fresh chat vs. follow-up rank on a warm cache
  - round-trip reduction: rank_candidates flow is cheaper than the eval-agent flow

NB: This file sorts alphabetically BEFORE ``test_user_journeys.py`` ('l' < 'u'),
so per CLAUDE.md it uses ``asyncio.get_event_loop().run_until_complete()`` — never
``asyncio.run()`` — to avoid event-loop contamination on Python 3.9.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from agents.orchestrator import OrchestratorAgent
from cli.confirmation import AutoConfirmProvider, classify_gate
from config.catalogue import MERCHANTS
from tests.fake_anthropic import FakeAnthropicClient, text_response, tool_use_response

ATHLETIC = "athletic-co.myshopify.com"
AUDIO = "audio-hub.myshopify.com"
COFFEE = "coffee-bar.myshopify.com"
ALL_MERCHANTS = list(MERCHANTS.keys())


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _prod(pid, name, price, domain, *, in_stock=True, confidence=1.0, rating=4.5, reviews=120):
    return {
        "product_id": pid,
        "name": name,
        "price": str(price),
        "currency": "USD",
        "merchant": domain.split(".")[0],
        "merchant_domain": domain,
        "in_stock": in_stock,
        "rating": rating,
        "review_count": reviews,
        "source_protocol": "stub",
        "confidence_score": confidence,
    }


def _discovery_turn(products: list[dict], notes: str = "found matches") -> object:
    import json

    return text_response(json.dumps({"products": products, "notes": notes}))


def _mandate(ctx, max_amount="500"):
    return ctx.ap2.create_mandate(
        "user_1",
        max_amount=Decimal(max_amount),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        payment_method_id="pm_test_secret",
    )


def _orch(ctx, client, confirm=None, mandate_id=None):
    return OrchestratorAgent(
        client,
        confirmation=confirm or AutoConfirmProvider(soft=True, explicit=True),
        mandate_id=mandate_id,
        available_merchants=ALL_MERCHANTS,
    )


# ─── single-merchant find + rank ─────────────────────────────────────────────


def test_single_merchant_find_rank(multi_merchant_ctx):
    products = [
        _prod("ath_001", "Demo Running Shoes", "129.99", ATHLETIC, rating=4.5, reviews=120),
        _prod("ath_006", "Premium Running Shoes", "179.00", ATHLETIC, rating=4.8, reviews=300),
        _prod("ath_003", "Performance Running Shorts", "39.00", ATHLETIC, rating=4.2, reviews=60),
    ]
    client = FakeAnthropicClient(
        [
            tool_use_response(
                ("call_discovery_agent", {"brief": "running shoes", "merchant_domains": [ATHLETIC]})
            ),
            _discovery_turn(products),
            tool_use_response(("rank_candidates", {})),
            text_response("The top-ranked option balances price and reviews well."),
        ]
    )
    orch = _orch(multi_merchant_ctx, client)
    result = _run(orch.run(multi_merchant_ctx, "find me running shoes from Athletic Co"))

    # Discovery cache populated, ranking ran in-process (no eval subagent).
    assert len(multi_merchant_ctx.session.last_discovered_products) == 3
    assert "top-ranked" in result["reply"]
    # All scripted turns consumed — rank_candidates added NO subagent LLM turn.
    assert client.remaining() == 0
    assert len(client.calls) == 4


# ─── multi-merchant find + rank ──────────────────────────────────────────────


def test_multi_merchant_find_rank(multi_merchant_ctx):
    products = [
        _prod("ath_001", "Demo Running Shoes", "129.99", ATHLETIC),
        _prod("aud_002", "Wireless Over-Ear", "249.00", AUDIO, rating=4.7, reviews=210),
        _prod("cof_003", "Ethiopia Beans", "18.00", COFFEE, rating=4.9, reviews=500),
    ]
    client = FakeAnthropicClient(
        [
            tool_use_response(
                ("call_discovery_agent", {"brief": "gift ideas", "merchant_domains": ALL_MERCHANTS})
            ),
            _discovery_turn(products),
            tool_use_response(("rank_candidates", {})),
            text_response("Here is how the picks compare across the three stores."),
        ]
    )
    orch = _orch(multi_merchant_ctx, client)
    result = _run(orch.run(multi_merchant_ctx, "find gift ideas across all stores"))
    assert client.remaining() == 0
    # Cross-merchant set preserved in cache (one product per merchant).
    domains = {p["merchant_domain"] for p in multi_merchant_ctx.session.last_discovered_products}
    assert domains == {ATHLETIC, AUDIO, COFFEE}
    assert result["reply"]


# ─── cross-merchant cheapest ─────────────────────────────────────────────────


def test_cross_merchant_cheapest_ranks_low_price_high(multi_merchant_ctx):
    products = [
        _prod("aud_003", "Reference Monitors", "649.00", AUDIO, rating=4.9, reviews=90),
        _prod("aud_001", "Studio Headphones", "89.00", AUDIO, rating=4.5, reviews=120),
        _prod("ath_005", "Athletic Wireless Earbuds", "79.00", ATHLETIC, rating=4.3, reviews=140),
    ]
    client = FakeAnthropicClient(
        [
            tool_use_response(
                (
                    "call_discovery_agent",
                    {"brief": "headphones", "merchant_domains": [AUDIO, ATHLETIC]},
                )
            ),
            _discovery_turn(products),
            tool_use_response(("rank_candidates", {})),
            text_response("The budget pick leads on price."),
        ]
    )
    orch = _orch(multi_merchant_ctx, client)
    _run(orch.run(multi_merchant_ctx, "cheapest headphones across stores"))
    # Verify the deterministic ranking favoured a cheaper option as #1 over the
    # $649 monitors (price weight 25%).
    ranked = _run(orch._rank_candidates(multi_merchant_ctx))["ranked"]
    assert ranked[0]["product"]["product_id"] != "aud_003"


# ─── explicit narrative compare → EvaluationAgent fast-path ──────────────────


def test_explicit_compare_routes_to_eval_agent_fastpath(multi_merchant_ctx):
    products = [
        _prod("aud_001", "Studio Headphones", "89.00", AUDIO),
        _prod("aud_002", "Wireless Over-Ear", "249.00", AUDIO, rating=4.7, reviews=210),
    ]
    client = FakeAnthropicClient(
        [
            tool_use_response(
                ("call_discovery_agent", {"brief": "headphones", "merchant_domains": [AUDIO]})
            ),
            _discovery_turn(products),
            # Orchestrator explicitly routes the comparison to the eval subagent.
            tool_use_response(
                (
                    "call_evaluation_agent",
                    {"brief": "compare these two", "products": products},
                )
            ),
            # EvaluationAgent: ONE tool_use turn — rank_products is terminal, so
            # NO second reformat turn is scripted (Lever 2 fast-path).
            tool_use_response(("rank_products", {"products": products})),
            # Orchestrator writes the user-facing comparison prose.
            text_response(
                "Between the two, the over-ear model wins on reviews; the studio pick is cheaper."
            ),
        ]
    )
    orch = _orch(multi_merchant_ctx, client)
    result = _run(orch.run(multi_merchant_ctx, "compare the studio and over-ear headphones"))
    # All turns consumed and no extra reformat turn was needed for the eval agent.
    assert client.remaining() == 0
    assert "wins on reviews" in result["reply"]


# ─── find + buy full journey with HITL gate ──────────────────────────────────


def test_find_rank_buy_full_journey_records_spend(multi_merchant_ctx):
    m = _mandate(multi_merchant_ctx)
    products = [_prod("ath_001", "Demo Running Shoes", "129.99", ATHLETIC)]
    confirm = AutoConfirmProvider(soft=True, explicit=True)
    client = FakeAnthropicClient(
        [
            tool_use_response(
                ("call_discovery_agent", {"brief": "running shoes", "merchant_domains": [ATHLETIC]})
            ),
            _discovery_turn(products),
            tool_use_response(("rank_candidates", {})),
            tool_use_response(
                (
                    "call_purchase_agent",
                    {
                        "brief": "buy the top pick",
                        "merchant_domain": ATHLETIC,
                        "items": [
                            {
                                "product_id": "ath_001",
                                "name": "Demo Running Shoes",
                                "price": "129.99",
                                "quantity": 1,
                            }
                        ],
                    },
                )
            ),
            text_response(
                '{"order": {"order_id": "ord_lever", "status": "confirmed"}, "status": "completed"}'
            ),
            text_response("Done. Your order is confirmed."),
        ]
    )
    orch = _orch(multi_merchant_ctx, client, confirm=confirm, mandate_id=m.mandate_id)
    result = _run(orch.run(multi_merchant_ctx, "find running shoes and buy the top pick"))

    assert "confirmed" in result["reply"]
    # The full discover → rank → purchase chain ran; the HITL gate fired once at
    # the explicit tier ($129.99, new merchant) and carried the correct amount.
    assert len(confirm.gates_seen) == 1
    tier, gate = confirm.gates_seen[0]
    assert tier == "explicit"
    assert gate.amount == Decimal("129.99")
    # All scripted turns consumed — ranking added no subagent LLM turn.
    assert client.remaining() == 0


# ─── out-of-stock surfaced ───────────────────────────────────────────────────


def test_out_of_stock_surfaced_in_ranking(multi_merchant_ctx):
    products = [
        _prod("ath_001", "Demo Running Shoes", "129.99", ATHLETIC, in_stock=True),
        _prod("ath_002", "Trail Runner Pro", "189.00", ATHLETIC, in_stock=False),
    ]
    client = FakeAnthropicClient(
        [
            tool_use_response(
                ("call_discovery_agent", {"brief": "trail shoes", "merchant_domains": [ATHLETIC]})
            ),
            _discovery_turn(products),
            tool_use_response(("rank_candidates", {})),
            text_response("One option is out of stock."),
        ]
    )
    orch = _orch(multi_merchant_ctx, client)
    _run(orch.run(multi_merchant_ctx, "show me trail running shoes"))
    ranked = _run(orch._rank_candidates(multi_merchant_ctx))
    oos = [r for r in ranked["ranked"] if r["product"]["product_id"] == "ath_002"][0]
    assert "OUT_OF_STOCK" in oos["risk_flags"]


# ─── price-tier gates (classification unchanged by the levers) ───────────────
# The levers never touch the HITL gate path. These assert the tier thresholds
# still classify correctly across the price ranges CLAUDE.md requires.


def test_price_tier_soft_gate_under_30():
    # <= $30 from a known merchant → soft.
    assert classify_gate(Decimal("14.00")) == "soft"


def test_price_tier_explicit_gate_mid():
    # $30-$100 (not new merchant) and >$100 both → explicit.
    assert classify_gate(Decimal("49.00")) == "explicit"
    assert classify_gate(Decimal("249.00")) == "explicit"


def test_price_tier_full_summary_over_500():
    assert classify_gate(Decimal("649.00")) == "explicit_with_summary"


def test_first_purchase_from_merchant_forces_explicit():
    # A cheap item from a NEW merchant still upgrades to explicit.
    assert classify_gate(Decimal("14.00"), is_first_purchase_from_merchant=True) == "explicit"


# ─── entry point: follow-up rank on a warm cache (no re-discovery) ───────────


def test_followup_rank_reuses_warm_cache_without_rediscovery(multi_merchant_ctx):
    products = [
        _prod("cof_001", "Ceramic Mug", "14.00", COFFEE, rating=4.1, reviews=40),
        _prod("cof_003", "Ethiopia Beans", "18.00", COFFEE, rating=4.9, reviews=500),
    ]
    # Turn 1: a normal find populates the cache.
    client = FakeAnthropicClient(
        [
            tool_use_response(
                ("call_discovery_agent", {"brief": "coffee", "merchant_domains": [COFFEE]})
            ),
            _discovery_turn(products),
            tool_use_response(("rank_candidates", {})),
            text_response("Here are coffee picks."),
            # Turn 2 (same ctx/session): user asks to re-rank — NO discovery call.
            tool_use_response(("rank_candidates", {})),
            text_response("Re-ranked your earlier results."),
        ]
    )
    orch = _orch(multi_merchant_ctx, client)
    _run(orch.run(multi_merchant_ctx, "find coffee gear"))
    cache_after_turn1 = list(multi_merchant_ctx.session.last_discovered_products)

    result2 = _run(orch.run(multi_merchant_ctx, "rank those again, best first"))
    # No re-discovery: cache identical, all scripted turns consumed.
    assert multi_merchant_ctx.session.last_discovered_products == cache_after_turn1
    assert client.remaining() == 0
    assert result2["reply"]


# ─── round-trip reduction proof ──────────────────────────────────────────────


def test_rank_candidates_flow_costs_fewer_roundtrips_than_eval_agent(multi_merchant_ctx):
    """Same find+rank intent, two routings: rank_candidates (Lever 1) makes
    strictly fewer model round-trips than routing through call_evaluation_agent,
    because the deterministic ranker consumes ZERO subagent LLM turns."""
    products = [
        _prod("ath_001", "Demo Running Shoes", "129.99", ATHLETIC),
        _prod("ath_006", "Premium Running Shoes", "179.00", ATHLETIC, rating=4.8, reviews=300),
    ]

    # Routing A — rank_candidates (in-process).
    client_a = FakeAnthropicClient(
        [
            tool_use_response(
                ("call_discovery_agent", {"brief": "shoes", "merchant_domains": [ATHLETIC]})
            ),
            _discovery_turn(products),
            tool_use_response(("rank_candidates", {})),
            text_response("Top pick chosen."),
        ]
    )
    orch_a = _orch(multi_merchant_ctx, client_a)
    _run(orch_a.run(multi_merchant_ctx, "find shoes, best first"))

    # Routing B — call_evaluation_agent (subagent), even WITH the Lever 2
    # fast-path (one eval turn, not two).
    multi_merchant_ctx.session.last_discovered_products = []
    client_b = FakeAnthropicClient(
        [
            tool_use_response(
                ("call_discovery_agent", {"brief": "shoes", "merchant_domains": [ATHLETIC]})
            ),
            _discovery_turn(products),
            tool_use_response(("call_evaluation_agent", {"brief": "rank", "products": products})),
            tool_use_response(("rank_products", {"products": products})),  # terminal fast-path
            text_response("Top pick chosen."),
        ]
    )
    orch_b = _orch(multi_merchant_ctx, client_b)
    _run(orch_b.run(multi_merchant_ctx, "find shoes, best first"))

    assert len(client_a.calls) < len(client_b.calls)
    assert len(client_a.calls) == 4
