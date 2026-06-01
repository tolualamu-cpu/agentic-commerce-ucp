"""MerchantClient — the shared interface every merchant integration implements.

This is the single most important contract in Phase 1: it lets MerchantGateway
swap UCPRestClient for ShopifyMCPAdapter (or any future adapter) without any
caller noticing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from models.order import BuyerInfo, CheckoutSession, PurchaseOrder, TrackingInfo
from models.product import CartItem, ProductResult


class MerchantClient(ABC):
    """Every merchant integration (UCP or direct) implements this."""

    merchant_domain: str

    @abstractmethod
    async def search_products(
        self, query: str, filters: dict | None = None, limit: int = 20
    ) -> list[ProductResult]: ...

    @abstractmethod
    async def get_product(self, product_id: str) -> ProductResult | None: ...

    # 3-step UCP checkout lifecycle
    @abstractmethod
    async def create_checkout_session(self) -> CheckoutSession: ...

    @abstractmethod
    async def update_checkout_session(
        self,
        session_id: str,
        items: list[CartItem],
        buyer: BuyerInfo | None = None,
        discounts: list[str] | None = None,
    ) -> CheckoutSession: ...

    @abstractmethod
    async def complete_checkout(
        self,
        session_id: str,
        payment_handler_id: str,
        payment_token: str,
    ) -> PurchaseOrder: ...

    @abstractmethod
    async def get_order_status(self, order_id: str) -> TrackingInfo: ...

    async def close(self) -> None:
        """Override to release HTTP clients / subprocesses."""
        return None
