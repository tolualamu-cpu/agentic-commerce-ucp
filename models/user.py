"""User-side schemas. payment_method_id is stored here but never crosses into agent context."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, EmailStr, Field


class Address(BaseModel):
    line1: str
    line2: str | None = None
    city: str
    region: str
    postal_code: str
    country: str = "US"
    is_default_shipping: bool = False
    is_default_billing: bool = False


class BudgetConstraints(BaseModel):
    """Hints used by EvaluationAgent ranking — not enforced limits.

    Hard spending limits live on AgentMandate.
    """

    monthly_target: Decimal | None = None
    preferred_price_band: tuple[Decimal, Decimal] | None = None


class UserProfile(BaseModel):
    user_id: str
    name: str
    email: EmailStr | None = None
    addresses: list[Address] = Field(default_factory=list)

    vendor_allowlist: list[str] = Field(default_factory=list)
    vendor_blocklist: list[str] = Field(default_factory=list)
    preferred_categories: list[str] = Field(default_factory=list)

    budget: BudgetConstraints = Field(default_factory=BudgetConstraints)

    # Sensitive — NEVER serialised into agent context.
    # Only PaymentGateway reads this when resolving mandate → token.
    payment_method_id: str | None = Field(default=None, repr=False)

    def default_shipping(self) -> Address | None:
        for a in self.addresses:
            if a.is_default_shipping:
                return a
        return self.addresses[0] if self.addresses else None

    def agent_safe_view(self) -> dict:
        """Returns a dict safe to include in agent context — payment_method_id stripped."""
        data = self.model_dump(exclude={"payment_method_id"})
        return data
