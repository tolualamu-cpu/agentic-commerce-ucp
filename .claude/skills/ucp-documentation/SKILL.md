description: UCP (Universal Commerce Protocol) specification reference — discovery, checkout, identity, orders, payments, transports, and security.

# UCP Documentation Reference

This skill is a living reference for the Universal Commerce Protocol (UCP) as
implemented in this codebase. Consult it whenever building, extending, or
debugging merchant integrations, checkout flows, or agent commerce features.

---

## 1. What UCP Is

UCP is an open, decentralized commerce protocol — "the common language for
platforms, agents, and businesses." It provides building blocks for agentic
commerce across industries: discovery, checkout, identity linking, order
management, and payments. Co-developed by Google, Shopify, Stripe, Amazon,
Walmart, Target, and 50+ other partners. Apache 2.0 licensed.

**Official site:** https://ucp.dev/
**Specification pages:** overview, checkout, identity-linking, order, checkout-rest, embedded-checkout
**GitHub:** linked from ucp.dev

---

## 2. Discovery (/.well-known/ucp)

Merchants publish a JSON profile at `https://{domain}/.well-known/ucp`.

### Profile shape (our model: `models/ucp_profile.py`)
```
UCPProfile:
  merchant_domain: str
  capabilities: list[UCPCapabilityDeclaration]  # reverse-domain namespaces
  services: list[UCPService]                     # transport endpoints
  payment_handlers: list[PaymentHandler]          # Trust Triangle handlers
  signing_keys: list[SigningKey]                  # JWK public keys
```

### Capability namespaces (our impl: `ucp/capabilities.py`)
```
dev.ucp.shopping.checkout       — 3-step checkout lifecycle
dev.ucp.identity.linking        — OAuth 2.0 account linking
dev.ucp.orders.management       — order status, webhooks, returns
dev.ucp.payments.token_exchange — opaque payment token flow
dev.ucp.extensions.ap2          — AP2 mandate-based payments
dev.ucp.extensions.discounts    — discount/promo support
dev.ucp.extensions.fulfillment  — shipping/fulfillment tracking
```

### Capability negotiation algorithm
1. Compute intersection of agent + merchant capabilities
2. Select highest mutually-supported version (by date) for each
3. Remove extensions whose parent capabilities are not in the intersection
4. Repeat step 3 until stable

Our implementation: `ucp/capabilities.py::CapabilityNegotiator.negotiate()`

### Profile discovery flow (our impl: `ucp/discovery.py`)
1. Check DB cache (TTL-based, default 60s, configurable)
2. Real HTTP GET `https://{domain}/.well-known/ucp`
3. Fall back to stub from `config/merchant_profiles.json`
4. Return `None` if no UCP support (caller falls back to direct adapter)

Platform advertises its own profile URL via `UCP-Agent` HTTP header.

---

## 3. Checkout Lifecycle (3-step)

Maps to the `/checkout-sessions` resource. Our model: `models/order.py::CheckoutSession`.

### Endpoints (REST transport)
```
POST   /checkout-sessions               — create session
PUT    /checkout-sessions/{id}           — update (items, buyer, discounts)
POST   /checkout-sessions/{id}/complete  — complete (with payment token)
```

### Session states
```
CheckoutStatus:
  OPEN       — session created, items being added
  COMPLETED  — payment processed, order placed
  EXPIRED    — TTL exceeded without completion
  CANCELLED  — user or agent cancelled
```

### Checkout flow
1. **Create** — empty session with merchant_domain, gets session_id
2. **Update** — add line_items, buyer info (name, email, shipping_address), apply discounts
3. **Complete** — submit payment_handler_id + payment_token, receive PurchaseOrder

Our implementation: `ucp/client.py::UCPRestClient`

### Checkout response fields
```
session_id, merchant_domain, line_items[], subtotal, discount, tax,
shipping, total, currency, status, payment_handlers[], buyer,
created_at, expires_at
```

---

## 4. Payment Architecture (Trust Triangle)

Three roles, separated for PCI-DSS scope minimization:

| Role | Responsibility |
|------|---------------|
| **Payment Credential Provider** | Defines handler specs, tokenization logic |
| **Business (merchant)** | Configures handlers with credentials/keys in checkout response |
| **Platform (agent)** | Executes handler logic, obtains opaque token, never sees raw card |

### Payment handler model (`models/ucp_profile.py::PaymentHandler`)
```
id: str        — unique handler identifier
name: str      — human-readable name (e.g. "Stripe", "Google Pay")
spec_url: str  — handler specification URL
```

### Three payment scenarios

**Scenario A — Digital Wallet (Google Pay, Shop Pay):**
Business advertises wallet config -> Platform calls wallet API -> Submits encrypted token

**Scenario B — Direct Tokenization (PSP):**
Business advertises PSP tokenizer endpoint -> Platform calls PSP -> Submits token
(merchant may request 3DS challenge via `continue_url`)

**Scenario C — AP2 Mandate (autonomous agent):**
Agent generates cryptographically-signed mandate -> Business validates signature as authorization

### AP2 in this codebase (`ucp/ap2_extension.py`)
- HMAC-SHA256 signed spending mandates
- Per-transaction, daily, and monthly caps
- Instant revocation via DB flag
- `payment_method_id` never leaves the AP2 layer
- Mandate proof can be added to outgoing UCP requests via `present_mandate_proof`

