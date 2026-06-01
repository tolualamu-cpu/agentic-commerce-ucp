"""Smoke tests for cli/display.py — every renderer survives realistic + minimal inputs.

These don't assert exact ANSI output (brittle and uninteresting). They assert
"the function ran without raising" — which catches the kind of bugs you'd
otherwise only find by re-running main.py manually.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from rich.console import Console

from cli.confirmation import GateData
from cli.display import (
    RichConfirmProvider,
    _fmt_dt,
    _pct,
    _short_dt,
    display_checkout_summary,
    display_mandate_status,
    display_order,
    display_orders,
    display_products,
    display_profile,
    display_tracking,
    display_welcome,
)
from models.mandate import AgentMandate
from models.order import (
    CheckoutSession,
    CheckoutStatus,
    OrderStatus,
    PurchaseOrder,
    TrackingInfo,
)
from models.product import CartItem, ProductResult, RankedProduct
from models.ucp_profile import PaymentHandler
from models.user import Address, UserProfile


# ─── Helpers ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def silent_console(monkeypatch):
    """Redirect Rich's shared console to /dev/null so test output stays clean."""
    from cli import display

    quiet = Console(file=io.StringIO(), record=True, width=120)
    monkeypatch.setattr(display, "console", quiet)
    return quiet


def _now():
    return datetime.now(timezone.utc)


def _mandate():
    return AgentMandate(
        mandate_id="m_test",
        user_id="u",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        created_at=_now(),
        expiry=_now(),
    )


def _product(name="Shoe", price="100", **kw):
    base = dict(
        product_id="p1",
        name=name,
        price=Decimal(price),
        merchant="Brand",
        merchant_domain="brand.com",
        in_stock=True,
    )
    base.update(kw)
    return ProductResult(**base)


# ─── Welcome / mandate / profile ────────────────────────────────────────────


def test_display_welcome_runs(silent_console):
    display_welcome("Alex", _mandate(), Decimal("0"), Decimal("0"))


def test_display_mandate_status_with_zero_spend(silent_console):
    display_mandate_status(_mandate(), Decimal("0"), Decimal("0"))


def test_display_mandate_status_with_partial_spend(silent_console):
    display_mandate_status(_mandate(), Decimal("250"), Decimal("1750"))


def test_display_mandate_status_revoked(silent_console):
    m = _mandate().model_copy(update={"revoked": True})
    display_mandate_status(m, Decimal("0"), Decimal("0"))


def test_display_profile_with_full_data(silent_console):
    user = UserProfile(
        user_id="u",
        name="Alex",
        payment_method_id="pm_test",
        preferred_categories=["running"],
        addresses=[
            Address(
                line1="1 Main St",
                city="SF",
                region="CA",
                postal_code="94110",
                country="US",
                is_default_shipping=True,
            )
        ],
        vendor_blocklist=["bad.com"],
    )
    display_profile(user)


def test_display_profile_minimal(silent_console):
    """User with no addresses, no categories, no payment method."""
    user = UserProfile(user_id="u", name="Alex")
    display_profile(user)


# ─── Product cards ──────────────────────────────────────────────────────────


def test_display_products_empty_list(silent_console):
    display_products([])


def test_display_products_in_stock(silent_console):
    ranked = [RankedProduct(product=_product(), score=0.8, rank=1, rationale="best fit")]
    display_products(ranked)


def test_display_products_with_risk_flags(silent_console):
    p = _product(in_stock=False, confidence_score=0.5)
    ranked = [
        RankedProduct(product=p, score=0.4, rank=1, risk_flags=["OUT_OF_STOCK", "LOW_CONFIDENCE"])
    ]
    display_products(ranked)


# ─── Checkout / order / tracking ────────────────────────────────────────────


def test_display_checkout_summary_with_items(silent_console):
    s = CheckoutSession(
        session_id="s1",
        merchant_domain="shop.com",
        line_items=[CartItem(product_id="p1", name="X", price=Decimal("50"), quantity=2)],
        subtotal=Decimal("100"),
        tax=Decimal("8"),
        total=Decimal("108"),
        currency="USD",
        status=CheckoutStatus.OPEN,
        payment_handlers=[PaymentHandler(id="stripe", name="Stripe", spec_url="x")],
        created_at=_now(),
    )
    display_checkout_summary(s)


def test_display_checkout_summary_with_discount_and_shipping(silent_console):
    s = CheckoutSession(
        session_id="s1",
        merchant_domain="shop.com",
        line_items=[CartItem(product_id="p1", name="X", price=Decimal("50"))],
        subtotal=Decimal("50"),
        discount=Decimal("5"),
        tax=Decimal("4"),
        shipping=Decimal("8"),
        total=Decimal("57"),
        currency="USD",
        status=CheckoutStatus.OPEN,
        created_at=_now(),
    )
    display_checkout_summary(s)


def test_display_order_full_fields(silent_console):
    order = PurchaseOrder(
        order_id="ord_x",
        session_id="s1",
        merchant_domain="shop.com",
        items=[CartItem(product_id="p1", name="X", price=Decimal("50"))],
        total=Decimal("57"),
        status=OrderStatus.CONFIRMED,
        mandate_id="m_test",
        payment_intent_id="pi_test_xyz",
        tracking_number="TRK123",
        estimated_delivery="2-3 days",
        created_at=_now(),
    )
    display_order(order)


