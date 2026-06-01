"""PaymentGateway — the bridge between mandate authority and payment token.

This is the ONLY layer outside Phase 0's AP2 engine that reads
``mandate.payment_method_id``. Every other module sees only ``mandate_id`` strings.

Flow:
    1. AP2MandateEngine.verify_and_authorize(mandate_id, amount, vendor)
       → AuthResult (signature ok, not revoked/expired, caps ok)
    2. Resolve mandate.payment_method_id
    3. StripeAdapter.tokenize(payment_method_id, amount) → opaque token
    4. Return PaymentToken to caller (Purchase Agent receives this)
    5. Caller passes token to UCPClient.complete_checkout — never the raw card
    6. On success, AP2MandateEngine.record_spend writes the spend record

The agent only ever sees the opaque token + payment_intent_id.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from adapters.stripe import PaymentToken, StripeAdapter
from models.mandate import AuthResult
from ucp.ap2_extension import AP2MandateEngine


@dataclass
class TokenizationResult:
    """Returned to the caller. Safe to pass through agent context."""

    authorized: bool
    auth: AuthResult
    token: PaymentToken | None = None
    reason: str | None = None


class PaymentGateway:
    """Single trust boundary between mandate authority and payment tokenisation."""

    def __init__(self, ap2: AP2MandateEngine, stripe: StripeAdapter):
        self.ap2 = ap2
        self.stripe = stripe

    def get_payment_token(
        self,
        mandate_id: str,
        amount: Decimal,
        currency: str = "USD",
        vendor: str | None = None,
        category: str | None = None,
        merchant_domain: str | None = None,
    ) -> TokenizationResult:
        # Step 1: authorise against the mandate
        auth = self.ap2.verify_and_authorize(
            mandate_id=mandate_id,
            amount=Decimal(amount),
            vendor=vendor,
            category=category,
        )
        if not auth.authorized:
            return TokenizationResult(authorized=False, auth=auth, reason=auth.reason)

        # Step 2: resolve mandate → payment_method_id (this is the boundary crossing)
        mandate = self.ap2.get_mandate(mandate_id)
        if mandate is None or not mandate.payment_method_id:
            return TokenizationResult(
                authorized=False,
                auth=auth,
                reason="no_payment_method_on_mandate",
            )

        # Step 3: tokenise. payment_method_id never leaves this scope.
        token = self.stripe.tokenize(
            payment_method_id=mandate.payment_method_id,
            amount=Decimal(amount),
            currency=currency,
            merchant_domain=merchant_domain,
        )
        return TokenizationResult(authorized=True, auth=auth, token=token)

    def record_completed_purchase(
        self,
        mandate_id: str,
        amount: Decimal,
        order_id: str,
        vendor: str,
        category: str | None = None,
    ) -> None:
        """Called after UCPClient.complete_checkout returns successfully."""
        self.ap2.record_spend(
            mandate_id=mandate_id,
            amount=Decimal(amount),
            order_id=order_id,
            vendor=vendor,
            category=category,
        )
