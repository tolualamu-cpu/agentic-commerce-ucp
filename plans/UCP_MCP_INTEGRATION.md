# UCP MCP Integration Plan
## Full Kith (and future merchant) activation via the UCP MCP transport

> Status: **Planning** — no Phase 8 code written yet.
> Blocking evidence: Kith's MCP endpoint returns `-32001 "Missing profile uri"` without
> a `UCP-Agent` header pointing to our published `/.well-known/ucp` profile.
> Last verified: 2026-06-09

---

## Problem statement

`UCPMCPClient` (`ucp/client.py`) is a complete stub — every method raises
`NotImplementedError("UCPMCPClient activated in Phase 3")`. Meanwhile Kith's live MCP
endpoint (`https://kithnyc.myshopify.com/api/ucp/mcp`) is reachable and will serve
authenticated agents. The blocker is *identity*: Kith's server validates who we are by
fetching the URL in our `UCP-Agent` request header. We have no such URL.

**Six blockers, in dependency order:**

| # | Blocker | Why it matters |
|---|---------|----------------|
| B1 | Platform has no `/.well-known/ucp` endpoint | Kith can't discover us → every MCP call → -32001 |
| B2 | `UCP-Agent` header not injected in outbound requests | Without it, Kith can't find our profile |
| B3 | No JSON-RPC 2.0 transport layer | `UCPMCPClient` can't make any calls |
| B4 | `UCPMCPClient` methods are stubs | Product search, checkout, orders all crash |
| B5 | Payment token flow unimplemented for MCP merchants | Kith uses Google Pay / Shop Pay / Shopify Card, not Stripe |
| B6 | Kith still routes via `LiveShopifyTransport` | Cannot graduate until B1–B5 proven end-to-end |

---

## Architecture overview

```
Browser / CLI
     │
     ▼
OrchestratorAgent ──calls──▶ tools/discovery_tools.py::search_products
                                      │
                                      ▼
                             MerchantGateway.search("kith.com")
                                      │
                               _build_client("kith.com")
                                      │
                          [direct_adapters check: kith.com NOT in map]
                                      │
                           UCPProfileDiscovery.try_discover
                                      │
                          profile.preferred_transport() == "mcp"
                                      │
                                      ▼
                               UCPMCPClient
                                      │  POST /api/ucp/mcp
                                      │  Headers:
                                      │    UCP-Agent: https://our-domain/.well-known/ucp
                                      │    Signature + Signature-Input (RFC 9421)
                                      │    Content-Type: application/json
                                      │
                          kithnyc.myshopify.com/api/ucp/mcp
                                      │
                           (merchant fetches our /.well-known/ucp)
                                      │
                                      ▼
                              JSON-RPC 2.0 response
```

---

## Phase 8a — Platform agent identity endpoint

**Goal:** Publish `GET /.well-known/ucp` so Kith can verify who we are.

### What to build

#### `config/settings.py` — add agent identity fields

```python
# Add to Settings class:
agent_domain: str = Field(
    default_factory=lambda: os.getenv("AGENT_DOMAIN", "http://localhost:8000")
)
# agent_private_key_pem and agent_key_id already exist.
```

**`.env.example`** — document the new variable:
```bash
AGENT_DOMAIN=https://your-platform-domain.com  # publicly reachable URL; forms UCP-Agent header
```

#### `web/routers/agent_profile.py` — new router

```python
"""GET /.well-known/ucp — Platform agent identity profile.

Merchants fetch this URL (via the UCP-Agent header we send them) to verify
that we are a legitimate UCP-compliant buying agent.  It advertises our
signing key, our supported capabilities as a buyer, and our platform identity.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from config.settings import settings
from ucp.signing import _load_private_key
from ucp.capabilities import AGENT_CAPABILITIES
import base64

router = APIRouter()

@router.get("/.well-known/ucp")
async def agent_ucp_profile():
    """UCP agent profile — consumed by merchant MCP servers."""
    jwk = _get_agent_jwk()
    return JSONResponse({
        "ucp": {
            "version": "2026-04-08",
            "agent": {
                "name": "Agentic Commerce Platform",
                "domain": settings.agent_domain,
                "type": "buying_agent",
            },
            "capabilities": {
                cap: [{"version": "2026-04-08"}]
                for cap in AGENT_CAPABILITIES
            },
            "signing_keys": [jwk] if jwk else [],
        }
    })

def _get_agent_jwk() -> dict | None:
    """Export the agent's public signing key as JWK for merchant verification."""
    if not settings.agent_private_key_pem:
        return None
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = serialization.load_pem_private_key(
        settings.agent_private_key_pem.encode(), password=None
    )
    if not isinstance(key, Ed25519PrivateKey):
        return None
    pub = key.public_key()
    raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {
        "kid": settings.agent_key_id,
        "kty": "OKP",
        "crv": "Ed25519",
        "alg": "EdDSA",
        "x": base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii"),
        "use": "sig",
    }
```

