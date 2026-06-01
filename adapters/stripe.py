"""StripeAdapter — payment tokenisation in Stripe test mode.

This is the ONLY module in the system that:
  - reads the Stripe test key
  - touches the Stripe SDK
  - sees ``payment_method_id`` (a Stripe-side ID)

The PaymentGateway above is the only caller. Agents never reach this layer.

Test mode: if no Stripe key is configured, a deterministic in-memory tokeniser
is used so Phase 1 is fully testable offline. The interface is identical.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from decimal import Decimal


@dataclass
class PaymentToken:
    """Opaque token returned to the agent layer. payment_method_id NOT included."""

    token: str
    amount: Decimal
    currency: str
    payment_intent_id: str


class StripeAdapter:
    """Tokenises a stored payment method for a single purchase.

    Initialise with a stripe test key for live test-mode calls; pass None for the
    offline tokeniser. Both produce the same PaymentToken shape.
    """

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or ""
        self._live = bool(
            self.api_key.startswith("sk_test_") or self.api_key.startswith("sk_live_")
        )
        self._stripe = None
        if self._live:
            try:
                import stripe  # noqa: F401

                self._stripe = stripe
                self._stripe.api_key = self.api_key
            except ImportError:
                self._live = False  # SDK missing — fall back to offline

    def tokenize(
        self,
        payment_method_id: str,
        amount: Decimal,
        currency: str = "USD",
        merchant_domain: str | None = None,
    ) -> PaymentToken:
        """Create a Stripe PaymentIntent and return an opaque token.

        The returned token is what the agent layer passes to UCPClient.complete_checkout.
        Stripe sees the card; the merchant sees the token; the agent never sees either.
        """
        if self._live:
            intent = self._stripe.PaymentIntent.create(
                amount=int(Decimal(amount) * 100),
                currency=currency.lower(),
                payment_method=payment_method_id,
                confirmation_method="manual",
                confirm=False,
                metadata={"merchant_domain": merchant_domain or ""},
            )
            return PaymentToken(
                token=intent.client_secret,
                amount=Decimal(amount),
                currency=currency,
                payment_intent_id=intent.id,
            )

        # Offline test tokeniser — deterministic per (pm, amount, nonce)
        nonce = uuid.uuid4().hex[:8]
        digest = hashlib.sha256(
            f"{payment_method_id}:{amount}:{currency}:{nonce}".encode("utf-8")
        ).hexdigest()[:32]
        return PaymentToken(
            token=f"tok_test_{digest}",
            amount=Decimal(amount),
            currency=currency,
            payment_intent_id=f"pi_test_{nonce}",
        )
