"""End-to-end Phase 0 → Phase 2 user-journey tests.

These prove the system holds together as one continuous flow — discovery,
evaluation, purchase, tracking — against TWO different merchant integrations:

  1. Shopify direct adapter   (today's reality: MVP via ShopifyMCPAdapter)
  2. UCP REST merchant        (target reality: when merchants ship /.well-known/ucp)

The TOOLS are identical in both scenarios. Only the routing underneath differs.
If both pass, the "swap adapter without touching agents" claim holds at the tool
layer — Phase 3 agents will inherit this correctness without rewriting flows.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal

import httpx

from adapters.shopify_mcp import ShopifyMCPAdapter, StubShopifyTransport
from adapters.stripe import StripeAdapter
from gateway.merchant_gateway import MerchantGateway
from gateway.payment_gateway import PaymentGateway
from guardrails.confidence import ConfidenceChecker
from guardrails.spending import SpendingLimiter
from models.order import BuyerInfo, OrderStatus
from models.product import CartItem
from models.user import UserProfile
from storage.state import SessionState
from tools.context import ToolContext
from tools.discovery_tools import search_products
from tools.evaluation_tools import rank_products
from tools.purchase_tools import (
    complete_order,
    create_checkout_session,
    get_payment_token,
    record_mandate_spend,
    save_order,
    update_checkout_session,
    validate_mandate,
)
from tools.tracking_tools import get_order_status
from ucp.discovery import UCPProfileDiscovery
from ucp.signing import RequestSigner, generate_keypair


# ────────────────────────────────────────────────────────────────────────────
# A minimal UCP-compliant mock merchant — handles the full checkout lifecycle
# ────────────────────────────────────────────────────────────────────────────


class UCPMockMerchant:
    """In-memory UCP REST merchant. Responds to the endpoints our client calls.

    State persists across requests within one test so create→update→complete
    behaves like a real merchant. The signed-request validation isn't enforced
    here (we trust the client to sign — verified separately in test_signing).
    """

    def __init__(self, domain: str = "ucp-merchant.local"):
        self.domain = domain
        self.sessions: dict[str, dict] = {}
        self.orders: dict[str, dict] = {}
        self.products = [
            {
                "product_id": "ucp_001",
                "name": "UCP Running Shoes",
                "price": "119.99",
                "currency": "USD",
                "merchant": "UCP Demo Store",
                "merchant_domain": domain,
                "rating": 4.6,
                "review_count": 312,
                "in_stock": True,
            },
            {
                "product_id": "ucp_002",
                "name": "UCP Training Shorts",
                "price": "39.99",
                "currency": "USD",
                "merchant": "UCP Demo Store",
                "merchant_domain": domain,
                "rating": 4.2,
                "review_count": 87,
                "in_stock": True,
            },
        ]

    def handle(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path

        if method == "POST" and path == "/products/search":
            body = json.loads(request.content)
            q = body["query"].lower()
            hits = [p for p in self.products if q in p["name"].lower()] or self.products
            return httpx.Response(200, json={"results": hits})

        if method == "POST" and path == "/checkout-sessions":
            sid = f"ucp_sess_{uuid.uuid4().hex[:8]}"
            self.sessions[sid] = {
                "session_id": sid,
                "status": "open",
                "subtotal": "0",
                "total": "0",
                "currency": "USD",
                "line_items": [],
            }
            return httpx.Response(200, json=self.sessions[sid])

        if method == "PUT" and path.startswith("/checkout-sessions/"):
            sid = path.rsplit("/", 1)[-1]
            sess = self.sessions[sid]
            body = json.loads(request.content)
            sess["line_items"] = body["line_items"]
            sub = sum(Decimal(i["price"]) * i["quantity"] for i in body["line_items"])
            tax = sub * Decimal("0.08")
            sess["subtotal"] = str(sub)
            sess["tax"] = str(tax)
            sess["total"] = str(sub + tax)
            sess["currency"] = "USD"
            return httpx.Response(200, json=sess)

        if method == "POST" and path.endswith("/complete"):
            sid = path.split("/")[2]
            sess = self.sessions[sid]
            body = json.loads(request.content)
            order_id = f"ucp_ord_{uuid.uuid4().hex[:8]}"
            order = {
                "order_id": order_id,
                "items": sess["line_items"],
                "total": sess["total"],
                "currency": "USD",
                "status": "confirmed",
                "payment_intent_id": f"pi_test_{body['payment_token'][:8]}",
                "tracking_number": "TRK-UCP-001",
                "estimated_delivery": "2-3 days",
            }
            self.orders[order_id] = order
            sess["status"] = "completed"
            return httpx.Response(200, json=order)

        if method == "GET" and path.startswith("/orders/"):
            oid = path.rsplit("/", 1)[-1]
            o = self.orders.get(oid)
            if not o:
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "status": "shipped",
                    "tracking_number": o["tracking_number"],
                    "carrier": "UPS",
                    "estimated_delivery": o["estimated_delivery"],
                    "last_event": "in transit",
                },
            )

        return httpx.Response(404)


# ────────────────────────────────────────────────────────────────────────────
# Fixture builders — one per merchant integration path
# ────────────────────────────────────────────────────────────────────────────


def _build_ctx(tmp_db, ap2, tmp_path, *, ucp: bool) -> ToolContext:
    user = UserProfile(
        user_id="user_1",
        name="Alex",
        payment_method_id="pm_test_secret",
        preferred_categories=["running", "training"],
    )

    private_pem, _, _ = generate_keypair("k1")
    signer = RequestSigner(private_pem, key_id="k1")

    if ucp:
        # UCP path: real stub profile + MockTransport answering all endpoints
        merchant = UCPMockMerchant("ucp-merchant.local")
        stub_path = tmp_path / "profiles.json"
        stub_path.write_text(
            json.dumps(
                {
                    "profiles": {
                        "ucp-merchant.local": {
                            "merchant_domain": "ucp-merchant.local",
                            "capabilities": [
                                {
                                    "namespace": "dev.ucp.shopping.checkout",
                                    "version": "2025-01-15",
                                    "spec_url": "https://ucp.dev/spec",
                                }
                            ],
                            "services": [
                                {
                                    "type": "rest",
                                    "spec_url": "https://ucp.dev/oas",
                                    "base_url": "http://ucp-merchant.local",
                                }
                            ],
                            "payment_handlers": [
                                {
                                    "id": "stripe",
                                    "name": "Stripe",
                                    "spec_url": "https://stripe.com",
                                }
                            ],
                            "signing_keys": [],
                        }
                    }
                }
            )
        )
        http = httpx.AsyncClient(transport=httpx.MockTransport(merchant.handle))
        discovery = UCPProfileDiscovery(tmp_db, http_client=http, stub_path=stub_path)
        # No direct adapter — gateway must take the UCP path
        # Share the same MockTransport with UCPRestClient so tests stay offline
        gateway = MerchantGateway(discovery=discovery, signer=signer, ucp_http_client=http)
    else:
        # Shopify path: offline discovery + StubShopifyTransport
        stub_path = tmp_path / "profiles.json"
        stub_path.write_text(json.dumps({"profiles": {}}))
        offline = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
        discovery = UCPProfileDiscovery(tmp_db, http_client=offline, stub_path=stub_path)
        shopify = ShopifyMCPAdapter(
            "demo-shop.myshopify.com",
            StubShopifyTransport(
                [
                    {
                        "id": "shop_001",
                        "title": "Shopify Running Shoes",
                        "price": "129.99",
                        "currency": "USD",
                        "vendor": "Shopify Demo",
                        "available": True,
                        "rating": 4.5,
                        "review_count": 240,
                    },
                ]
            ),
        )
        gateway = MerchantGateway(
            discovery=discovery,
            signer=signer,
            direct_adapters={"demo-shop.myshopify.com": shopify},
        )

    stripe = StripeAdapter(api_key=None)
    return ToolContext(
        db=tmp_db,
        ap2=ap2,
        merchant_gateway=gateway,
        payment_gateway=PaymentGateway(ap2, stripe),
        spending_limiter=SpendingLimiter(tmp_db),
        confidence_checker=ConfidenceChecker(threshold=0.8),
        user=user,
        session=SessionState(user_id=user.user_id),
    )


# ────────────────────────────────────────────────────────────────────────────
# The journey — executed against both adapters
# ────────────────────────────────────────────────────────────────────────────


async def _run_journey(
    ctx: ToolContext,
    merchant_domain: str,
    mandate_id: str,
    target_product_name_hint: str,
):
    """Discovery → Evaluation → Validate → Purchase chain → Tracking."""
    # 1. Discovery
    products = await search_products(
        ctx,
        query=target_product_name_hint,
        merchant_domains=[merchant_domain],
        mandate_id=mandate_id,
    )
    assert products, "discovery returned no products"

    # 2. Evaluation
    ranked = await rank_products(ctx, products=products)
    assert ranked[0].rank == 1
    chosen = ranked[0].product
    assert "OUT_OF_STOCK" not in ranked[0].risk_flags

    # 3. Pre-flight mandate validation
    pre = await validate_mandate(
        ctx,
        mandate_id=mandate_id,
        amount=chosen.price,
        vendor=merchant_domain,
        category="running",
    )
    assert pre.authorized, f"pre-flight failed: {pre.reason}"

    # 4. Purchase chain
    session = await create_checkout_session(
        ctx,
        merchant_domain=merchant_domain,
        mandate_id=mandate_id,
    )
    assert session is not None

    session = await update_checkout_session(
        ctx,
        session_id=session.session_id,
        merchant_domain=merchant_domain,
        items=[
            CartItem(
                product_id=chosen.product_id,
                name=chosen.name,
                price=chosen.price,
                quantity=1,
            )
        ],
        buyer=BuyerInfo(name="Alex", shipping_address={"city": "SF", "country": "US"}),
        mandate_id=mandate_id,
    )
    assert session.total > chosen.price  # includes tax

    token = await get_payment_token(
        ctx,
        mandate_id=mandate_id,
        amount=session.total,
        vendor=merchant_domain,
        merchant_domain=merchant_domain,
    )
    assert token["authorized"]
    # Payment isolation invariant — at every step, raw pm_id never appears
    assert "pm_test_secret" not in repr(token)

    order = await complete_order(
        ctx,
        session_id=session.session_id,
        merchant_domain=merchant_domain,
        payment_handler_id="stripe",
        payment_token=token["token"],
        mandate_id=mandate_id,
    )
    assert order is not None
    assert order.status == OrderStatus.CONFIRMED
    assert order.mandate_id == mandate_id

    await save_order(ctx, order=order)
    await record_mandate_spend(
        ctx,
        mandate_id=mandate_id,
        amount=order.total,
        order_id=order.order_id,
        vendor=merchant_domain,
        category="running",
    )

    # 5. Tracking
    tracking = await get_order_status(
        ctx,
        order_id=order.order_id,
        merchant_domain=merchant_domain,
        mandate_id=mandate_id,
    )
    assert tracking is not None
    # Shopify stub returns "pending", UCP mock returns "shipped" — both valid
    assert tracking.status in (
        OrderStatus.PENDING,
        OrderStatus.SHIPPED,
        OrderStatus.CONFIRMED,
    )

    return order


# ────────────────────────────────────────────────────────────────────────────
# The actual tests
# ────────────────────────────────────────────────────────────────────────────


def test_shopify_journey_end_to_end(tmp_db, ap2, tmp_path):
    """Discovery → Evaluation → Purchase → Tracking through ShopifyMCPAdapter."""
    ctx = _build_ctx(tmp_db, ap2, tmp_path, ucp=False)
    m = ctx.ap2.create_mandate(
        "user_1",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        payment_method_id="pm_test_secret",
    )
    order = asyncio.get_event_loop().run_until_complete(
        _run_journey(
            ctx,
            "demo-shop.myshopify.com",
            m.mandate_id,
            "running",
        )
    )
    assert order.merchant_domain == "demo-shop.myshopify.com"

    # Audit trail captured every meaningful step
    actions = {r["tool"] for r in tmp_db.audit_log.all()}
    assert {
        "search_products",
        "create_checkout_session",
        "update_checkout_session",
        "get_payment_token",
        "complete_order",
        "get_order_status",
    } <= actions

    # Spend recorded in mandate ledger
    spends = ctx.db.spend_records.search(lambda r: True)  # all
    assert len(ctx.db.spend_records.all()) == 1


def test_ucp_journey_end_to_end(tmp_db, ap2, tmp_path):
    """Same flow, but routed through UCPRestClient against a UCP mock merchant.

    Proves: identical tools + identical assertions, different routing decision.
    """
    ctx = _build_ctx(tmp_db, ap2, tmp_path, ucp=True)
    m = ctx.ap2.create_mandate(
        "user_1",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        payment_method_id="pm_test_secret",
    )
    order = asyncio.get_event_loop().run_until_complete(
        _run_journey(
            ctx,
            "ucp-merchant.local",
            m.mandate_id,
            "running",
        )
    )
    assert order.merchant_domain == "ucp-merchant.local"
    # UCP path actually used — order_id has the UCP-specific prefix
    assert order.order_id.startswith("ucp_ord_")
    # UCP mock returns real tracking
    assert order.tracking_number == "TRK-UCP-001"


def test_daily_cap_exhaustion_blocks_second_purchase(tmp_db, ap2, tmp_path):
    """After one purchase consumes most of the daily cap, the next is blocked.

    This is the *whole point* of having mandate caps. If this fails, the spending
    safety story is broken across phases.
    """
    ctx = _build_ctx(tmp_db, ap2, tmp_path, ucp=False)
    # Daily cap = $200. Product is $129.99 + tax ≈ $140. Second purchase MUST fail.
    m = ctx.ap2.create_mandate(
        "user_1",
        max_amount=Decimal("500"),
        daily_cap=Decimal("200"),
        monthly_cap=Decimal("5000"),
        payment_method_id="pm_test_secret",
    )
    loop = asyncio.get_event_loop()

    # First purchase succeeds and consumes ~$140 of $200 daily
    order = loop.run_until_complete(
        _run_journey(
            ctx,
            "demo-shop.myshopify.com",
            m.mandate_id,
            "running",
        )
    )
    spent_after_first = Decimal(tmp_db.spend_records.all()[0]["amount"])
    assert spent_after_first > Decimal("100")
    headroom = Decimal("200") - spent_after_first
    assert headroom < spent_after_first  # less headroom than first purchase

    # Second attempt: same product, but cap is now exhausted
    pre = loop.run_until_complete(
        validate_mandate(
            ctx,
            mandate_id=m.mandate_id,
            amount=order.total,
            vendor="demo-shop.myshopify.com",
        )
    )
    assert not pre.authorized
    assert pre.reason == "exceeds_daily_cap"

    # Payment gateway also refuses if asked directly — defence in depth
    token = loop.run_until_complete(
        get_payment_token(
            ctx,
            mandate_id=m.mandate_id,
            amount=order.total,
            vendor="demo-shop.myshopify.com",
            merchant_domain="demo-shop.myshopify.com",
        )
    )
    assert token["authorized"] is False
    assert token["reason"] == "exceeds_daily_cap"
    assert "token" not in token


def test_vendor_blocklist_propagates_through_full_journey(tmp_db, ap2, tmp_path):
    """User adds a merchant to the blocklist mid-session; discovery returns empty.

    Proves vendor gating works at the discovery boundary, not just at purchase time.
    """
    ctx = _build_ctx(tmp_db, ap2, tmp_path, ucp=False)
    ctx.user.vendor_blocklist = ["demo-shop.myshopify.com"]
    m = ctx.ap2.create_mandate(
        "user_1",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        payment_method_id="pm_test_secret",
    )
    products = asyncio.get_event_loop().run_until_complete(
        search_products(
            ctx,
            query="running",
            merchant_domains=["demo-shop.myshopify.com"],
            mandate_id=m.mandate_id,
        )
    )
    # Silent filter — agent never learns why
    assert products == []


def test_routing_decision_visible_via_source_protocol(tmp_db, ap2, tmp_path):
    """Same tools, different adapter: ProductResult.source_protocol reveals the path.

    This is the most direct evidence that the gateway swap works.
    """
    loop = asyncio.get_event_loop()

    shopify_ctx = _build_ctx(tmp_db, ap2, tmp_path, ucp=False)
    shopify_products = loop.run_until_complete(
        search_products(
            shopify_ctx,
            query="running",
            merchant_domains=["demo-shop.myshopify.com"],
        )
    )
    assert shopify_products[0].source_protocol == "shopify_mcp"


def test_routing_decision_ucp_path(tmp_db, ap2, tmp_path):
    ucp_ctx = _build_ctx(tmp_db, ap2, tmp_path, ucp=True)
    ucp_products = asyncio.get_event_loop().run_until_complete(
        search_products(
            ucp_ctx,
            query="running",
            merchant_domains=["ucp-merchant.local"],
        )
    )
    assert ucp_products[0].source_protocol == "ucp_rest"