#### `web/app.py` — register the router

```python
from web.routers.agent_profile import router as agent_profile_router
app.include_router(agent_profile_router)  # no prefix — serves at /.well-known/ucp
```

### Tests to add (`tests/test_agent_profile_endpoint.py`)

- `test_well_known_ucp_returns_200` — GET `/.well-known/ucp` → 200 with `Content-Type: application/json`
- `test_agent_profile_contains_version` — response has `ucp.version == "2026-04-08"`
- `test_agent_profile_contains_signing_key` — when `AGENT_PRIVATE_KEY_PEM` is set, `signing_keys` has one JWK with `kty=OKP`, `crv=Ed25519`
- `test_agent_profile_empty_key_when_no_pem` — when env var is unset, `signing_keys == []`, no crash
- `test_agent_profile_lists_agent_capabilities` — `capabilities` dict keys match `AGENT_CAPABILITIES`
- `test_agent_domain_reflects_settings` — `agent.domain` matches `settings.agent_domain`
- `test_well_known_ucp_cache_headers` — response sets `Cache-Control: max-age=300` (reduce merchant re-fetch load)

---

## Phase 8b — UCP-Agent header injection

**Goal:** Every outbound UCP request (both REST and MCP) carries
`UCP-Agent: https://{agent_domain}/.well-known/ucp`.

### What to build

#### `ucp/client.py` — update `UCPClient.__init__`

```python
class UCPClient(MerchantClient):
    def __init__(
        self,
        profile: UCPProfile,
        signer: RequestSigner,
        agent_domain: str = "",   # ← new param
    ):
        self.profile = profile
        self.merchant_domain = profile.merchant_domain
        self.signer = signer
        self.agent_domain = agent_domain

    @property
    def _ucp_agent_header(self) -> str:
        if self.agent_domain:
            return f"{self.agent_domain.rstrip('/')}/.well-known/ucp"
        return ""
```

#### `UCPRestClient._send` — inject header when set

```python
async def _send(self, method: str, path: str, body: dict | None = None) -> dict:
    headers = {"content-type": "application/json", "accept": "application/json"}
    ucp_agent = self._ucp_agent_header
    if ucp_agent:
        headers["UCP-Agent"] = ucp_agent
    # ... existing signing logic unchanged ...
```

#### `gateway/merchant_gateway.py` — pass agent_domain through

```python
# In __init__, add:
self.agent_domain: str = agent_domain or ""

# In _build_client, update UCPClient instantiation:
if transport == "mcp":
    return UCPMCPClient(profile, self.signer, agent_domain=self.agent_domain)
return UCPRestClient(profile, self.signer,
                     http_client=self.ucp_http_client,
                     agent_domain=self.agent_domain)
```

#### `web/session.py` and `main.py` — supply agent_domain from settings

```python
from config.settings import settings
gateway = MerchantGateway(
    ...,
    agent_domain=settings.agent_domain,  # ← new kwarg
)
```

### Tests to add (`tests/test_ucp_agent_header.py`)

- `test_rest_client_injects_ucp_agent_header` — `UCPRestClient._send` adds `UCP-Agent` header when `agent_domain` is set
- `test_rest_client_omits_ucp_agent_header_when_domain_empty` — no header added when `agent_domain=""`
- `test_mcp_client_injects_ucp_agent_header` — `UCPMCPClient` JSON-RPC requests include `UCP-Agent`
- `test_gateway_passes_agent_domain_to_ucp_rest_client` — MerchantGateway constructor wires `agent_domain` through
- `test_gateway_passes_agent_domain_to_ucp_mcp_client` — same for MCP path
- `test_ucp_agent_url_format` — header value is `{agent_domain}/.well-known/ucp` (no double slashes)

---

## Phase 8c — MCP JSON-RPC 2.0 transport layer