def test_display_order_minimal(silent_console):
    order = PurchaseOrder(
        order_id="ord_y",
        session_id="s1",
        merchant_domain="shop.com",
        items=[CartItem(product_id="p1", name="X", price=Decimal("50"))],
        total=Decimal("50"),
        mandate_id="m_test",
        created_at=_now(),
    )
    display_order(order)


def test_display_tracking_each_status(silent_console):
    for status in (
        OrderStatus.PENDING,
        OrderStatus.CONFIRMED,
        OrderStatus.SHIPPED,
        OrderStatus.DELIVERED,
        OrderStatus.CANCELLED,
        OrderStatus.REFUNDED,
    ):
        display_tracking(
            TrackingInfo(
                order_id="ord_t",
                status=status,
                last_updated=_now(),
            )
        )


def test_display_tracking_with_all_fields(silent_console):
    display_tracking(
        TrackingInfo(
            order_id="ord_t",
            status=OrderStatus.SHIPPED,
            tracking_number="TRK999",
            carrier="UPS",
            estimated_delivery="2 days",
            last_event="picked up",
            last_updated=_now(),
        )
    )


def test_display_orders_empty(silent_console):
    display_orders([])


def test_display_orders_with_rows(silent_console):
    display_orders(
        [
            {
                "order_id": "ord_1",
                "merchant_domain": "x.com",
                "total": "50",
                "status": "confirmed",
                "created_at": _now().isoformat(),
            },
        ]
    )


# ─── Helper functions ───────────────────────────────────────────────────────


def test_fmt_dt_handles_none():
    assert _fmt_dt(None) == "—"


def test_fmt_dt_handles_string():
    result = _fmt_dt("2026-05-13T12:34:56")
    assert "2026" in result


def test_short_dt_handles_empty():
    assert _short_dt("") == "—"


def test_pct_returns_zero_for_no_cap():
    assert _pct(Decimal("10"), Decimal("0")) == ""


def test_pct_computes_percentage():
    assert "50%" in _pct(Decimal("50"), Decimal("100"))


# ─── RichConfirmProvider ────────────────────────────────────────────────────


def test_explicit_confirm_classifies_inputs(silent_console, monkeypatch):
    """Approves on CONFIRM (case-insensitive). Cancel words → cancel.
    Everything else → question with the raw text preserved."""
    import asyncio

    provider = RichConfirmProvider()
    gate = GateData(
        merchant_domain="x.com", amount=Decimal("100"), currency="USD", item_summary="x"
    )

    cases = [
        # (answer, expected_decision, expected_text_or_None)
        ("CONFIRM", "confirm", None),
        ("confirm", "confirm", None),
        ("Confirm", "confirm", None),
        ("  CONFIRM  ", "confirm", None),
        ("no", "cancel", None),
        ("cancel", "cancel", None),
        ("stop", "cancel", None),
        ("", "cancel", None),
        # Anything else is a question and the raw text is preserved
        ("why did you pick this?", "question", "why did you pick this?"),
        ("rank the mugs", "question", "rank the mugs"),
    ]
    for answer, expected_decision, expected_text in cases:
        from cli import display

        monkeypatch.setattr(display.Prompt, "ask", lambda *a, _v=answer, **kw: _v)
        result = asyncio.get_event_loop().run_until_complete(provider.explicit_confirm(gate))
        assert result.decision == expected_decision, f"answer={answer!r} got {result.decision}"
        if expected_text is not None:
            assert result.text == expected_text, f"answer={answer!r} text mismatch: {result.text}"


def test_soft_confirm_empty_proceeds(silent_console, monkeypatch):
    """Soft gate: empty input means proceed (press Enter)."""
    import asyncio

    provider = RichConfirmProvider()
    gate = GateData(
        merchant_domain="x.com",
        amount=Decimal("20"),
        currency="USD",
        item_summary="mug",
    )
    from cli import display

    monkeypatch.setattr(display.Prompt, "ask", lambda *a, **kw: "")
    result = asyncio.get_event_loop().run_until_complete(provider.soft_confirm(gate))
    assert result.decision == "confirm"


def test_soft_confirm_question_preserved(silent_console, monkeypatch):
    """Soft gate: free text becomes a question, not silent cancel."""
    import asyncio

    provider = RichConfirmProvider()
    gate = GateData(
        merchant_domain="x.com",
        amount=Decimal("20"),
        currency="USD",
        item_summary="mug",
    )
    from cli import display

    monkeypatch.setattr(display.Prompt, "ask", lambda *a, **kw: "tell me more")
    result = asyncio.get_event_loop().run_until_complete(provider.soft_confirm(gate))
    assert result.decision == "question"
    assert result.text == "tell me more"


def test_soft_confirm_no_words_still_cancel(silent_console, monkeypatch):
    import asyncio

    provider = RichConfirmProvider()
    gate = GateData(
        merchant_domain="x.com",
        amount=Decimal("20"),
        currency="USD",
        item_summary="mug",
    )
    from cli import display

    for word in ("no", "N", "cancel", "stop"):
        monkeypatch.setattr(display.Prompt, "ask", lambda *a, _v=word, **kw: _v)
        result = asyncio.get_event_loop().run_until_complete(provider.soft_confirm(gate))
        assert result.decision == "cancel", f"word {word!r} should cancel"
