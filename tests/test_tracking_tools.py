"""Tracking tools: order status, returns, refund lookup."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from models.order import OrderStatus, PurchaseOrder
from models.product import CartItem
from tools.tracking_tools import (
    check_refund_status,
    get_order_status,
    initiate_return,
)


def _seed_order(ctx, **overrides) -> PurchaseOrder:
    base = dict(
        order_id="ord_abc",
        session_id="sess_1",
        merchant_domain="demo-shop.myshopify.com",
        items=[CartItem(product_id="p1", name="Shoe", price=Decimal("50"))],
        total=Decimal("50"),
        mandate_id="m_1",
        payment_intent_id="pi_test_xyz",
        created_at=datetime.now(timezone.utc),
    )
    base.update(overrides)
    order = PurchaseOrder(**base)
    ctx.db.orders.insert(order.model_dump(mode="json"))
    return order


def test_get_order_status_polls_merchant(tool_ctx):
    info = asyncio.get_event_loop().run_until_complete(
        get_order_status(
            tool_ctx,
            order_id="ord_doesnt_exist_at_merchant",
            merchant_domain="demo-shop.myshopify.com",
        )
    )
    # Stub returns {"status": "pending"} for unknown orders
    assert info is not None
    assert info.status == OrderStatus.PENDING


def test_initiate_return_requires_known_order(tool_ctx):
    _seed_order(tool_ctx)
    r = asyncio.get_event_loop().run_until_complete(
        initiate_return(
            tool_ctx,
            order_id="ord_abc",
            merchant_domain="demo-shop.myshopify.com",
            items=[{"product_id": "p1", "quantity": 1}],
            reason="defective",
        )
    )
    assert r["accepted"] is True
    assert r["status"] == "submitted"


def test_initiate_return_rejects_unknown_order(tool_ctx):
    r = asyncio.get_event_loop().run_until_complete(
        initiate_return(
            tool_ctx,
            order_id="ord_does_not_exist",
            merchant_domain="demo-shop.myshopify.com",
            items=[],
            reason="x",
        )
    )
    assert r["accepted"] is False
    assert r["reason"] == "order_not_found"


def test_check_refund_status_finds_by_intent(tool_ctx):
    _seed_order(tool_ctx, status=OrderStatus.REFUNDED)
    r = asyncio.get_event_loop().run_until_complete(
        check_refund_status(
            tool_ctx,
            payment_intent_id="pi_test_xyz",
        )
    )
    assert r["order_id"] == "ord_abc"
    assert r["status"] == "refunded"


def test_check_refund_status_unknown(tool_ctx):
    r = asyncio.get_event_loop().run_until_complete(
        check_refund_status(
            tool_ctx,
            payment_intent_id="pi_nope",
        )
    )
    assert r["status"] == "unknown"


def test_tracking_actions_are_audited(tool_ctx):
    _seed_order(tool_ctx)
    asyncio.get_event_loop().run_until_complete(
        initiate_return(
            tool_ctx,
            order_id="ord_abc",
            merchant_domain="demo-shop.myshopify.com",
            items=[],
            reason="x",
        )
    )
    actions = {r["tool"] for r in tool_ctx.db.audit_log.all()}
    assert "initiate_return" in actions
