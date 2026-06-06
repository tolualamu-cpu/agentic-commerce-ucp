"""Unit tests for the orchestrator's deterministic ``rank_candidates`` tool (Lever 1).

These prove ZERO ranking regression: ``_rank_candidates`` (which reads the
discovery cache and coerces dicts → ProductResult) produces byte-identical
ordering / scores / risk_flags to calling the canonical ``rank_products`` Python
scorer directly. Coverage spans all three demo merchants, cross-merchant mixed
baskets, out-of-stock handling, low-confidence flagging, subset selection, and
the empty-cache edge.

This file sorts alphabetically BEFORE ``test_user_journeys.py`` so per CLAUDE.md
it uses ``asyncio.get_event_loop().run_until_complete()`` (never ``asyncio.run``).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from agents.orchestrator import OrchestratorAgent
from models.product import ProductResult
from tests.fake_anthropic import FakeAnthropicClient
from tools.evaluation_tools import rank_products


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _orch() -> OrchestratorAgent:
    # No scripted responses needed — we call the tool handler directly.
    return OrchestratorAgent(FakeAnthropicClient([]))


def _p(
    pid: str,
    name: str,
    price: str,
    domain: str,
    *,
    in_stock: bool = True,
    confidence: float = 1.0,
    rating: float | None = 4.5,
    reviews: int | None = 120,
) -> ProductResult:
    return ProductResult(
        product_id=pid,
        name=name,
        price=Decimal(price),
        merchant=domain.split(".")[0],
        merchant_domain=domain,
        in_stock=in_stock,
        confidence_score=confidence,
        rating=rating,
        review_count=reviews,
    )


# ─── sample baskets per merchant ─────────────────────────────────────────────

ATHLETIC = "athletic-co.myshopify.com"
AUDIO = "audio-hub.myshopify.com"
COFFEE = "coffee-bar.myshopify.com"


def _athletic_basket() -> list[ProductResult]:
    return [
        _p("ath_001", "Demo Running Shoes", "129.99", ATHLETIC),
        _p("ath_006", "Premium Running Shoes", "179.00", ATHLETIC, rating=4.8, reviews=300),
        _p("ath_003", "Performance Running Shorts", "39.00", ATHLETIC, rating=4.2, reviews=60),
    ]


def _audio_basket() -> list[ProductResult]:
    return [
        _p("aud_001", "Studio Headphones", "89.00", AUDIO),
        _p("aud_002", "Wireless Over-Ear", "249.00", AUDIO, rating=4.7, reviews=210),
        _p("aud_003", "Reference Monitors", "649.00", AUDIO, rating=4.9, reviews=90),
    ]


def _coffee_basket() -> list[ProductResult]:
    return [
        _p("cof_001", "Ceramic Mug", "14.00", COFFEE, rating=4.1, reviews=40),
        _p("cof_002", "Travel Tumbler", "28.00", COFFEE, rating=4.4, reviews=75),
        _p("cof_003", "Ethiopia Beans", "18.00", COFFEE, rating=4.9, reviews=500),
    ]


def _assert_parity(ctx, products: list[ProductResult]):
    """rank_candidates(from-cache-dicts) == rank_products(ProductResult objects)."""
    ctx.session.last_discovered_products = [p.model_dump(mode="json") for p in products]
    orch = _orch()

    got = _run(orch._rank_candidates(ctx))
    truth = _run(rank_products(ctx, products=products))

    # identical ordering
    assert [r["product"]["product_id"] for r in got["ranked"]] == [
        r.product.product_id for r in truth
    ]
    # identical scores + ranks + per-item flags
    for got_r, truth_r in zip(got["ranked"], truth):
        assert got_r["product"]["product_id"] == truth_r.product.product_id
        assert got_r["score"] == truth_r.score
        assert got_r["rank"] == truth_r.rank
        assert got_r["risk_flags"] == truth_r.risk_flags
    return got


def test_parity_single_merchant_athletic(multi_merchant_ctx):
    _assert_parity(multi_merchant_ctx, _athletic_basket())


def test_parity_single_merchant_audio(multi_merchant_ctx):
    _assert_parity(multi_merchant_ctx, _audio_basket())


def test_parity_single_merchant_coffee(multi_merchant_ctx):
    _assert_parity(multi_merchant_ctx, _coffee_basket())


def test_parity_cross_merchant_mixed(multi_merchant_ctx):
    mixed = _athletic_basket()[:1] + _audio_basket()[:1] + _coffee_basket()[:1]
    _assert_parity(multi_merchant_ctx, mixed)


def test_out_of_stock_flagged(multi_merchant_ctx):
    products = [
        _p("ath_001", "Demo Running Shoes", "129.99", ATHLETIC),
        _p("ath_002", "Trail Runner Pro", "189.00", ATHLETIC, in_stock=False),
    ]
    got = _assert_parity(multi_merchant_ctx, products)
    oos = [r for r in got["ranked"] if r["product"]["product_id"] == "ath_002"][0]
    assert "OUT_OF_STOCK" in oos["risk_flags"]
    assert "OUT_OF_STOCK" in got["risk_flags"]


def test_low_confidence_flagged(multi_merchant_ctx):
    products = [
        _p("aud_001", "Studio Headphones", "89.00", AUDIO, confidence=0.55),
        _p("aud_002", "Wireless Over-Ear", "249.00", AUDIO, confidence=0.95),
    ]
    got = _assert_parity(multi_merchant_ctx, products)
    low = [r for r in got["ranked"] if r["product"]["product_id"] == "aud_001"][0]
    assert "LOW_CONFIDENCE" in low["risk_flags"]
    assert "LOW_CONFIDENCE" in got["risk_flags"]


def test_product_ids_subset_selection(multi_merchant_ctx):
    products = _coffee_basket()
    multi_merchant_ctx.session.last_discovered_products = [
        p.model_dump(mode="json") for p in products
    ]
    orch = _orch()
    got = _run(orch._rank_candidates(multi_merchant_ctx, product_ids=["cof_001", "cof_003"]))
    ids = {r["product"]["product_id"] for r in got["ranked"]}
    assert ids == {"cof_001", "cof_003"}  # cof_002 excluded


def test_empty_cache_returns_empty(multi_merchant_ctx):
    multi_merchant_ctx.session.last_discovered_products = []
    orch = _orch()
    got = _run(orch._rank_candidates(multi_merchant_ctx))
    assert got["ranked"] == []
    assert got["risk_flags"] == []
    assert "No products" in got["top_pick_rationale"]


def test_malformed_cache_entries_skipped(multi_merchant_ctx):
    # A junk entry missing required fields must be skipped, not crash.
    good = _p("cof_003", "Ethiopia Beans", "18.00", COFFEE).model_dump(mode="json")
    multi_merchant_ctx.session.last_discovered_products = [good, {"garbage": True}]
    orch = _orch()
    got = _run(orch._rank_candidates(multi_merchant_ctx))
    assert [r["product"]["product_id"] for r in got["ranked"]] == ["cof_003"]


def test_rationale_names_top_pick(multi_merchant_ctx):
    products = _athletic_basket()
    multi_merchant_ctx.session.last_discovered_products = [
        p.model_dump(mode="json") for p in products
    ]
    orch = _orch()
    got = _run(orch._rank_candidates(multi_merchant_ctx))
    top_name = got["ranked"][0]["product"]["name"]
    assert top_name in got["top_pick_rationale"]