**Goal:** A reusable, testable transport class that executes JSON-RPC calls against any
UCP MCP endpoint. `UCPMCPClient` will delegate all wire communication here.

### What to build

#### `ucp/mcp_transport.py` — new file

```python
"""JSON-RPC 2.0 transport for UCP MCP endpoints.

A UCP MCP endpoint is an HTTP server that accepts JSON-RPC 2.0 POSTs.
Each request has:
  - method: "tools/list" or "tools/call"
  - params: {"name": "<tool>", "arguments": {...}}  (for tools/call)
  - headers: UCP-Agent, RFC 9421 Signature, Content-Type

The transport is synchronously created but all calls are async.
"""
from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from ucp.signing import RequestSigner


class MCPTransportError(Exception):
    """Raised when the MCP server returns a JSON-RPC error or non-200 HTTP."""
    def __init__(self, code: int, message: str, data: dict | None = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data or {}


_id_counter = itertools.count(1)


@dataclass
class MCPTransport:
    """Stateless JSON-RPC 2.0 transport over HTTPS.

    One instance per UCPMCPClient. Re-use the httpx.AsyncClient across calls.
    """
    endpoint: str           # e.g. "https://kithnyc.myshopify.com/api/ucp/mcp"
    signer: RequestSigner
    agent_domain: str = ""
    http_client: httpx.AsyncClient | None = None
    _owns_http: bool = field(init=False, default=False)

    def __post_init__(self):
        if self.http_client is None:
            self.http_client = httpx.AsyncClient(timeout=15.0)
            self._owns_http = True

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Execute a tools/call JSON-RPC request. Returns the `result` field."""
        payload = {
            "jsonrpc": "2.0",
            "id": next(_id_counter),
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        return await self._post(payload)

    async def list_tools(self) -> list[dict]:
        """Execute a tools/list JSON-RPC request. Returns the tools array."""
        payload = {
            "jsonrpc": "2.0",
            "id": next(_id_counter),
            "method": "tools/list",
            "params": {},
        }
        result = await self._post(payload)
        return result if isinstance(result, list) else result.get("tools", [])

    async def _post(self, payload: dict) -> Any:
        body = json.dumps(payload, default=str).encode("utf-8")
        headers: dict[str, str] = {
            "content-type": "application/json",
            "accept": "application/json",
        }
        if self.agent_domain:
            headers["UCP-Agent"] = f"{self.agent_domain.rstrip('/')}/.well-known/ucp"

        signed = self.signer.sign("POST", self.endpoint, headers=headers, body=body)
        resp = await self.http_client.request(
            method="POST",
            url=signed.url,
            headers=signed.headers,
            content=signed.body,
        )
        resp.raise_for_status()
        rpc_response = resp.json()

        if "error" in rpc_response:
            err = rpc_response["error"]
            raise MCPTransportError(
                code=err.get("code", -32000),
                message=err.get("message", "Unknown RPC error"),
                data=err.get("data"),
            )
        return rpc_response.get("result")

    async def close(self) -> None:
        if self._owns_http and self.http_client is not None:
            await self.http_client.aclose()
```

### Tests to add (`tests/test_mcp_transport.py`)

- `test_call_sends_tools_call_method` — payload `method == "tools/call"`, `params.name` is the tool name
- `test_call_injects_ucp_agent_header` — header present when `agent_domain` set
- `test_call_returns_result_field` — strips `{"jsonrpc":"2.0","id":1,"result": {...}}` → returns `{...}`
- `test_call_raises_on_rpc_error` — JSON-RPC error object → `MCPTransportError` with correct `.code`
- `test_call_raises_on_32001` — `-32001` specifically raises `MCPTransportError(code=-32001, ...)`
- `test_list_tools_sends_tools_list_method` — `method == "tools/list"` in payload
- `test_list_tools_returns_tools_array` — handles `{"result": {"tools": [...]}}` and `{"result": [...]}`
- `test_request_is_rfc9421_signed` — `Signature-Input` and `Signature` headers present
- `test_http_error_propagates` — 500 from HTTP → `httpx.HTTPStatusError` (not silenced)
- `test_close_releases_owned_client` — `_owns_http=True` → `aclose()` called; `_owns_http=False` → not called

---

## Phase 8d — UCPMCPClient product discovery

**Goal:** Replace the two stub methods with real `MCPTransport.call` invocations.

### What to build

