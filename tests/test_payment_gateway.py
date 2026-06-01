"""PaymentGateway: mandate → token resolution. Verifies payment isolation."""

from __future__ import annotations

from decimal import Decimal

from adapters.stripe import StripeAdapter
from gateway.payment_gateway import PaymentGateway


def test_authorised_purchase_returns_token(ap2):
    m = ap2.create_mandate(
        "u",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        payment_method_id="pm_test_card_visa",
    )
    pg = PaymentGateway(ap2, StripeAdapter(api_key=None))
    r = pg.get_payment_token(m.mandate_id, Decimal("100"), vendor="shop.com")
    assert r.authorized
    assert r.token is not None
    assert r.token.token.startswith("tok_test_")
    assert r.token.payment_intent_id.startswith("pi_test_")


def test_unauthorised_no_token(ap2):
    m = ap2.create_mandate(
        "u",
        max_amount=Decimal("50"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        payment_method_id="pm_test",
    )
    pg = PaymentGateway(ap2, StripeAdapter(api_key=None))
    r = pg.get_payment_token(m.mandate_id, Decimal("100"))
    assert not r.authorized
    assert r.token is None
    assert r.reason == "exceeds_per_transaction_cap"


def test_no_payment_method_on_mandate(ap2):
    m = ap2.create_mandate(
        "u",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
    )
    pg = PaymentGateway(ap2, StripeAdapter(api_key=None))
    r = pg.get_payment_token(m.mandate_id, Decimal("100"))
    assert not r.authorized
    assert r.reason == "no_payment_method_on_mandate"


def test_record_purchase_writes_spend(ap2, tmp_db):
    m = ap2.create_mandate(
        "u",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        payment_method_id="pm_test",
    )
    pg = PaymentGateway(ap2, StripeAdapter(api_key=None))
    pg.record_completed_purchase(
        m.mandate_id, Decimal("75"), "ord_abc", vendor="shop.com", category="apparel"
    )
    rows = tmp_db.spend_records.all()
    assert len(rows) == 1
    assert rows[0]["amount"] == "75"
    assert rows[0]["order_id"] == "ord_abc"


def test_token_response_does_not_expose_payment_method_id(ap2):
    m = ap2.create_mandate(
        "u",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        payment_method_id="pm_secret_value",
    )
    pg = PaymentGateway(ap2, StripeAdapter(api_key=None))
    r = pg.get_payment_token(m.mandate_id, Decimal("50"))
    assert r.authorized
    # Critical invariant: payment_method_id never appears in the returned object
    blob = repr(r)
    assert "pm_secret_value" not in blob
