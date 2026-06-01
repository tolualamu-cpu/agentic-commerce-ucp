"""Discovery tools: search + product details with vendor gating."""

from __future__ import annotations

import asyncio

from tools.discovery_tools import get_product_details, search_products


def test_search_fans_out_across_merchants(tool_ctx):
    results = asyncio.get_event_loop().run_until_complete(
        search_products(
            tool_ctx,
            query="shoes",
            merchant_domains=["demo-shop.myshopify.com"],
        )
    )
    assert len(results) >= 1
    assert results[0].merchant_domain == "demo-shop.myshopify.com"


def test_search_silently_drops_blocklisted_merchants(tool_ctx):
    tool_ctx.user.vendor_blocklist = ["demo-shop.myshopify.com"]
    results = asyncio.get_event_loop().run_until_complete(
        search_products(
            tool_ctx,
            query="shoes",
            merchant_domains=["demo-shop.myshopify.com"],
        )
    )
    # Blocklisted merchant filtered silently — caller doesn't learn the reason
    assert results == []


def test_search_writes_audit_entry(tool_ctx):
    asyncio.get_event_loop().run_until_complete(
        search_products(
            tool_ctx,
            query="shoes",
            merchant_domains=["demo-shop.myshopify.com"],
        )
    )
    rows = tool_ctx.db.audit_log.all()
    assert any(r["tool"] == "search_products" for r in rows)


def test_get_product_details_returns_product(tool_ctx):
    p = asyncio.get_event_loop().run_until_complete(
        get_product_details(
            tool_ctx,
            product_id="shop_001",
            merchant_domain="demo-shop.myshopify.com",
        )
    )
    assert p is not None
    assert p.product_id == "shop_001"


def test_get_product_details_returns_none_for_blocked(tool_ctx):
    tool_ctx.user.vendor_blocklist = ["demo-shop.myshopify.com"]
    p = asyncio.get_event_loop().run_until_complete(
        get_product_details(
            tool_ctx,
            product_id="shop_001",
            merchant_domain="demo-shop.myshopify.com",
        )
    )
    assert p is None