#### `ucp/client.py` — rewrite `UCPMCPClient`

```python
class UCPMCPClient(UCPClient):
    """UCP over MCP transport (JSON-RPC 2.0 over HTTPS).

    Wire format: POST <mcp_endpoint> with JSON-RPC 2.0 body, UCP-Agent header.
    Tool names follow UCP 2026-04-08 OpenRPC schema for dev.ucp.shopping.
    """

    def __init__(
        self,
        profile: UCPProfile,
        signer: RequestSigner,
        agent_domain: str = "",
        http_client: httpx.AsyncClient | None = None,
    ):
        super().__init__(profile, signer, agent_domain=agent_domain)
        endpoint = self._resolve_mcp_endpoint(profile)
        self._transport = MCPTransport(
            endpoint=endpoint,
            signer=signer,
            agent_domain=agent_domain,
            http_client=http_client,
        )

    @staticmethod
    def _resolve_mcp_endpoint(profile: UCPProfile) -> str:
        for svc in profile.services:
            if svc.type == "mcp" and svc.base_url:
                return svc.base_url
        return f"https://{profile.merchant_domain}/api/ucp/mcp"

    async def search_products(
        self, query: str, filters: dict | None = None, limit: int = 20
    ) -> list[ProductResult]:
        result = await self._transport.call(
            "shopping/searchProducts",
            {"query": query, "filters": filters or {}, "limit": limit},
        )
        items = result if isinstance(result, list) else result.get("products", result.get("results", []))
        return [self._product_from_dict(p) for p in items]

    async def get_product(self, product_id: str) -> ProductResult | None:
        try:
            result = await self._transport.call(
                "shopping/getProduct",
                {"productId": product_id},
            )
        except MCPTransportError as e:
            if e.code == -32002:   # UCP "not found" code
                return None
            raise
        return self._product_from_dict(result)

    def _product_from_dict(self, p: dict) -> ProductResult:
        # UCP 2026-04-08 field names vs our internal model
        return ProductResult(
            product_id=p.get("productId") or p.get("product_id", ""),
            name=p.get("title") or p.get("name", ""),
            description=p.get("description"),
            price=Decimal(str(p.get("price", "0"))),
            currency=p.get("currency", "USD"),
            merchant=p.get("merchant") or self.merchant_domain,
            merchant_domain=p.get("merchantDomain") or p.get("merchant_domain") or self.merchant_domain,
            rating=p.get("rating"),
            review_count=p.get("reviewCount") or p.get("review_count"),
            in_stock=p.get("availableForSale", p.get("in_stock", True)),
            url=p.get("url") or p.get("onlineStoreUrl"),
            images=p.get("images", []),
            attributes=p.get("attributes") or p.get("options", {}),
            source_protocol="ucp_mcp",
        )

    async def close(self) -> None:
        await self._transport.close()
```

### Tests to add (`tests/test_ucp_mcp_client_discovery.py`)

Use the mock-transport pattern from `test_kith_merchant_journeys.py` — inject
`httpx.MockTransport` so no live network calls.

- `test_search_products_calls_shopping_search_products` — `tools/call` with `name="shopping/searchProducts"`
- `test_search_products_passes_query_and_limit` — arguments contain `query`, `limit`
- `test_search_products_returns_product_results` — maps MCP response to `ProductResult` list
- `test_search_products_handles_camel_case_fields` — `productId`, `title`, `availableForSale`, `onlineStoreUrl`
- `test_search_products_handles_snake_case_fields` — `product_id`, `name`, `in_stock`, `url`
- `test_search_products_empty_result` — returns empty list, no crash
- `test_get_product_calls_shopping_get_product` — correct tool name, `productId` in arguments
- `test_get_product_returns_product_result` — maps response correctly
- `test_get_product_returns_none_on_32002` — `MCPTransportError(code=-32002)` → returns `None`
- `test_get_product_reraises_other_errors` — `-32001` propagates
- `test_source_protocol_is_ucp_mcp` — `ProductResult.source_protocol == "ucp_mcp"`
- `test_resolve_mcp_endpoint_from_profile` — picks `services[type=mcp].base_url`
- `test_resolve_mcp_endpoint_fallback` — falls back to `https://{domain}/api/ucp/mcp`

---

## Phase 8e — UCPMCPClient checkout lifecycle

**Goal:** Implement create / update / complete checkout and order status via MCP tools.

### What to build

