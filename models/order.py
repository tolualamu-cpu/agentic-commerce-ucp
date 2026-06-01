"""Order + checkout schemas — maps directly to UCP /checkout-sessions resource."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field

from models.product import CartItem
from models.ucp_profile import PaymentHandler


class CheckoutStatus(str, Enum):
    OPEN = "open"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class OrderStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class BuyerInfo(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    shipping_address: dict[str, str]


class CheckoutSession(BaseModel):
    """Maps to UCP /checkout-sessions resource (3-step: create → update → complete)."""

    session_id: str
    merchant_domain: str

    line_items: list[CartItem] = Field(default_factory=list)
    subtotal: Decimal = Decimal("0")
    discount: Decimal = Decimal("0")
    tax: Decimal = Decimal("0")
    shipping: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    currency: str = "USD"

    status: CheckoutStatus = CheckoutStatus.OPEN
    payment_handlers: list[PaymentHandler] = Field(default_factory=list)
    buyer: BuyerInfo | None = None

    created_at: datetime
    expires_at: datetime | None = None


class TrackingInfo(BaseModel):
    order_id: str
    status: OrderStatus
    tracking_number: str | None = None
    carrier: str | None = None
    estimated_delivery: str | None = None
    last_event: str | None = None
    last_updated: datetime


class PurchaseOrder(BaseModel):
    order_id: str
    session_id: str
    merchant_domain: str

    items: list[CartItem]
    total: Decimal
    currency: str = "USD"

    status: OrderStatus = OrderStatus.PENDING

    mandate_id: str
    payment_intent_id: str | None = None  # Stripe reference, never card data

    tracking_number: str | None = None
    estimated_delivery: str | None = None

    created_at: datetime
