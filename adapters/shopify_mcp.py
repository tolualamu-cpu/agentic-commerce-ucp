"""ShopifyMCPAdapter — concrete merchant client for Shopify stores.

Strategy: Shopify exposes shopping operations via its MCP server
(@shopify/dev-mcp). In Phase 3, agent calls flow through the anthropic SDK's
``mcp_servers`` parameter. Here in Phase 1 we define the adapter shape and
expose a pluggable ``transport`` so it's testable now and wires to the real
MCP server later.

The adapter is the only place that knows Shopify's wire format. Every method
returns UCP-vocabulary types (ProductResult, CheckoutSession, PurchaseOrder).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol

from gateway.base import MerchantClient
from models.order import (
    BuyerInfo,
    CheckoutSession,
    CheckoutStatus,
    OrderStatus,
    PurchaseOrder,
    TrackingInfo,
)
from models.product import CartItem, ProductResult
from models.ucp_profile import PaymentHandler


class ShopifyTransport(Protocol):
    """Pluggable transport — production uses Shopify MCP; tests use a stub."""

    async def search_products(self, query: str, filters: dict, limit: int) -> list[dict]: ...
    async def get_product(self, product_id: str) -> dict | None: ...
    async def create_cart(self) -> dict: ...
    async def update_cart(self, cart_id: str, items: list[dict], buyer: dict | None) -> dict: ...
    async def complete_cart(self, cart_id: str, payment_token: str) -> dict: ...
    async def get_order(self, order_id: str) -> dict: ...


class ShopifyMCPAdapter(MerchantClient):
    """Wraps a ShopifyTransport, normalising every response to UCP types."""

    DEFAULT_PAYMENT_HANDLERS = [
        PaymentHandler(id="stripe", name="Stripe", spec_url="https://stripe.com/docs/api"),
    ]

    def __init__(self, merchant_domain: str, transport: ShopifyTransport):
        self.merchant_domain = merchant_domain
        self.transport = transport

    # ── search ────────────────────────────────────────────────────────────────

    async def search_products(
        self, query: str, filters: dict | None = None, limit: int = 20
    ) -> list[ProductResult]:
        raw = await self.transport.search_products(query, filters or {}, limit)
        return [self._to_product(r) for r in raw]

    async def get_product(self, product_id: str) -> ProductResult | None:
        raw = await self.transport.get_product(product_id)
        return self._to_product(raw) if raw else None

    def _to_product(self, p: dict) -> ProductResult:
        return ProductResult(
            product_id=str(p["id"]),
            name=p.get("title") or p.get("name", ""),
            description=p.get("description"),
            price=Decimal(str(p.get("price", "0"))),
            currency=p.get("currency", "USD"),
            merchant=p.get("vendor", self.merchant_domain),
            merchant_domain=self.merchant_domain,
            rating=p.get("rating"),
            review_count=p.get("review_count"),
            shipping_estimate=p.get("shipping_estimate"),
            in_stock=p.get("available", True),
            url=p.get("url") or p.get("online_store_url"),
            images=[
                img if isinstance(img, str) else img.get("src", "") for img in p.get("images", [])
            ],
            attributes=p.get("attributes", {}),
            source_protocol="shopify_mcp",
        )

    # ── checkout (Shopify cart maps to UCP CheckoutSession) ───────────────────

    async def create_checkout_session(self) -> CheckoutSession:
        cart = await self.transport.create_cart()
        return self._cart_to_session(cart)

    async def update_checkout_session(
        self,
        session_id: str,
        items: list[CartItem],
        buyer: BuyerInfo | None = None,
        discounts: list[str] | None = None,
    ) -> CheckoutSession:
        shop_items = [
            {
                "product_id": i.product_id,
                "name": i.name,
                "price": str(i.price),
                "quantity": i.quantity,
            }
            for i in items
        ]
        cart = await self.transport.update_cart(
            session_id,
            shop_items,
            buyer.model_dump(mode="json") if buyer else None,
        )
        return self._cart_to_session(cart)

    async def complete_checkout(
        self,
        session_id: str,
        payment_handler_id: str,
        payment_token: str,
    ) -> PurchaseOrder:
        # Shopify only supports the configured handler; payment_handler_id is honoured
        # if the cart accepts multiple, otherwise it's informational.
        result = await self.transport.complete_cart(session_id, payment_token)
        items = [
            CartItem(
                product_id=str(i.get("product_id", i.get("id"))),
                name=i.get("name", i.get("title", "")),
                price=Decimal(str(i.get("price", "0"))),
                quantity=i.get("quantity", 1),
                merchant_domain=self.merchant_domain,
            )
            for i in result.get("items", [])
        ]
        return PurchaseOrder(
            order_id=str(result["order_id"]),
            session_id=session_id,
            merchant_domain=self.merchant_domain,
            items=items,
            total=Decimal(str(result.get("total", "0"))),
            currency=result.get("currency", "USD"),
            status=OrderStatus(result.get("status", "confirmed")),
            mandate_id=result.get("mandate_id", ""),
            payment_intent_id=result.get("payment_intent_id"),
            tracking_number=result.get("tracking_number"),
            estimated_delivery=result.get("estimated_delivery"),
            created_at=datetime.now(timezone.utc),
        )

    async def get_order_status(self, order_id: str) -> TrackingInfo:
        raw = await self.transport.get_order(order_id)
        return TrackingInfo(
            order_id=order_id,
            status=OrderStatus(raw.get("status", "pending")),
            tracking_number=raw.get("tracking_number"),
            carrier=raw.get("carrier"),
            estimated_delivery=raw.get("estimated_delivery"),
            last_event=raw.get("last_event"),
            last_updated=datetime.now(timezone.utc),
        )

    def _cart_to_session(self, cart: dict) -> CheckoutSession:
        items = [
            CartItem(
                product_id=str(i.get("product_id", i.get("id"))),
                name=i.get("name", i.get("title", "")),
                price=Decimal(str(i.get("price", "0"))),
                quantity=i.get("quantity", 1),
                merchant_domain=self.merchant_domain,
            )
            for i in cart.get("items", [])
        ]
        return CheckoutSession(
            session_id=str(cart.get("id", cart.get("cart_id"))),
            merchant_domain=self.merchant_domain,
            line_items=items,
            subtotal=Decimal(str(cart.get("subtotal", "0"))),
            discount=Decimal(str(cart.get("discount", "0"))),
            tax=Decimal(str(cart.get("tax", "0"))),
            shipping=Decimal(str(cart.get("shipping", "0"))),
            total=Decimal(str(cart.get("total", "0"))),
            currency=cart.get("currency", "USD"),
            status=CheckoutStatus(cart.get("status", "open")),
            payment_handlers=self.DEFAULT_PAYMENT_HANDLERS,
            created_at=datetime.now(timezone.utc),
        )


class StubShopifyTransport:
    """In-memory stub used until Phase 3 wires the real MCP server.

    Behaviour is intentionally simple but realistic: search returns deterministic
    results, cart mutations are stored in-memory, complete returns a synthetic order.

    Set the ``STUB_VERBOSE`` env var (any non-empty value) to enable structured
    stderr logging of every cart mutation — useful when diagnosing merchant
    flows in the live REPL.
    """

    def __init__(self, seed_products: list[dict] | None = None):
        self.products = seed_products or [
            {
                "id": "shop_001",
                "title": "Demo Running Shoes",
                "price": "129.99",
                "currency": "USD",
                "vendor": "Demo Brand",
                "available": True,
                "rating": 4.5,
                "review_count": 240,
                "images": ["https://example.com/shoe.jpg"],
            },
        ]
        self.carts: dict[str, dict] = {}
        self.orders: dict[str, dict] = {}
        import os

        self._verbose = bool(os.getenv("STUB_VERBOSE"))

    def _log(self, op: str, **kw) -> None:
        if not self._verbose:
            return
        import sys

        parts = [f"{k}={v!r}" for k, v in kw.items()]
        print(f"[stub.{op}] {' '.join(parts)}", file=sys.stderr)

    async def search_products(self, query: str, filters: dict, limit: int) -> list[dict]:
        q = query.lower()
        hits = [p for p in self.products if q in p["title"].lower()]
        result = hits[:limit] or self.products[:limit]
        self._log("search", query=query, hits=len(result))
        return result

    async def get_product(self, product_id: str) -> dict | None:
        return next((p for p in self.products if str(p["id"]) == product_id), None)

    async def create_cart(self) -> dict:
        cart_id = f"cart_{uuid.uuid4().hex[:12]}"
        self.carts[cart_id] = {
            "id": cart_id,
            "items": [],
            "subtotal": "0",
            "total": "0",
            "currency": "USD",
            "status": "open",
        }
        self._log("create_cart", cart_id=cart_id)
        return self.carts[cart_id]

    async def update_cart(self, cart_id: str, items: list[dict], buyer: dict | None) -> dict:
        cart = self.carts.setdefault(
            cart_id, {"id": cart_id, "items": [], "currency": "USD", "status": "open"}
        )
        # Normalise every line item — coerce price/quantity defensively so
        # we never crash on partial inputs from a model.
        normalised = []
        for raw in items:
            try:
                price = Decimal(str(raw.get("price", "0")))
            except Exception:
                price = Decimal("0")
            try:
                qty = int(raw.get("quantity", 1))
            except Exception:
                qty = 1
            if qty <= 0:
                continue
            normalised.append(
                {
                    "product_id": str(raw.get("product_id", "")),
                    "name": str(raw.get("name", "")),
                    "price": str(price),
                    "quantity": qty,
                    "line_total": str(price * qty),
                }
            )
        cart["items"] = normalised
        subtotal = sum(
            (Decimal(i["price"]) * i["quantity"] for i in normalised),
            start=Decimal("0"),
        )
        tax = subtotal * Decimal("0.08")
        cart["subtotal"] = str(subtotal)
        cart["tax"] = str(tax)
        cart["total"] = str(subtotal + tax)
        cart["buyer"] = buyer
        self._log(
            "update_cart",
            cart_id=cart_id,
            items=len(normalised),
            subtotal=str(subtotal),
            tax=str(tax),
            total=cart["total"],
        )
        return cart

    async def complete_cart(self, cart_id: str, payment_token: str) -> dict:
        cart = self.carts.get(cart_id)
        if cart is None:
            self._log("complete_cart.error", cart_id=cart_id, reason="not_found")
            raise ValueError(f"cart {cart_id} not found")
        if not cart.get("items"):
            self._log("complete_cart.error", cart_id=cart_id, reason="empty_cart")
            raise ValueError(f"cart {cart_id} has no items to complete")
        order_id = f"ord_{uuid.uuid4().hex[:12]}"
        order = {
            "order_id": order_id,
            "items": cart.get("items", []),
            "total": cart.get("total", "0"),
            "currency": cart.get("currency", "USD"),
            "status": "confirmed",
            "payment_intent_id": f"pi_test_{payment_token[:8]}",
            "tracking_number": None,
            "estimated_delivery": "2-3 days",
        }
        self.orders[order_id] = order
        cart["status"] = "completed"
        self._log("complete_cart", cart_id=cart_id, order_id=order_id, total=order["total"])
        return order

    async def get_order(self, order_id: str) -> dict:
        return self.orders.get(order_id, {"status": "pending"})