Add the remaining methods to `UCPMCPClient` in `ucp/client.py`:

```python
async def create_checkout_session(self) -> CheckoutSession:
    result = await self._transport.call("shopping/createCheckout", {})
    return self._session_from_dict(result)

async def update_checkout_session(
    self,
    session_id: str,
    items: list[CartItem],
    buyer: BuyerInfo | None = None,
    discounts: list[str] | None = None,
) -> CheckoutSession:
    args: dict = {
        "checkoutId": session_id,
        "lineItems": [self._item_to_dict(i) for i in items],
    }
    if buyer:
        args["buyer"] = buyer.model_dump(mode="json")
    if discounts:
        args["discounts"] = discounts
    result = await self._transport.call("shopping/updateCheckout", args)
    return self._session_from_dict(result)

async def complete_checkout(
    self,
    session_id: str,
    payment_handler_id: str,
    payment_token: str,
) -> PurchaseOrder:
    result = await self._transport.call(
        "shopping/completeCheckout",
        {
            "checkoutId": session_id,
            "paymentHandlerId": payment_handler_id,
            "paymentToken": payment_token,
        },
    )
    return self._order_from_dict(result, session_id)

async def get_order_status(self, order_id: str) -> TrackingInfo:
    result = await self._transport.call(
        "shopping/getOrder",
        {"orderId": order_id},
    )
    return TrackingInfo(
        order_id=order_id,
        status=OrderStatus(result.get("fulfillmentStatus", result.get("status", "pending"))),
        tracking_number=result.get("trackingNumber") or result.get("tracking_number"),
        carrier=result.get("carrier"),
        estimated_delivery=result.get("estimatedDelivery") or result.get("estimated_delivery"),
        last_event=result.get("lastEvent") or result.get("last_event"),
        last_updated=datetime.now(timezone.utc),
    )

def _session_from_dict(self, d: dict) -> CheckoutSession:
    # UCP 2026-04-08 uses camelCase; fall back to snake_case for any adapter re-use
    session_id = d.get("checkoutId") or d.get("session_id", "")
    handlers = [PaymentHandler(**h) for h in d.get("paymentHandlers", d.get("payment_handlers", []))]
    if not handlers:
        handlers = self.profile.payment_handlers
    items = [
        CartItem(
            product_id=i.get("productId") or i["product_id"],
            name=i.get("title") or i["name"],
            price=Decimal(str(i["price"])),
            quantity=i.get("quantity", 1),
            merchant_domain=self.merchant_domain,
        )
        for i in d.get("lineItems", d.get("line_items", []))
    ]
    return CheckoutSession(
        session_id=session_id,
        merchant_domain=self.merchant_domain,
        line_items=items,
        subtotal=Decimal(str(d.get("subtotal", d.get("lineItemsSubtotalPrice", "0")))),
        discount=Decimal(str(d.get("discount", "0"))),
        tax=Decimal(str(d.get("tax", d.get("totalTax", "0")))),
        shipping=Decimal(str(d.get("shipping", d.get("shippingLine", {}).get("price", "0") if isinstance(d.get("shippingLine"), dict) else "0"))),
        total=Decimal(str(d.get("total", d.get("totalPrice", "0")))),
        currency=d.get("currency", d.get("currencyCode", "USD")),
        status=CheckoutStatus(d.get("status", "open")),
        payment_handlers=handlers,
        created_at=datetime.now(timezone.utc),
    )

def _order_from_dict(self, d: dict, session_id: str) -> PurchaseOrder:
    items = [
        CartItem(
            product_id=i.get("productId") or i["product_id"],
            name=i.get("title") or i["name"],
            price=Decimal(str(i["price"])),
            quantity=i.get("quantity", 1),
            merchant_domain=self.merchant_domain,
        )
        for i in d.get("lineItems", d.get("items", []))
    ]
    return PurchaseOrder(
        order_id=d.get("orderId") or d.get("order_id", ""),
        session_id=session_id,
        merchant_domain=self.merchant_domain,
        items=items,
        total=Decimal(str(d.get("totalPrice", d.get("total", "0")))),
        currency=d.get("currency", d.get("currencyCode", "USD")),
        status=OrderStatus(d.get("fulfillmentStatus", d.get("status", "confirmed"))),
        mandate_id=d.get("mandate_id", ""),
        tracking_number=d.get("trackingNumber") or d.get("tracking_number"),
        estimated_delivery=d.get("estimatedDelivery") or d.get("estimated_delivery"),
        created_at=datetime.now(timezone.utc),
    )

@staticmethod
def _item_to_dict(item: CartItem) -> dict:
    return {
        "productId": item.product_id,
        "title": item.name,
        "price": str(item.price),
        "quantity": item.quantity,
        "attributes": item.attributes,
    }
```

