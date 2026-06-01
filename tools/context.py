"""ToolContext — the dependency container every tool function receives.

Why a container instead of globals or implicit imports:
  - Tests instantiate a fresh context per test (DB, ap2, gateways)
  - Agents are constructed with one context and pass it to every tool call
  - No tool reaches around the context for state — makes audit easy

Construction is centralised in ``ToolContext.create()`` so wiring evolves in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from gateway.merchant_gateway import MerchantGateway
from gateway.payment_gateway import PaymentGateway
from guardrails.confidence import ConfidenceChecker
from guardrails.spending import SpendingLimiter
from guardrails.vendors import VendorGate
from models.user import UserProfile
from storage.db import DB
from storage.state import SessionState
from ucp.ap2_extension import AP2MandateEngine


@dataclass
class ToolContext:
    db: DB
    ap2: AP2MandateEngine
    merchant_gateway: MerchantGateway
    payment_gateway: PaymentGateway
    spending_limiter: SpendingLimiter
    confidence_checker: ConfidenceChecker
    user: UserProfile
    session: SessionState
    # Optional hook the web layer wires up so tools can notify the
    # browser of cart changes (e.g., agent's add_to_cart updating the
    # header badge in real time). Receives a JSON-safe dict event
    # like ``{"type": "cart_update", "data": {"count": N}}``. CLI
    # contexts leave this unset; tools call it best-effort.
    cart_event_notifier: Optional[Callable[[dict[str, Any]], None]] = None

    def vendor_gate(self, mandate_id: str | None = None) -> VendorGate:
        """Returns a vendor gate bound to the current mandate (if any)."""
        mandate = self.ap2.get_mandate(mandate_id) if mandate_id else None
        return VendorGate(self.user, mandate)