---

## 5. Transport Protocols

UCP supports multiple transports. Each merchant declares which in their profile's `services` list.

### REST (primary, our impl: `ucp/client.py::UCPRestClient`)
- HTTP/1.1, `application/json`
- All requests signed via RFC 9421 (HTTP Message Signatures)
- Ed25519 algorithm, components: @method, @target-uri, host, content-type, content-digest
- Our signing implementation: `ucp/signing.py::RequestSigner`

### MCP (JSON-RPC, our impl: `ucp/client.py::UCPMCPClient`)
- JSON-RPC `tools/call` with UCP payload in `params.arguments`
- Responses include both `structuredContent` and serialized `content[]`

### A2A (Agent-to-Agent)
- Agent card specification for peer agent integration

### Embedded
- JSON-RPC for host-embedded contexts

### Transport preference order (our default)
```python
# ucp/client.py — resolve_base_url prefers REST first
["rest", "mcp", "a2a"]
```

---

## 6. Security Requirements

- All communication MUST use HTTPS
- Signing keys published in profile enable RFC 9421 message signature verification
- Authentication: API keys, OAuth 2.0, mTLS, or HTTP Message Signatures
- Identity must bind to `UCP-Agent` header claim
- PCI-DSS scope minimized through opaque credentials and handler delegation
- Profile hosting: HTTPS-only, no redirects, minimum 60-second Cache-Control with public directive

### RFC 9421 Signing (our impl: `ucp/signing.py`)
```
Algorithm:   Ed25519 (EdDSA)
Key format:  JWK with kty=OKP, crv=Ed25519
Components:  @method, @target-uri, host, content-type, content-digest
Digest:      SHA-256 per RFC 9530
```

---

## 7. Order Management

### Order states (`models/order.py::OrderStatus`)
```
PENDING    — order placed, awaiting confirmation
CONFIRMED  — merchant confirmed
SHIPPED    — in transit
DELIVERED  — received
CANCELLED  — cancelled by user or merchant
REFUNDED   — payment returned
```

### Tracking (`models/order.py::TrackingInfo`)
```
order_id, status, tracking_number, carrier,
estimated_delivery, last_event, last_updated
```

Real-time webhooks power status updates, shipment tracking, and return processing.

---

## 8. Identity Linking

OAuth 2.0 standard enables agents to maintain secure, authorized relationships
with merchants without sharing credentials. Namespace: `dev.ucp.identity.linking`.

---

## 9. Versioning

- Format: `YYYY-MM-DD`
- Negotiation selects highest mutually-supported version
- Merchants may publish `supported_versions` map for older protocol versions
- Breaking changes require protocol version bump; backwards-compatible additions do not

---

## 10. Error Handling

| Failure | REST | MCP |
|---------|------|-----|
| Discovery failure (unreachable profile) | HTTP 424 | Error code -32001 |
| Version mismatch | HTTP 422 | Error code -32001 |
| Negotiation failure | HTTP 200 with `status: "error"` + `messages[]` | Same |

Optional `continue_url` provides web fallback for graceful degradation.

---

## 11. Architecture in This Codebase

### The Gateway Pattern (`gateway/merchant_gateway.py`)
```
Agent -> Tool Layer -> MerchantGateway -> resolve_client(domain):
  1. Cache hit? -> return cached client
  2. UCPProfileDiscovery.try_discover(domain)?
     - If profile + capabilities match -> UCPClient (REST or MCP)
  3. Direct adapter registered? -> ShopifyMCPAdapter / LiveShopifyTransport
  4. None -> unsupported merchant
```

### MerchantClient interface (`gateway/base.py`)
Every merchant integration implements this ABC:
```python
search_products(query, filters, limit) -> list[ProductResult]
get_product(product_id) -> ProductResult | None
create_checkout_session() -> CheckoutSession
update_checkout_session(session_id, items, buyer, discounts) -> CheckoutSession
complete_checkout(session_id, handler_id, token) -> PurchaseOrder
get_order_status(order_id) -> TrackingInfo
close() -> None
```

### Key files
```
ucp/discovery.py        — /.well-known/ucp fetching + cache
ucp/capabilities.py     — capability namespaces + negotiation
ucp/client.py           — UCPRestClient, UCPMCPClient
ucp/signing.py          — RFC 9421 Ed25519 signing
ucp/ap2_extension.py    — AP2 mandate engine
models/ucp_profile.py   — profile, capability, service, handler, key schemas
models/product.py        — ProductResult, CartItem, RankedProduct
models/order.py          — CheckoutSession, PurchaseOrder, TrackingInfo
gateway/base.py          — MerchantClient ABC
gateway/merchant_gateway.py — try-UCP-first routing
config/merchant_profiles.json — stub UCP profiles for demo merchants
```

---

## 12. Co-developers and Ecosystem

**Shopping:** Google, Shopify, Etsy, Wayfair, Target, Walmart, Amazon, Microsoft, Meta, Salesforce, Stripe
**Lodging:** Amadeus, Booking.com, Expedia, Hilton, Marriott, Trip.com
**Food:** DoorDash, Square, Toast, Uber Eats
**50+ endorsed partners** across retail, payments, and platforms
