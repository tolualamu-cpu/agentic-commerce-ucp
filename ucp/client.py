"""UCPClient — UCP protocol implementation over a chosen transport.

UCPRestClient implements the 3-step checkout lifecycle from the UCP spec:
    POST   /checkout-sessions                 → create
    PUT    /checkout-sessions/{id}            → update (items, buyer, discounts)
    POST   /checkout-sessions/{id}/complete   → complete (with payment token)

Every outbound request is signed via RequestSigner (RFC 9421). Whether merchants
verify today is irrelevant — UCP compliance requires we sign.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

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
from models.ucp_profile import PaymentHandler, UCPProfile
from ucp.signing import RequestSigner


class UCPClient(MerchantClient):
    """Abstract base — concrete subclasses pick a transport (REST, MCP, A2A)."""

    profile: UCPProfile

    def __init__(self, profile: UCPProfile, signer: RequestSigner):
        self.profile = profile
        self.merchant_domain = profile.merchant_domain
        self.signer = signer


class UCPRestClient(UCPClient):
    """UCP over REST/HTTPS. JSON bodies, RFC 9421 signed."""

    def __init__(
        self,
        profile: UCPProfile,
        signer: RequestSigner,
        http_client: httpx.AsyncClient | None = None,
    ):
        super().__init__(profile, signer)
        self._http = http_client
        self._owns_http = http_client is None
        self._base_url = self._resolve_base_url(profile)

    @staticmethod
    def _resolve_base_url(profile: UCPProfile) -> str:
        for svc in profile.services:
            if svc.type == "rest" and svc.base_url:
                return svc.base_url.rstrip("/")
        return f"https://{profile.merchant_domain}"

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=10.0)
        return self._http

    async def _send(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{self._base_url}{path}"
        body_bytes = b"" if body is None else json.dumps(body, default=str).encode("utf-8")
        headers = {"content-type": "application/json", "accept": "application/json"}
        signed = self.signer.sign(method, url, headers=headers, body=body_bytes)
        resp = await self.http.request(
            method=signed.method,
            url=signed.url,
            headers=signed.headers,
            content=signed.body,
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    # ── discovery ─────────────────────────────────────────────────────────────

    async def search_products(
        self, query: str, filters: dict | None = None, limit: int = 20
    ) -> list[ProductResult]:
        body = {"query": query, "filters": filters or {}, "limit": limit}
        data = await self._send("POST", "/products/search", body)
        return [self._product_from_dict(p) for p in data.get("results", [])]

    async def get_product(self, product_id: str) -> ProductResult | None:
        try:
            data = await self._send("GET", f"/products/{product_id}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        return self._product_from_dict(data)

    def _product_from_dict(self, p: dict) -> ProductResult:
        return ProductResult(
            product_id=p["product_id"],
            name=p["name"],
            description=p.get("description"),
            price=Decimal(str(p["price"])),
            currency=p.get("currency", "USD"),
            merchant=p.get("merchant", self.merchant_domain),
            merchant_domain=p.get("merchant_domain", self.merchant_domain),
            rating=p.get("rating"),
            review_count=p.get("review_count"),
            shipping_estimate=p.get("shipping_estimate"),
            shipping_cost=Decimal(str(p["shipping_cost"]))
            if p.get("shipping_cost") is not None
            else None,
            in_stock=p.get("in_stock", True),
            url=p.get("url"),
            images=p.get("images", []),
            attributes=p.get("attributes", {}),
            source_protocol="ucp_rest",
        )

    # ── checkout lifecycle ────────────────────────────────────────────────────

    async def create_checkout_session(self) -> CheckoutSession:
        data = await self._send("POST", "/checkout-sessions", {})
        return self._session_from_dict(data)

    async def update_checkout_session(
        self,
        session_id: str,
        items: list[CartItem],
        buyer: BuyerInfo | None = None,
        discounts: list[str] | None = None,
    ) -> CheckoutSession:
        body: dict[str, Any] = {
            "line_items": [self._item_to_dict(i) for i in items],
        }
        if buyer is not None:
            body["buyer"] = buyer.model_dump(mode="json")
        if discounts:
            body["discounts"] = discounts
        data = await self._send("PUT", f"/checkout-sessions/{session_id}", body)
        return self._session_from_dict(data)

    async def complete_checkout(
        self,
        session_id: str,
        payment_handler_id: str,
        payment_token: str,
    ) -> PurchaseOrder:
        body = {
            "payment_handler_id": payment_handler_id,
            "payment_token": payment_token,
        }
        data = await self._send("POST", f"/checkout-sessions/{session_id}/complete", body)
        return self._order_from_dict(data, session_id)

    async def get_order_status(self, order_id: str) -> TrackingInfo:
        data = await self._send("GET", f"/orders/{order_id}")
        return TrackingInfo(
            order_id=order_id,
            status=OrderStatus(data.get("status", "pending")),
            tracking_number=data.get("tracking_number"),
            carrier=data.get("carrier"),
            estimated_delivery=data.get("estimated_delivery"),
            last_event=data.get("last_event"),
            last_updated=datetime.now(timezone.utc),
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _item_to_dict(item: CartItem) -> dict:
        return {
            "product_id": item.product_id,
            "name": item.name,
            "price": str(item.price),
            "quantity": item.quantity,
            "attributes": item.attributes,
        }

    def _session_from_dict(self, d: dict) -> CheckoutSession:
        handlers = [
            PaymentHandler(**h) for h in d.get("payment_handlers", [])
        ] or self.profile.payment_handlers
        items = [
            CartItem(
                product_id=i["product_id"],
                name=i["name"],
                price=Decimal(str(i["price"])),
                quantity=i.get("quantity", 1),
                merchant_domain=self.merchant_domain,
                attributes=i.get("attributes", {}),
            )
            for i in d.get("line_items", [])
        ]
        return CheckoutSession(
            session_id=d["session_id"],
            merchant_domain=self.merchant_domain,
            line_items=items,
            subtotal=Decimal(str(d.get("subtotal", "0"))),
            discount=Decimal(str(d.get("discount", "0"))),
            tax=Decimal(str(d.get("tax", "0"))),
            shipping=Decimal(str(d.get("shipping", "0"))),
            total=Decimal(str(d.get("total", "0"))),
            currency=d.get("currency", "USD"),
            status=CheckoutStatus(d.get("status", "open")),
            payment_handlers=handlers,
            created_at=datetime.now(timezone.utc),
        )

    def _order_from_dict(self, d: dict, session_id: str) -> PurchaseOrder:
        items = [
            CartItem(
                product_id=i["product_id"],
                name=i["name"],
                price=Decimal(str(i["price"])),
                quantity=i.get("quantity", 1),
                merchant_domain=self.merchant_domain,
            )
            for i in d.get("items", [])
        ]
        return PurchaseOrder(
            order_id=d["order_id"],
            session_id=session_id,
            merchant_domain=self.merchant_domain,
            items=items,
            total=Decimal(str(d.get("total", "0"))),
            currency=d.get("currency", "USD"),
            status=OrderStatus(d.get("status", "confirmed")),
            mandate_id=d.get("mandate_id", ""),
            payment_intent_id=d.get("payment_intent_id"),
            tracking_number=d.get("tracking_number"),
            estimated_delivery=d.get("estimated_delivery"),
            created_at=datetime.now(timezone.utc),
        )

    async def close(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()


class UCPMCPClient(UCPClient):
    """UCP over MCP transport (JSON-RPC 2.0).

    Wires through anthropic SDK's mcp_servers. Phase 3 will activate this; for now
    the abstract surface exists so MerchantGateway can route to it when a merchant
    profile declares MCP transport.
    """

    async def search_products(self, *a, **kw):
        raise NotImplementedError("UCPMCPClient activated in Phase 3")

    async def get_product(self, *a, **kw):
        raise NotImplementedError("UCPMCPClient activated in Phase 3")

    async def create_checkout_session(self, *a, **kw):
        raise NotImplementedError("UCPMCPClient activated in Phase 3")

    async def update_checkout_session(self, *a, **kw):
        raise NotImplementedError("UCPMCPClient activated in Phase 3")

    async def complete_checkout(self, *a, **kw):
        raise NotImplementedError("UCPMCPClient activated in Phase 3")

    async def get_order_status(self, *a, **kw):
        raise NotImplementedError("UCPMCPClient activated in Phase 3")
