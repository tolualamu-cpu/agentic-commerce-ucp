"""Smoke tests for canonical models."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from models import (
    AgentMandate,
    CartItem,
    CheckoutSession,
    CheckoutStatus,
    ProductResult,
    UCPProfile,
    UCPService,
    UserProfile,
)


def test_user_profile_strips_payment_method():
    u = UserProfile(user_id="u1", name="Alex", payment_method_id="pm_secret_123")
    safe = u.agent_safe_view()
    assert "payment_method_id" not in safe


def test_ucp_profile_capability_lookup():
    p = UCPProfile(
        merchant_domain="x.com",
        services=[UCPService(type="rest", spec_url="https://x.com/spec")],
    )
    assert p.preferred_transport() == "rest"
    assert p.has_capability("dev.ucp.shopping.checkout") is False


def test_cart_item_line_total():
    i = CartItem(product_id="p1", name="Shoe", price=Decimal("50"), quantity=3)
    assert i.line_total == Decimal("150")


def test_product_confidence_score_bounds():
    p = ProductResult(
        product_id="p",
        name="x",
        price=Decimal("1"),
        merchant="m",
        merchant_domain="m.com",
        confidence_score=0.5,
    )
    assert 0 <= p.confidence_score <= 1


def test_mandate_status_transitions():
    now = datetime.now(timezone.utc)
    m = AgentMandate(
        mandate_id="m1",
        user_id="u",
        max_amount=Decimal("100"),
        daily_cap=Decimal("500"),
        monthly_cap=Decimal("2000"),
        created_at=now,
        expiry=now + timedelta(hours=1),
    )
    assert m.is_active().name == "ACTIVE"
    m2 = m.model_copy(update={"revoked": True})
    assert m2.is_active().name == "REVOKED"
    m3 = m.model_copy(update={"expiry": now - timedelta(hours=1)})
    assert m3.is_active().name == "EXPIRED"


def test_checkout_session_defaults():
    s = CheckoutSession(
        session_id="s1", merchant_domain="x.com", created_at=datetime.now(timezone.utc)
    )
    assert s.status == CheckoutStatus.OPEN
    assert s.subtotal == Decimal("0")
