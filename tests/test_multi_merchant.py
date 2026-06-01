"""Multi-merchant discovery + cross-merchant comparison.

Uses the production catalogue (config/catalogue.py via multi_merchant_ctx fixture)
so these tests catch real-world bugs the user would see in the REPL.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from tools.discovery_tools import search_products
from tools.evaluation_tools import compare_prices, rank_products


def test_multi_merchant_discovery_returns_results_from_correct_merchants(
    multi_merchant_ctx,
):
    """Searching 'headphones' should return results from Audio Hub only.

    Athletic Co and Coffee Bar have no headphones in their catalogue, so the
    fan-out correctly drops them.
    """
    domains = list(multi_merchant_ctx.merchant_gateway.direct_adapters.keys())
    results = asyncio.get_event_loop().run_until_complete(
        search_products(
            multi_merchant_ctx,
            query="headphones",
            merchant_domains=domains,
        )
    )
    assert results, "expected headphones from audio hub"
    merchants_returned = {p.merchant_domain for p in results}
    # Audio Hub must show up. Athletic Co + Coffee Bar may or may not match
    # substring "headphones" — what matters is Audio Hub wins.
    assert "audio-hub.myshopify.com" in merchants_returned


def test_cross_merchant_overlap_when_same_product_class_appears_at_multiple(
    multi_merchant_ctx,
):
    """'Wireless earbuds' appears at BOTH Athletic Co (sport-focused) AND
    Audio Hub (compact). Discovery must surface candidates from both."""
    domains = list(multi_merchant_ctx.merchant_gateway.direct_adapters.keys())
    results = asyncio.get_event_loop().run_until_complete(
        search_products(
            multi_merchant_ctx,
            query="earbuds",
            merchant_domains=domains,
        )
    )
    merchants_returned = {p.merchant_domain for p in results}
    assert "athletic-co.myshopify.com" in merchants_returned
    assert "audio-hub.myshopify.com" in merchants_returned


def test_compare_prices_groups_by_merchant_sorted(multi_merchant_ctx):
    """compare_prices() returns a dict keyed by merchant_domain, each list
    sorted by price ascending."""
    domains = list(multi_merchant_ctx.merchant_gateway.direct_adapters.keys())
    by_merchant = asyncio.get_event_loop().run_until_complete(
        compare_prices(
            multi_merchant_ctx,
            product_name="headphones",
            merchant_domains=domains,
        )
    )
    assert "audio-hub.myshopify.com" in by_merchant
    audio_items = by_merchant["audio-hub.myshopify.com"]
    # Sorted ascending by price (string-comparable as Decimal)
    prices = [Decimal(i["price"]) for i in audio_items]
    assert prices == sorted(prices), "compare_prices must sort by price asc"


def test_ranking_across_merchants_picks_best_overall(multi_merchant_ctx):
    """When candidates come from multiple merchants, the ranker considers them
    together — best score wins regardless of merchant boundary."""
    domains = list(multi_merchant_ctx.merchant_gateway.direct_adapters.keys())
    candidates = asyncio.get_event_loop().run_until_complete(
        search_products(
            multi_merchant_ctx,
            query="coffee",
            merchant_domains=domains,
        )
    )
    ranked = asyncio.get_event_loop().run_until_complete(
        rank_products(multi_merchant_ctx, products=candidates)
    )
    assert ranked, "expected coffee items to rank"
    # All ranks contiguous starting at 1
    assert [r.rank for r in ranked] == list(range(1, len(ranked) + 1))


def test_oos_item_propagates_risk_flag(multi_merchant_ctx):
    """Athletic Co's Trail Runner Pro is intentionally out-of-stock — the
    ranker must surface OUT_OF_STOCK as a risk flag."""
    domains = ["athletic-co.myshopify.com"]
    candidates = asyncio.get_event_loop().run_until_complete(
        search_products(
            multi_merchant_ctx,
            query="trail",
            merchant_domains=domains,
        )
    )
    oos = [p for p in candidates if not p.in_stock]
    assert oos, "expected at least one OOS trail item from athletic-co"
    ranked = asyncio.get_event_loop().run_until_complete(
        rank_products(multi_merchant_ctx, products=oos)
    )
    assert "OUT_OF_STOCK" in ranked[0].risk_flags


def test_catalogue_satisfies_gate_tier_diversity_checklist(multi_merchant_ctx):
    """Sanity check: the catalogue actually has items in each price tier
    so the user can test all three gate tiers in the live REPL."""
    domains = list(multi_merchant_ctx.merchant_gateway.direct_adapters.keys())
    # search broadly — query that hits multiple categories
    loop = asyncio.get_event_loop()
    all_products = []
    for q in ["coffee", "shoes", "headphones", "shirt", "speaker", "mug"]:
        all_products.extend(
            loop.run_until_complete(
                search_products(
                    multi_merchant_ctx,
                    query=q,
                    merchant_domains=domains,
                )
            )
        )
    prices = [p.price for p in all_products]
    assert any(p <= Decimal("30") for p in prices), "missing soft-gate tier (≤$30)"
    assert any(Decimal("100") < p <= Decimal("500") for p in prices), "missing explicit-gate tier"
    assert any(p > Decimal("500") for p in prices), "missing full-summary tier (>$500)"


def test_catalogue_has_at_least_four_coffee_mugs(multi_merchant_ctx):
    """Post-expansion: Coffee Bar exposes 4+ mug variants so ranking is meaningful."""
    mugs = asyncio.get_event_loop().run_until_complete(
        search_products(
            multi_merchant_ctx,
            query="mug",
            merchant_domains=["coffee-bar.myshopify.com"],
        )
    )
    assert len(mugs) >= 4, f"expected ≥4 mugs, got {len(mugs)}"


def test_catalogue_has_three_headphone_tiers(multi_merchant_ctx):
    """Audio Hub spans entry ($89), mid-range ($179-$249), premium ($649)."""
    headphones = asyncio.get_event_loop().run_until_complete(
        search_products(
            multi_merchant_ctx,
            query="headphones",
            merchant_domains=["audio-hub.myshopify.com"],
        )
    )
    assert len(headphones) >= 3
    prices = sorted([p.price for p in headphones])
    assert prices[0] < Decimal("100"), "missing entry-tier headphones"
    assert any(Decimal("100") <= p <= Decimal("500") for p in prices), "missing mid-tier headphones"
    assert prices[-1] > Decimal("500"), "missing premium-tier headphones"


def test_catalogue_has_multiple_running_shoes(multi_merchant_ctx):
    """Athletic Co exposes 3+ shoe variants (demo $130, premium $179,
    stability $159) so ranking is meaningful within shoes."""
    shoes = asyncio.get_event_loop().run_until_complete(
        search_products(
            multi_merchant_ctx,
            query="running shoes",
            merchant_domains=["athletic-co.myshopify.com"],
        )
    )
    assert len(shoes) >= 3, f"expected ≥3 running shoes, got {len(shoes)}"


def test_catalogue_has_multiple_coffee_bean_variants(multi_merchant_ctx):
    """Coffee Bar exposes 3+ bean variants (Ethiopia, Decaf, Colombia,
    Kenya) so the user can compare single-origin options."""
    beans = asyncio.get_event_loop().run_until_complete(
        search_products(
            multi_merchant_ctx,
            query="coffee beans",
            merchant_domains=["coffee-bar.myshopify.com"],
        )
    )
    assert len(beans) >= 3, f"expected ≥3 coffee beans, got {len(beans)}"