### Tests to add (`tests/test_ucp_mcp_client_checkout.py`)

- `test_create_checkout_calls_shopping_create_checkout` — correct tool name, empty args
- `test_create_checkout_returns_checkout_session` — maps `checkoutId` → `session_id`
- `test_update_checkout_calls_shopping_update_checkout` — tool name + `checkoutId` + `lineItems`
- `test_update_checkout_sends_buyer_when_provided`
- `test_update_checkout_sends_discounts_when_provided`
- `test_complete_checkout_calls_shopping_complete_checkout` — `checkoutId`, `paymentHandlerId`, `paymentToken`
- `test_complete_checkout_returns_purchase_order` — maps `orderId`, `totalPrice`, `fulfillmentStatus`
- `test_get_order_status_calls_shopping_get_order`
- `test_get_order_status_maps_camelcase_fields` — `fulfillmentStatus`, `trackingNumber`, `estimatedDelivery`
- `test_session_from_dict_handles_shopify_camel_case` — `lineItems`, `totalPrice`, `currencyCode`, `shippingLine`
- `test_session_from_dict_falls_back_to_profile_payment_handlers` — no `paymentHandlers` in response → uses profile's
- `test_full_checkout_lifecycle_via_mcp` — integration: create → update → complete in one test with mocked transport

---

## Phase 8f — Payment token flow for MCP merchants

**Goal:** Obtain a payment token from Google Pay / Shop Pay / Shopify Card (Kith's three
payment handlers) and pass it to `complete_checkout`. No Stripe involved.

### Problem

Our current `get_payment_token` in `tools/purchase_tools.py` calls `PaymentGateway`, which
wraps Stripe. Kith does not accept Stripe tokens — it wants tokens from its own registered
handlers (Trust Triangle model: handler → opaque token → merchant).

### Design

**Server-side for headless CLI:** For the CLI (`main.py`), Kith purchases redirect to
Kith's own checkout URL (same as `LiveShopifyTransport.complete_cart()`). This is acceptable
for CLI demos. A full payment token flow requires a browser-side JS component (Google Pay
button, Shop Pay SDK).

**For the web UI:** The browser initializes the payment handler, user completes tokenization
in a modal, browser POSTs the opaque token to our server, server passes it to
`UCPMCPClient.complete_checkout`.

### What to build

#### `web/routers/payment.py` — new router (web only)

```python
"""Payment token collection for Trust Triangle flow.

For UCP MCP merchants (e.g. Kith), our server receives an opaque payment token
from the client-side payment handler (Google Pay, Shop Pay) and passes it through
to the merchant's MCP checkout endpoint. We never see card data.
"""
```

Endpoints:
- `GET /payment/handlers` — returns the list of handlers for the current checkout session
  (fetched from `session.active_checkout_session.payment_handlers`)
- `POST /payment/token` — receives `{handler_id, payment_token}` from browser; stores
  `session.pending_payment_token`; triggers `complete_checkout` via the purchase agent

#### `web/templates/_payment_handlers.html` — partial template

Renders payment handler buttons (Google Pay, Shop Pay) when the gate modal is in MCP-merchant mode.

#### `tools/purchase_tools.py` — update `get_payment_token`

```python
async def get_payment_token(ctx: ToolContext, payment_handler_id: str) -> dict:
    """Obtain a payment token for the active checkout session.

    For REST/Stripe merchants: delegates to PaymentGateway (existing flow).
    For MCP merchants (Trust Triangle): waits for browser-side token from
    /payment/token endpoint, or returns redirect-to-checkout for CLI.
    """
    session = ctx.session

    # Check if we have a pending token from the browser (web flow)
    if hasattr(session, "pending_payment_token") and session.pending_payment_token:
        token = session.pending_payment_token
        session.pending_payment_token = None
        return {"payment_token": token, "payment_handler_id": payment_handler_id}

    # CLI flow: if no browser token available, return redirect URL from checkout session
    if session.active_checkout_session:
        for handler in session.active_checkout_session.payment_handlers:
            if handler.id == payment_handler_id and hasattr(handler, "redirect_url"):
                return {
                    "requires_redirect": True,
                    "redirect_url": handler.redirect_url,
                    "payment_handler_id": payment_handler_id,
                }

    # Fallback: Stripe flow for REST merchants with Stripe handler
    return await ctx.payment_gateway.get_payment_token(
        ctx.user, payment_handler_id, session
    )
```

