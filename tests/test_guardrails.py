"""Guardrail tests: SpendingLimiter, VendorGate, ConfidenceChecker."""

from __future__ import annotations

from decimal import Decimal

from guardrails.confidence import ConfidenceChecker
from guardrails.spending import SpendingLimiter
from guardrails.vendors import VendorGate
from models.user import UserProfile


def test_spending_limiter_passes_under_cap(ap2, tmp_db):
    m = ap2.create_mandate(
        "u",
        max_amount=Decimal("200"),
        daily_cap=Decimal("500"),
        monthly_cap=Decimal("2000"),
    )
    limiter = SpendingLimiter(tmp_db)
    r = limiter.check(m.mandate_id, Decimal("50"))
    assert r.authorized


def test_spending_limiter_blocks_over_per_tx(ap2, tmp_db):
    m = ap2.create_mandate(
        "u",
        max_amount=Decimal("100"),
        daily_cap=Decimal("500"),
        monthly_cap=Decimal("2000"),
    )
    limiter = SpendingLimiter(tmp_db)
    r = limiter.check(m.mandate_id, Decimal("150"))
    assert not r.authorized
    assert r.reason == "exceeds_per_transaction_cap"


def test_spending_limiter_aggregates_daily(ap2, tmp_db):
    m = ap2.create_mandate(
        "u",
        max_amount=Decimal("500"),
        daily_cap=Decimal("100"),
        monthly_cap=Decimal("5000"),
    )
    ap2.record_spend(m.mandate_id, Decimal("90"), "ord1", vendor="x")
    limiter = SpendingLimiter(tmp_db)
    r = limiter.check(m.mandate_id, Decimal("20"))
    assert not r.authorized
    assert r.reason == "exceeds_daily_cap"


def test_vendor_gate_user_blocklist():
    user = UserProfile(user_id="u", name="U", vendor_blocklist=["bad.com"])
    gate = VendorGate(user)
    assert gate.check("bad.com").allowed is False
    assert gate.check("good.com").allowed is True


def test_vendor_gate_user_allowlist_restricts():
    user = UserProfile(user_id="u", name="U", vendor_allowlist=["nike.com"])
    gate = VendorGate(user)
    assert gate.check("nike.com").allowed is True
    assert gate.check("adidas.com").allowed is False


def test_vendor_gate_case_insensitive():
    user = UserProfile(user_id="u", name="U", vendor_blocklist=["BAD.COM"])
    assert VendorGate(user).check("bad.com").allowed is False


def test_confidence_checker_above_threshold():
    c = ConfidenceChecker(threshold=0.8)
    d = c.check(0.9)
    assert d.pass_ is True
    assert d.escalate is False


def test_confidence_checker_below_threshold_escalates():
    c = ConfidenceChecker(threshold=0.8)
    d = c.check(0.6)
    assert d.pass_ is False
    assert d.escalate is True
