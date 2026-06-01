"""ShopifyMCPAdapter against the StubShopifyTransport."""

from __future__ import annotations

import asyncio
from decimal import Decimal

from adapters.shopify_mcp import ShopifyMCPAdapter, StubShopifyTransport
from models.order import OrderStatus
from models.product import CartItem


def _adapter() -> ShopifyMCPAdapter:
    return ShopifyMCPAdapter("demo-shop.myshopify.com", StubShopifyTransport())


def test_search_returns_ucp_types():
    a = _adapter()
    results = asyncio.get_event_loop().run_until_complete(a.search_products("running shoes"))
    assert len(results) >= 1
    assert results[0].source_protocol == "shopify_mcp"
    assert results[0].merchant_domain == "demo-shop.myshopify.com"


def test_checkout_lifecycle():
    a = _adapter()
    loop = asyncio.get_event_loop()
    session = loop.run_until_complete(a.create_checkout_session())
    session = loop.run_until_complete(
        a.update_checkout_session(
            session.session_id,
            items=[
                CartItem(
                    product_id="shop_001",
                    name="Demo Shoes",
                    price=Decimal("129.99"),
                    quantity=1,
                )
            ],
        )
    )
    assert session.subtotal == Decimal("129.99")
    assert session.total > Decimal("129.99")  # includes tax

    order = loop.run_until_complete(
        a.complete_checkout(
            session.session_id,
            "stripe",
            "tok_test_xyz",
        )
    )
    assert order.status == OrderStatus.CONFIRMED
    assert order.merchant_domain == "demo-shop.myshopify.com"
    assert order.payment_intent_id is not None