#### `web/session.py` — add `pending_payment_token` to `WebSession`

```python
pending_payment_token: str | None = None
```

### Tests to add (`tests/test_payment_token_mcp.py`)

- `test_get_payment_token_uses_pending_token_when_set` — `pending_payment_token` on session → returned and cleared
- `test_get_payment_token_clears_after_use` — `pending_payment_token` is `None` after call
- `test_get_payment_token_returns_redirect_when_no_browser_token` — CLI flow: handler has `redirect_url` → returned
- `test_payment_handlers_endpoint_returns_session_handlers` — `GET /payment/handlers` when checkout active
- `test_payment_token_endpoint_stores_token` — `POST /payment/token` sets `pending_payment_token`

---

## Phase 8g — End-to-end Kith MCP journey test

**Goal:** A full mocked journey test proving the entire stack works before touching live traffic.

### What to build

Add a new test class to `tests/test_kith_merchant_journeys.py`:

```
TestKithUCPMCPJourney
```

All HTTP mocked (no live calls). The mock transport:
1. Serves `/.well-known/ucp` for our platform (agent profile)
2. Accepts the first MCP call, checks `UCP-Agent` header present
3. Returns `shopping/searchProducts` response with 3 Kith products
4. Returns `shopping/createCheckout` → session_id
5. Returns `shopping/updateCheckout` → updated session with buyer
6. Returns `shopping/completeCheckout` → order with `orderId`

Tests:
- `test_kith_mcp_search_products_full_journey` — search → product cards → add to cart
- `test_kith_mcp_checkout_lifecycle` — create → update → complete → order confirmed
- `test_kith_mcp_ucp_agent_header_present_in_all_calls` — every call checks the header
- `test_kith_mcp_transport_error_32001_surfaces_clearly` — no `UCP-Agent` → error message
  explains the platform identity misconfiguration, not a cryptic crash
- `test_kith_mcp_get_order_status` — `shopping/getOrder` → `TrackingInfo`

---

## Phase 8h — Kith graduation (remove from LIVE_MERCHANTS)

**Goal:** Once B1–B5 are proven by the Phase 8g tests, Kith routes via UCP MCP instead
of `LiveShopifyTransport`.

### What to change

#### `config/catalogue.py`

Remove `kith.com` from `LIVE_MERCHANTS`. Keep `kith.com` in `merchant_profiles.json`
stub so discovery has a pre-cached profile (avoids a live `/.well-known/ucp` fetch on
every startup).

```python
# Before:
LIVE_MERCHANTS: dict[str, dict] = {
    "kith.com": {
        "store_url": "https://kith.com",
        "display_name": "Kith",
        ...
    }
}

# After:
LIVE_MERCHANTS: dict[str, dict] = {}  # Kith now routes via UCP MCP
```

Also add `kith.com` to a new `UCP_MERCHANTS` dict if per-merchant config is needed:

```python
UCP_MERCHANTS: dict[str, dict] = {
    "kith.com": {
        "display_name": "Kith",
        "logo_url": "https://kith.com/cdn/shop/files/favicon3_32x32.png?v=1613503289",
    }
}
```

#### `web/session.py` and `main.py`

`MerchantGateway.direct_adapters` will no longer include `kith.com`.
Discovery will resolve it via `merchant_profiles.json` stub → `UCPMCPClient`.

#### `tests/test_kith_merchant_journeys.py`

Update `TestMerchantGatewayIntegration` to account for Kith moving to UCP path:
- `test_every_merchant_registered_in_gateway` — Kith must still resolve (via UCP, not direct adapter)
- `test_kith_resolves_to_ucp_mcp_client` — `resolve_client("kith.com")` returns `UCPMCPClient` instance
- Remove `test_live_merchant_has_max_pages` for Kith (no longer `LiveShopifyTransport`)

### Regression gate update

