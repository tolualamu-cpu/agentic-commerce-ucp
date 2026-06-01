"""AP2MandateEngine: create, verify, authorise, revoke, cap enforcement."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal


def test_create_and_sign(ap2):
    m = ap2.create_mandate(
        "user_1",
        max_amount=Decimal("200"),
        daily_cap=Decimal("500"),
        monthly_cap=Decimal("2000"),
    )
    assert m.mandate_id.startswith("mandate_")
    assert m.digital_signature is not None
    assert ap2.verify_signature(m) is True


def test_tampered_signature_detected(ap2):
    m = ap2.create_mandate(
        "user_1",
        max_amount=Decimal("200"),
        daily_cap=Decimal("500"),
        monthly_cap=Decimal("2000"),
    )
    m.max_amount = Decimal("999999")
    assert ap2.verify_signature(m) is False


def test_authorize_happy_path(ap2):
    ap2.create_mandate(
        "user_1",
        max_amount=Decimal("200"),
        daily_cap=Decimal("500"),
        monthly_cap=Decimal("2000"),
    )
    m = list(ap2.db.mandates.all())[0]
    r = ap2.verify_and_authorize(
        m["mandate_id"], Decimal("50"), vendor="shop.com", category="apparel"
    )
    assert r.authorized
    assert r.headroom_per_tx == Decimal("150")
    assert r.headroom_daily == Decimal("450")


def test_per_transaction_cap(ap2):
    m = ap2.create_mandate(
        "u",
        max_amount=Decimal("100"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
    )
    r = ap2.verify_and_authorize(m.mandate_id, Decimal("150"))
    assert not r.authorized
    assert r.reason == "exceeds_per_transaction_cap"


def test_daily_cap_enforced(ap2):
    m = ap2.create_mandate(
        "u",
        max_amount=Decimal("500"),
        daily_cap=Decimal("100"),
        monthly_cap=Decimal("5000"),
    )
    ap2.record_spend(m.mandate_id, Decimal("80"), "ord1", vendor="x")
    r = ap2.verify_and_authorize(m.mandate_id, Decimal("50"))
    assert not r.authorized
    assert r.reason == "exceeds_daily_cap"


def test_monthly_cap_enforced(ap2):
    m = ap2.create_mandate(
        "u",
        max_amount=Decimal("500"),
        daily_cap=Decimal("10000"),
        monthly_cap=Decimal("100"),
    )
    ap2.record_spend(m.mandate_id, Decimal("80"), "ord1", vendor="x")
    r = ap2.verify_and_authorize(m.mandate_id, Decimal("50"))
    assert not r.authorized
    assert r.reason == "exceeds_monthly_cap"


def test_vendor_blocked(ap2):
    m = ap2.create_mandate(
        "u",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        blocked_vendors=["sketchy.com"],
    )
    r = ap2.verify_and_authorize(m.mandate_id, Decimal("50"), vendor="sketchy.com")
    assert not r.authorized
    assert r.reason == "vendor_blocked"


def test_vendor_allowlist(ap2):
    m = ap2.create_mandate(
        "u",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        allowed_vendors=["nike.com"],
    )
    ok = ap2.verify_and_authorize(m.mandate_id, Decimal("50"), vendor="nike.com")
    bad = ap2.verify_and_authorize(m.mandate_id, Decimal("50"), vendor="adidas.com")
    assert ok.authorized
    assert not bad.authorized
    assert bad.reason == "vendor_not_allowed"


def test_category_allowlist(ap2):
    m = ap2.create_mandate(
        "u",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        allowed_categories=["apparel", "electronics"],
    )
    ok = ap2.verify_and_authorize(m.mandate_id, Decimal("50"), category="apparel")
    bad = ap2.verify_and_authorize(m.mandate_id, Decimal("50"), category="firearms")
    assert ok.authorized
    assert not bad.authorized
    assert bad.reason == "category_not_allowed"


def test_revocation_takes_effect_immediately(ap2):
    m = ap2.create_mandate(
        "u",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
    )
    assert ap2.revoke_mandate(m.mandate_id) is True
    r = ap2.verify_and_authorize(m.mandate_id, Decimal("50"))
    assert not r.authorized
    assert r.reason == "mandate_revoked"


def test_expired_mandate_rejected(ap2):
    m = ap2.create_mandate(
        "u",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        expiry_hours=1,
    )
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    r = ap2.verify_and_authorize(m.mandate_id, Decimal("50"), now=future)
    assert not r.authorized
    assert r.reason == "mandate_expired"


def test_mandate_not_found(ap2):
    r = ap2.verify_and_authorize("mandate_does_not_exist", Decimal("10"))
    assert not r.authorized
    assert r.reason == "mandate_not_found"


def test_spend_records_aggregate_correctly(ap2):
    m = ap2.create_mandate(
        "u",
        max_amount=Decimal("500"),
        daily_cap=Decimal("300"),
        monthly_cap=Decimal("1000"),
    )
    ap2.record_spend(m.mandate_id, Decimal("100"), "ord1", vendor="x")
    ap2.record_spend(m.mandate_id, Decimal("100"), "ord2", vendor="x")
    r = ap2.verify_and_authorize(m.mandate_id, Decimal("150"))
    assert not r.authorized
    assert r.reason == "exceeds_daily_cap"
    r2 = ap2.verify_and_authorize(m.mandate_id, Decimal("99"))
    assert r2.authorized
