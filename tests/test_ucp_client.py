"""UCPRestClient: 3-step checkout against a mock UCP merchant server."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import httpx

from models.order import BuyerInfo, CheckoutStatus, OrderStatus
from models.product import CartItem
from models.ucp_profile import (
    PaymentHandler,
    UCPCapabilityDeclaration,
    UCPProfile,
    UCPService,
)
from ucp.client import UCPRestClient
from ucp.signing import RequestSigner, generate_keypair


def _profile() -> UCPProfile:
    return UCPProfile(
        merchant_domain="ref.local",
        capabilities=[
            UCPCapabilityDeclaration(
                namespace="dev.ucp.shopping.checkout",
                version="2025-01-15",
                spec_url="https://ucp.dev/spec",
            )
        ],
        services=[
            UCPService(type="rest", spec_url="https://ucp.dev/oas", base_url="http://ref.local")
        ],
        payment_handlers=[
            PaymentHandler(id="stripe", name="Stripe", spec_url="https://stripe.com")
        ],
    )


def _signer() -> RequestSigner:
    private_pem, _, _ = generate_keypair("k1")
    return RequestSigner(private_pem, key_id="k1")


def _make_client(handler) -> UCPRestClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return UCPRestClient(_profile(), _signer(), http_client=http)


def test_search_products():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/products/search"
        assert "Signature" in req.headers
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "product_id": "p1",
                        "name": "Test Shoe",
                        "price": "129.99",
                        "currency": "USD",
                        "merchant": "Ref Store",
                        "in_stock": True,
                    }
                ]
            },
        )

    client = _make_client(handler)
    products = asyncio.get_event_loop().run_until_complete(client.search_products("shoes"))
    assert len(products) == 1
    assert products[0].price == Decimal("129.99")
    assert products[0].source_protocol == "ucp_rest"


def test_full_checkout_lifecycle():
    state = {"session_id": "sess_001"}

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        method = req.method
        assert "Signature" in req.headers
        assert "Signature-Input" in req.headers

        if method == "POST" and path == "/checkout-sessions":
            return httpx.Response(
                200,
                json={
                    "session_id": state["session_id"],
                    "status": "open",
                    "subtotal": "0",
                    "total": "0",
                    "currency": "USD",
                },
            )
        if method == "PUT" and path == f"/checkout-sessions/{state['session_id']}":
            body = json.loads(req.content)
            assert body["line_items"][0]["product_id"] == "p1"
            assert body["buyer"]["name"] == "Alex"
            return httpx.Response(
                200,
                json={
                    "session_id": state["session_id"],
                    "status": "open",
                    "line_items": body["line_items"],
                    "subtotal": "100",
                    "tax": "8",
                    "total": "108",
                    "currency": "USD",
                },
            )
        if method == "POST" and path == f"/checkout-sessions/{state['session_id']}/complete":
            body = json.loads(req.content)
            assert body["payment_handler_id"] == "stripe"
            assert body["payment_token"] == "tok_xyz"
            return httpx.Response(
                200,
                json={
                    "order_id": "ord_42",
                    "items": [
                        {
                            "product_id": "p1",
                            "name": "Shoe",
                            "price": "100",
                            "quantity": 1,
                        }
                    ],
                    "total": "108",
                    "status": "confirmed",
                    "currency": "USD",
                    "payment_intent_id": "pi_test_123",
                },
            )
        return httpx.Response(404)

    client = _make_client(handler)
    loop = asyncio.get_event_loop()

    session = loop.run_until_complete(client.create_checkout_session())
    assert session.status == CheckoutStatus.OPEN

    session = loop.run_until_complete(
        client.update_checkout_session(
            session.session_id,
            items=[CartItem(product_id="p1", name="Shoe", price=Decimal("100"))],
            buyer=BuyerInfo(name="Alex", shipping_address={"city": "SF", "country": "US"}),
        )
    )
    assert session.total == Decimal("108")

    order = loop.run_until_complete(
        client.complete_checkout(
            session.session_id,
            "stripe",
            "tok_xyz",
        )
    )
    assert order.order_id == "ord_42"
    assert order.status == OrderStatus.CONFIRMED
    assert order.payment_intent_id == "pi_test_123"


def test_get_order_status():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/orders/ord_1":
            return httpx.Response(
                200,
                json={
                    "status": "shipped",
                    "tracking_number": "TRK123",
                    "carrier": "UPS",
                    "estimated_delivery": "2d",
                },
            )
        return httpx.Response(404)

    client = _make_client(handler)
    info = asyncio.get_event_loop().run_until_complete(client.get_order_status("ord_1"))
    assert info.status == OrderStatus.SHIPPED
    assert info.tracking_number == "TRK123"
