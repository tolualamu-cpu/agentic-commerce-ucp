"""AgentMandate — the AP2 spending-authority object.

Mandate IDs are the only spending-authority handle that flows through agent context.
payment_method_id is bound to the mandate but only resolved inside PaymentGateway.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


class MandateStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class AgentMandate(BaseModel):
    """Spending authority granted by a user to the agent for some window.

    The HMAC signature is over the canonical JSON of the bound fields below.
    Verification re-computes the HMAC and compares constant-time.
    """

    mandate_id: str
    user_id: str

    max_amount: Decimal  # per-transaction cap
    daily_cap: Decimal
    monthly_cap: Decimal

    allowed_categories: list[str] = Field(default_factory=list)  # empty = any
    allowed_vendors: list[str] = Field(default_factory=list)  # empty = any
    blocked_vendors: list[str] = Field(default_factory=list)

    currency: str = "USD"
    created_at: datetime
    expiry: datetime
    revoked: bool = False
    revoked_at: datetime | None = None

    # Resolved by PaymentGateway only. Never copied into agent context.
    payment_method_id: str | None = Field(default=None, repr=False)

    digital_signature: str | None = None  # HMAC-SHA256 hex

    def is_active(self, now: datetime | None = None) -> MandateStatus:
        now = now or datetime.now(timezone.utc)
        if self.revoked:
            return MandateStatus.REVOKED
        if now >= self.expiry:
            return MandateStatus.EXPIRED
        return MandateStatus.ACTIVE

    def signed_fields(self) -> dict:
        """The canonical field set covered by digital_signature."""
        return {
            "mandate_id": self.mandate_id,
            "user_id": self.user_id,
            "max_amount": str(self.max_amount),
            "daily_cap": str(self.daily_cap),
            "monthly_cap": str(self.monthly_cap),
            "allowed_categories": sorted(self.allowed_categories),
            "allowed_vendors": sorted(self.allowed_vendors),
            "blocked_vendors": sorted(self.blocked_vendors),
            "currency": self.currency,
            "created_at": self.created_at.isoformat(),
            "expiry": self.expiry.isoformat(),
        }


class AuthResult(BaseModel):
    """Result of mandate authorisation for a specific intended purchase."""

    authorized: bool
    mandate_id: str
    amount: Decimal
    vendor: str | None = None
    category: str | None = None

    headroom_per_tx: Decimal | None = None
    headroom_daily: Decimal | None = None
    headroom_monthly: Decimal | None = None

    reason: str | None = None  # populated when authorized=False


class SpendRecord(BaseModel):
    """A single recorded spend against a mandate. Used for cap calculations."""

    mandate_id: str
    order_id: str
    amount: Decimal
    currency: str
    vendor: str
    category: str | None = None
    timestamp: datetime
