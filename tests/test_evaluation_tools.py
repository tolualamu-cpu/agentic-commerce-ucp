"""Evaluation tools: ranking weights + scoring sanity."""

from __future__ import annotations

import asyncio
from decimal import Decimal

from models.product import ProductResult
from models.user import UserProfile
from tools.evaluation_tools import (
    WEIGHTS,
    check_vendor_allowlist,
    compare_prices,
    fetch_reviews,
    rank_products,
)


def _product(name: str, price: str, **kw) -> ProductResult:
    base = dict(
        product_id=name.replace(" ", "_"),
        name=name,
        price=Decimal(price),
        currency="USD",
        merchant="Shop",
        merchant_domain="shop.com",
        in_stock=True,
    )
    base.update(kw)
    return ProductResult(**base)


def test_weights_sum_to_one():
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_ranking_prefers_cheaper_when_other_factors_equal(tool_ctx):
    cheap = _product("Shoe A", "50")
    pricey = _product("Shoe B", "200")
    ranked = asyncio.get_event_loop().run_until_complete(
        rank_products(
            tool_ctx,
            products=[pricey, cheap],
        )
    )
    assert ranked[0].product.product_id == "Shoe_A"
    assert ranked[0].rank == 1


def test_ranking_flags_out_of_stock(tool_ctx):
    oos = _product("Shoe X", "100", in_stock=False)
    ranked = asyncio.get_event_loop().run_until_complete(
        rank_products(
            tool_ctx,
            products=[oos],
        )
    )
    assert "OUT_OF_STOCK" in ranked[0].risk_flags


def test_ranking_flags_low_confidence(tool_ctx):
    low = _product("Shoe X", "100", confidence_score=0.5)
    ranked = asyncio.get_event_loop().run_until_complete(
        rank_products(
            tool_ctx,
            products=[low],
        )
    )
    assert "LOW_CONFIDENCE" in ranked[0].risk_flags


def test_ranking_respects_user_allowlist(tool_ctx):
    """At equal price, allowlisted merchant ranks above non-allowlisted (trust 20%)."""
    user = UserProfile(user_id="u", name="U", vendor_allowlist=["nike.com"])
    nike = _product("Nike Shoe", "100", merchant_domain="nike.com")
    other = _product("Other Shoe", "100", merchant_domain="other.com")
    ranked = asyncio.get_event_loop().run_until_complete(
        rank_products(
            tool_ctx,
            products=[other, nike],
            user=user,
        )
    )
    assert ranked[0].product.merchant_domain == "nike.com"
    assert ranked[1].product.merchant_domain == "other.com"


def test_empty_input_returns_empty(tool_ctx):
    ranked = asyncio.get_event_loop().run_until_complete(
        rank_products(
            tool_ctx,
            products=[],
        )
    )
    assert ranked == []


def test_fetch_reviews_summary(tool_ctx):
    summary = asyncio.get_event_loop().run_until_complete(
        fetch_reviews(
            tool_ctx,
            product_id="shop_001",
            merchant_domain="demo-shop.myshopify.com",
        )
    )
    assert summary["product_id"] == "shop_001"
    assert summary["rating"] == 4.5


def test_check_vendor_allowlist_passes(tool_ctx):
    assert (
        asyncio.get_event_loop().run_until_complete(
            check_vendor_allowlist(
                tool_ctx,
                merchant_domain="demo-shop.myshopify.com",
            )
        )
        is True
    )


def test_check_vendor_allowlist_blocks(tool_ctx):
    tool_ctx.user.vendor_blocklist = ["demo-shop.myshopify.com"]
    assert (
        asyncio.get_event_loop().run_until_complete(
            check_vendor_allowlist(
                tool_ctx,
                merchant_domain="demo-shop.myshopify.com",
            )
        )
        is False
    )


def test_compare_prices_sorts_by_price(tool_ctx):
    by_merchant = asyncio.get_event_loop().run_until_complete(
        compare_prices(
            tool_ctx,
            product_name="shoes",
            merchant_domains=["demo-shop.myshopify.com"],
        )
    )
    items = list(by_merchant.values())[0] if by_merchant else []
    if len(items) > 1:
        prices = [Decimal(i["price"]) for i in items]
        assert prices == sorted(prices)
