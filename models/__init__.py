"""UCP-vocabulary canonical types. The stable contract for the whole system."""

from models.mandate import AgentMandate, AuthResult, MandateStatus, SpendRecord
from models.order import (
    BuyerInfo,
    CheckoutSession,
    CheckoutStatus,
    OrderStatus,
    PurchaseOrder,
    TrackingInfo,
)
from models.product import CartItem, ProductResult, RankedProduct, SourceProtocol
from models.ucp_profile import (
    PaymentHandler,
    SigningKey,
    UCPCapabilityDeclaration,
    UCPProfile,
    UCPService,
)
from models.user import Address, BudgetConstraints, UserProfile

__all__ = [
    "AgentMandate",
    "AuthResult",
    "MandateStatus",
    "SpendRecord",
    "BuyerInfo",
    "CheckoutSession",
    "CheckoutStatus",
    "OrderStatus",
    "PurchaseOrder",
    "TrackingInfo",
    "CartItem",
    "ProductResult",
    "RankedProduct",
    "SourceProtocol",
    "PaymentHandler",
    "SigningKey",
    "UCPCapabilityDeclaration",
    "UCPProfile",
    "UCPService",
    "Address",
    "BudgetConstraints",
    "UserProfile",
]
