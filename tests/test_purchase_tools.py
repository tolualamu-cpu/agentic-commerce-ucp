"""Purchase tools: the full checkout chain + payment-isolation invariant."""

from __future__ import annotations

import asyncio
from decimal import Decimal

from models.order import BuyerInfo
from models.product import CartItem
from tools.purchase_tools import (
    complete_order,
    create_checkout_session,
    get_payment_token,
    record_mandate_spend,
    save_order,
    update_checkout_session,
    validate_mandate,
)


def _new_mandate(ctx, **overrides):
    kwargs = dict(
        user_id="user_1",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        payment_method_id="pm_test_secret",
    )
    kwargs.update(overrides)
    return ctx.ap2.create_mandate(**kwargs)


def test_validate_mandate_passes(tool_ctx):
    m = _new_mandate(tool_ctx)
    r = asyncio.get_event_loop().run_until_complete(
        validate_mandate(
            tool_ctx,
            mandate_id=m.mandate_id,
            amount=Decimal("100"),
        )
    )
    assert r.authorized


def test_get_payment_token_returns_dict_without_payment_method_id(tool_ctx):
    m = _new_mandate(tool_ctx)
    result = asyncio.get_event_loop().run_until_complete(
        get_payment_token(
            tool_ctx,
            mandate_id=m.mandate_id,
            amount=Decimal("100"),
            vendor="demo-shop.myshopify.com",
        )
    )
    assert result["authorized"] is True
    assert result["token"].startswith("tok_test_")
    # Critical: pm_secret_value never appears in agent-visible output
    blob = repr(result)
    assert "pm_test_secret" not in blob
    assert "payment_method_id" not in result


def test_get_payment_token_unauthorised_returns_reason(tool_ctx):
    m = _new_mandate(tool_ctx, max_amount=Decimal("50"))
    result = asyncio.get_event_loop().run_until_complete(
        get_payment_token(
            tool_ctx,
            mandate_id=m.mandate_id,
            amount=Decimal("100"),
        )
    )
    assert result["authorized"] is False
    assert result["reason"] == "exceeds_per_transaction_cap"
    assert "token" not in result


def test_full_purchase_chain(tool_ctx):
    """End-to-end Phase 2 integration: create → update → token → complete → save → record."""
    loop = asyncio.get_event_loop()
    m = _new_mandate(tool_ctx)
    merchant = "demo-shop.myshopify.com"

    # 1. Create session
    session = loop.run_until_complete(
        create_checkout_session(
            tool_ctx,
            merchant_domain=merchant,
            mandate_id=m.mandate_id,
        )
    )
    assert session is not None
    assert tool_ctx.session.get_open_session(merchant) == session.session_id

    # 2. Update with items
    items = [CartItem(product_id="shop_001", name="Shoes", price=Decimal("100"), quantity=1)]
    buyer = BuyerInfo(name="Alex", shipping_address={"city": "SF", "country": "US"})
    session = loop.run_until_complete(
        update_checkout_session(
            tool_ctx,
            session_id=session.session_id,
            merchant_domain=merchant,
            items=items,
            buyer=buyer,
            mandate_id=m.mandate_id,
        )
    )
    assert session.subtotal == Decimal("100")

    # 3. Get payment token
    token_result = loop.run_until_complete(
        get_payment_token(
            tool_ctx,
            mandate_id=m.mandate_id,
            amount=session.total,
            vendor=merchant,
            merchant_domain=merchant,
        )
    )
    assert token_result["authorized"]

    # 4. Complete
    order = loop.run_until_complete(
        complete_order(
            tool_ctx,
            session_id=session.session_id,
            merchant_domain=merchant,
            payment_handler_id="stripe",
            payment_token=token_result["token"],
            mandate_id=m.mandate_id,
        )
    )
    assert order is not None
    assert order.mandate_id == m.mandate_id
    assert tool_ctx.session.get_open_session(merchant) is None  # cleared

    # 5. Save + record
    loop.run_until_complete(save_order(tool_ctx, order=order))
    loop.run_until_complete(
        record_mandate_spend(
            tool_ctx,
            mandate_id=m.mandate_id,
            amount=order.total,
            order_id=order.order_id,
            vendor=merchant,
        )
    )

    # Order persisted
    assert len(tool_ctx.db.orders.all()) == 1
    # Spend recorded — future caps should account for it
    assert len(tool_ctx.db.spend_records.all()) == 1
    # Audit trail captured every step
    audit_actions = {r["tool"] for r in tool_ctx.db.audit_log.all()}
    assert {
        "create_checkout_session",
        "update_checkout_session",
        "get_payment_token",
        "complete_order",
    } <= audit_actions


def test_unknown_merchant_returns_none(tool_ctx):
    m = _new_mandate(tool_ctx)
    session = asyncio.get_event_loop().run_until_complete(
        create_checkout_session(
            tool_ctx,
            merchant_domain="never-heard-of.com",
            mandate_id=m.mandate_id,
        )
    )
    assert session is None


def test_update_session_injects_default_buyer_when_omitted(tool_ctx):
    """Tool layer auto-fills BuyerInfo from user.default_shipping() if the
    agent omits ``buyer`` — closes the 'order placed with no address' bug."""
    from models.user import Address

    tool_ctx.user.addresses = [
        Address(
            line1="42 Test Lane",
            city="Brooklyn",
            region="NY",
            postal_code="11201",
            country="US",
            is_default_shipping=True,
        )
    ]
    m = _new_mandate(tool_ctx)
    merchant = "demo-shop.myshopify.com"
    loop = asyncio.get_event_loop()
    session = loop.run_until_complete(
        create_checkout_session(
            tool_ctx,
            merchant_domain=merchant,
            mandate_id=m.mandate_id,
        )
    )
    # Call update WITHOUT buyer — tool should inject it
    session = loop.run_until_complete(
        update_checkout_session(
            tool_ctx,
            session_id=session.session_id,
            merchant_domain=merchant,
            items=[
                CartItem(
                    product_id="shop_001",
                    name="Shoes",
                    price=Decimal("100"),
                    quantity=1,
                )
            ],
            mandate_id=m.mandate_id,
        )
    )
    # The session reflects what the merchant received — check the audit row
    audit_with_buyer = [
        r for r in tool_ctx.db.audit_log.all() if r.get("tool") == "update_checkout_session"
    ]
    assert audit_with_buyer, "expected an update_checkout_session audit row"
    assert audit_with_buyer[-1]["args"]["has_buyer"] is True


def test_update_session_no_buyer_when_no_address(tool_ctx):
    """If neither agent nor user supply an address, BuyerInfo stays None.
    The stub adapter doesn't crash, but the audit row records the absence."""
    tool_ctx.user.addresses = []  # ensure empty
    m = _new_mandate(tool_ctx)
    merchant = "demo-shop.myshopify.com"
    loop = asyncio.get_event_loop()
    session = loop.run_until_complete(
        create_checkout_session(
            tool_ctx,
            merchant_domain=merchant,
            mandate_id=m.mandate_id,
        )
    )
    session = loop.run_until_complete(
        update_checkout_session(
            tool_ctx,
            session_id=session.session_id,
            merchant_domain=merchant,
            items=[
                CartItem(
                    product_id="shop_001",
                    name="Shoes",
                    price=Decimal("100"),
                    quantity=1,
                )
            ],
            mandate_id=m.mandate_id,
        )
    )
    audit = [r for r in tool_ctx.db.audit_log.all() if r.get("tool") == "update_checkout_session"]
    assert audit[-1]["args"]["has_buyer"] is False