Add to the standing regression gate in `CLAUDE.md`:
- `tests/test_ucp_mcp_client_discovery.py` — `UCPMCPClient` product search
- `tests/test_ucp_mcp_client_checkout.py` — full checkout lifecycle via MCP
- `tests/test_agent_profile_endpoint.py` — `/.well-known/ucp` returns valid agent profile
- `tests/test_ucp_agent_header.py` — `UCP-Agent` header injected in all transports
- `tests/test_mcp_transport.py` — JSON-RPC 2.0 transport layer correctness

---

## Implementation order and test counts

| Phase | What ships | New tests | Cumulative |
|-------|-----------|-----------|------------|
| 8a | `/.well-known/ucp` endpoint + `AGENT_DOMAIN` setting | 7 | current + 7 |
| 8b | `UCP-Agent` header injection everywhere | 6 | + 13 |
| 8c | `MCPTransport` JSON-RPC layer | 10 | + 23 |
| 8d | `UCPMCPClient` product discovery | 13 | + 36 |
| 8e | `UCPMCPClient` checkout + order status | 12 | + 48 |
| 8f | Payment token flow for MCP merchants | 5 | + 53 |
| 8g | Full Kith MCP journey test (mocked) | 5 | + 58 |
| 8h | Kith graduation from LIVE_MERCHANTS | 3 updates | + 58 |

Each phase must pass `pytest tests/ -x -q` before the next phase begins.

---

## Environment variables summary

| Variable | Example | When needed |
|----------|---------|-------------|
| `AGENT_DOMAIN` | `https://agentic-commerce.dev` | Phase 8a — forms `UCP-Agent` header |
| `AGENT_PRIVATE_KEY_PEM` | `-----BEGIN PRIVATE KEY-----...` | Already exists — Ed25519 for signing | <!-- pragma: allowlist secret -->
| `AGENT_KEY_ID` | `agent-key-1` | Already exists — JWK `kid` |

For local dev: `AGENT_DOMAIN=http://localhost:8000` works for end-to-end testing if you
temporarily point Kith's MCP server to fetch from a ngrok tunnel.

For production: `AGENT_DOMAIN` must be a publicly reachable HTTPS URL so merchants can
fetch `/.well-known/ucp`.

---

## Key decisions

| Decision | Rationale |
|----------|-----------|
| JSON-RPC 2.0 over HTTPS, not WebSocket/stdio MCP | Kith's endpoint is an HTTP server; this is simpler and testable |
| `MCPTransport` as a separate class from `UCPMCPClient` | Testable in isolation; `UCPMCPClient` stays focused on data mapping |
| camelCase field mapping in `UCPMCPClient._product_from_dict` | UCP 2026-04-08 spec uses camelCase; snake_case fallbacks for robustness |
| CLI path returns redirect_url, not a token | Trust Triangle requires browser JS; CLI gracefully degrades to Kith checkout URL |
| Kith stays in `merchant_profiles.json` stub after graduation | Avoids live `/.well-known/ucp` fetch on every cold boot; stub is the pre-normalised profile |
| Phase 8g journey test runs before graduation | Never graduate a merchant without full mocked-stack proof |

---

## Files to create or modify

**New files:**
- `web/routers/agent_profile.py`
- `ucp/mcp_transport.py`
- `web/routers/payment.py`
- `web/templates/_payment_handlers.html`
- `tests/test_agent_profile_endpoint.py`
- `tests/test_ucp_agent_header.py`
- `tests/test_mcp_transport.py`
- `tests/test_ucp_mcp_client_discovery.py`
- `tests/test_ucp_mcp_client_checkout.py`
- `tests/test_payment_token_mcp.py`

**Modified files:**
- `config/settings.py` — add `agent_domain`
- `.env.example` — document `AGENT_DOMAIN`
- `ucp/client.py` — implement `UCPMCPClient`, add `agent_domain` to `UCPClient`
- `gateway/merchant_gateway.py` — pass `agent_domain` through
- `web/session.py` — pass `agent_domain` to gateway; add `pending_payment_token`
- `main.py` — pass `agent_domain` to gateway
- `tools/purchase_tools.py` — update `get_payment_token` for MCP flow
- `web/app.py` — register `agent_profile_router`
- `config/catalogue.py` — remove Kith from `LIVE_MERCHANTS` (Phase 8h only)
- `tests/test_kith_merchant_journeys.py` — add `TestKithUCPMCPJourney`; update `TestMerchantGatewayIntegration`
- `CLAUDE.md` — add new test files to standing regression gate
