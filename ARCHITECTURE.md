# Agentic Commerce — System Architecture

> Reference document. Update as the system evolves.
> Last updated: 2026-05-10

---

## Systems Thinking Foundation

Before describing components, the system is understood through first principles:

### System Boundary

**Inside:** agent orchestration, product discovery, evaluation logic, checkout execution, mandate/spending control, order tracking, user profile, audit log.

**Outside (external dependencies):** Anthropic API, Stripe, Shopify, UCP-compliant merchants, payment networks.

**Key boundary rule:** Payment credentials never cross into the agent layer. Merchant-specific API details never cross into the tool layer. Agents only see UCP-vocabulary types.

### Stocks and Flows

```
STOCKS (data that accumulates)          FLOWS (data in motion)
────────────────────────────────        ──────────────────────────────────
UserProfile                             User intent → ProductResult[]
  └─ preferences, addresses             ProductResult[] → RankedProduct[]
  └─ payment_method_id (Stripe)         RankedProduct[] → HITL gate → approval
                                        approval → CheckoutSession → PurchaseOrder
OrderHistory                            PurchaseOrder → TrackingInfo → status updates
  └─ past purchases, merchants used
  └─ return/refund records              Mandate balance consumed by each purchase
                                        Mandate balance replenished on refund
AgentMandate
  └─ spending authority remaining
  └─ daily/monthly caps

MerchantProfileCache
  └─ /.well-known/ucp responses (60s TTL)

AuditLog
  └─ every agent action, timestamped
```

### Feedback Loops

```
REINFORCING (R) — amplifying loops
R1: Purchase history → better preference matching → better recommendations → user trust → more purchases

BALANCING (B) — correcting loops
B1: Mandate balance decreases with each purchase → spending cap enforced → prevents runaway spend
B2: Agent confidence below threshold → escalate to human → human corrects → system improves
B3: Merchant trust score drops on bad experience → vendor blocklist → fewer bad purchases

DELAYS
D1: Checkout confirmation gate introduces intentional delay → gives user time to cancel
D2: Profile cache TTL (60s) → prevents stale capability negotiation without hammering merchant
```

### Leverage Points

Ordered from highest to lowest leverage (Meadows hierarchy):

1. **The goal** — "complete the purchase the user would have chosen themselves" drives every design decision
2. **The rules** — HITL confirmation thresholds, mandate limits, confidence escalation (change these → change system behavior fundamentally)
3. **Information flows** — what agents can see: mandate balance, user preferences, merchant capabilities (withholding info = safety; revealing info = capability)
4. **The interface contracts** — the tool function signatures and UCP types are the stable API; everything below them can be swapped
5. **Physical buffers** — mandate as a spending buffer absorbs uncertainty; local profile cache absorbs merchant latency
6. **Component structure** — the layer architecture below; harder to change, high leverage to get right early

---

## Layer Architecture

```
┌─────────────────────────────────────────────────────────┐
│  L1  CONVERSATION LAYER                                  │
│  User ↔ Rich CLI (MVP) / Web UI / REST API (target)      │
│  Streaming output, HITL confirmation gates               │
└─────────────────────────────┬───────────────────────────┘
                              │ natural language + confirmations
┌─────────────────────────────▼───────────────────────────┐
│  L2  AGENT LAYER                                         │
│  OrchestratorAgent  [claude-sonnet-4-6, streaming]       │
│    ├─ DiscoveryAgent   [claude-haiku-4-5]                │
│    ├─ EvaluationAgent  [claude-haiku-4-5]                │
│    ├─ PurchaseAgent    [claude-haiku-4-5]                │
│    └─ TrackingAgent    [claude-haiku-4-5]                │
│                                                          │
│  Agents speak UCP vocabulary. They know nothing about    │
│  Shopify, Stripe, or any specific merchant/protocol.     │
└─────────────────────────────┬───────────────────────────┘
                              │ tool calls (typed Pydantic I/O)
┌─────────────────────────────▼───────────────────────────┐
│  L3  TOOL LAYER                                          │
│  Python functions given to agents as tools.              │
│  These are the stable interface contract.                │
│                                                          │
│  search_products(query, filters) → ProductResult[]       │
│  create_checkout_session(merchant_domain) → Session      │
│  update_checkout_session(id, items, buyer) → Session     │
│  complete_order(session_id, payment_token) → Order       │
│  get_order_status(order_id) → TrackingInfo               │
│  validate_mandate(id, amount, vendor) → AuthResult       │
│  audit_log(action, context) → None                       │
│                                                          │
│  Rule: tools call Gateway, never SDKs directly.          │
└─────────────────────────────┬───────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────┐
│  L4  GATEWAY LAYER                                       │
│  Routes tool calls to the right implementation.          │
│                                                          │
│  MerchantGateway                                         │
│    1. Try: UCPProfileDiscovery.try_discover(domain)      │
│    2. If profile + capability match → UCPClient          │
│    3. Else → DirectAdapter (Shopify MCP, etc.)           │
│                                                          │
│  PaymentGateway                                          │
│    1. Resolve mandate → payment_method_id                │
│    2. Tokenize via StripeAdapter                          │
│    3. Return token (never raw card) to Purchase Agent    │
│                                                          │
│  This layer is where MVP → production happens.           │
│  Agents above never change. Implementations below swap.  │
└──────────────────┬──────────────────┬───────────────────┘
                   │                  │
      ┌────────────▼──────┐  ┌────────▼────────────────┐
      │  L5  PROTOCOL     │  │  L5  PAYMENT PROTOCOL   │
      │  LAYER            │  │  LAYER                  │
      │                   │  │                         │
      │  UCPRestClient    │  │  StripeAdapter (MVP)    │
      │  UCPMCPClient     │  │  AP2Adapter (target)    │
      │  RequestSigner    │  │  AP2MandateEngine       │
      │  (RFC 9421)       │  │  (local, no merchant    │
      │  ProfileDiscovery │  │   dependency in MVP)    │
      │  ProfileStub      │  │                         │
      │  CapabilityNeg.   │  └────────────┬────────────┘
      └────────────┬──────┘               │
                   │                      │
      ┌────────────▼──────────────────────▼────────────┐
      │  L6  INTEGRATION LAYER                          │
      │  Concrete connections to external systems       │
      │                                                 │
      │  ShopifyMCPAdapter   (MVP — Shopify stores)     │
      │  GenericUCPAdapter   (any UCP merchant)         │
      │  GenericACPAdapter   (any ACP merchant)         │
      │  [future] AmazonAdapter, WalmartAdapter, etc.   │
      │                                                 │
      │  Stripe SDK          (MVP payments)             │
      │  [future] AP2-native payment handlers           │
      └────────────┬──────────────────────┬────────────┘
                   │                      │
      ┌────────────▼──────┐  ┌────────────▼────────────┐
      │  L7  DATA LAYER   │  │  EXTERNAL SYSTEMS        │
      │                   │  │                          │
      │  TinyDB (MVP)     │  │  Shopify (MCP)           │
      │  [future] PG+Redis│  │  Stripe API              │
      │                   │  │  UCP Merchants           │
      │  Tables:          │  │  Anthropic API           │
      │  - mandates       │  │                          │
      │  - orders         │  └──────────────────────────┘
      │  - audit_log      │
      │  - spend_records  │
      │  - profile_cache  │
      └───────────────────┘
```

---

## The Gateway Pattern (Core Scalability Mechanism)

The `MerchantGateway` is the single most important component for scalability. It implements the "try UCP, fall back gracefully" strategy:

```python
class MerchantGateway:
    """
    Routes merchant operations to the best available implementation.
    Agents call this. Agents never know which path ran.
    """

    async def search(self, query: str, filters: dict, domains: list[str]) -> list[ProductResult]:
        tasks = [self._search_one(query, filters, domain) for domain in domains]
        results = await asyncio.gather(*tasks)
        return self._merge_and_rank(results)

    async def _search_one(self, query, filters, domain) -> list[ProductResult]:
        client = await self._resolve_client(domain)
        return await client.search(query, filters)

    async def _resolve_client(self, domain: str) -> MerchantClient:
        # 1. Check cache
        if cached := self.client_cache.get(domain):
            return cached

        # 2. Try real UCP profile discovery
        profile = await self.discovery.try_discover(domain)

        # 3. Route based on what's available
        if profile:
            transport = profile.preferred_transport()   # "rest" | "mcp" | "a2a"
            if transport == "mcp":
                client = UCPMCPClient(profile, self.signer)
            else:
                client = UCPRestClient(profile, self.signer)
        else:
            # No UCP profile — use direct integration
            client = self.direct_adapters.get(domain) or self.shopify_mcp

        self.client_cache.set(domain, client, ttl=60)
        return client
```

**What this buys:**
- Today: Shopify MCP for all merchants
- When `walmart.com` ships `/.well-known/ucp`: Walmart automatically routes through `UCPRestClient`
- When a new merchant is added: `router.add_merchant("newstore.com")` — zero agent changes

---

## Progressive Enhancement Map

Each row shows the MVP implementation and what it upgrades to as dependencies become available:

| Capability | MVP (works today) | Unblocked by | Target |
|---|---|---|---|
| Product discovery | Shopify MCP | Merchant UCP adoption | `UCPRestClient.search()` auto-activated |
| Checkout | Shopify MCP cart/checkout | Merchant UCP adoption | `UCPRestClient` 3-step checkout |
| Request signing | Implemented, merchants may not verify | Merchant-side RFC 9421 support | Fully verified, bidirectional |
| Merchant profiles | Config stub (`merchant_profiles.json`) | Merchant publishing `/.well-known/ucp` | Real `UCPProfileDiscovery.discover()` |
| Capability negotiation | Hardcoded defaults per known merchant | Real profiles available | Live negotiation from profile |
| AP2 mandates | Local HMAC engine, no merchant-side support needed | Merchant AP2 extension adoption | Merchant-verified mandate proofs |
| Order tracking | Polling (`GET /order/{id}`) | Merchant webhook support | Event-driven via webhook receiver |
| Payments | Stripe SDK direct | AP2 payment handler adoption | Trust Triangle via payment handler delegation |
| Multi-merchant search | Fan-out to configured merchants | UCP adoption + profile discovery | Any domain added to router auto-discovered |
| A2A transport | Not implemented | UCP A2A spec maturity | `UCPA2AClient` for agent-to-agent flows |

---

## Data Models (UCP Vocabulary — Stable Contract)

These types are the shared language of the system. They must remain stable — everything below them can change.

```
ProductResult
  product_id, name, price, currency, merchant, merchant_domain
  rating, review_count, shipping_estimate, shipping_cost
  in_stock, url, images, attributes
  source_protocol        ← "shopify_mcp" | "ucp_rest" | "ucp_mcp" | "acp"
  confidence_score       ← 0.0–1.0, set by DiscoveryAgent

CheckoutSession          ← maps to UCP /checkout-sessions resource
  session_id, merchant_domain
  line_items, subtotal, discount, tax, total, currency
  status                 ← "open" | "completed" | "expired"
  payment_handlers       ← from merchant profile
  buyer                  ← shipping address, contact

PurchaseOrder
  order_id, session_id, merchant_domain
  items, total, status, mandate_id
  payment_intent_id      ← Stripe reference (not card data)
  tracking_number, estimated_delivery

AgentMandate             ← AP2 extension: spending authority
  mandate_id, user_id
  max_amount, daily_cap, monthly_cap
  allowed_categories, allowed_vendors
  expiry, revoked
  payment_method_id      ← NEVER leaves PaymentGateway
  digital_signature      ← HMAC-SHA256 (tamper detection)

UCPProfile               ← /.well-known/ucp response
  merchant_domain, capabilities[], services[], payment_handlers[]
  signing_keys[]         ← JWK format, for webhook verification
```

---

## Safety Architecture

Safety is a first-class system concern, not an afterthought.

```
LAYER 1 — Mandate bounds (hard limits)
  Every agent session creates a signed AgentMandate.
  PaymentGateway verifies mandate before every payment call.
  Spending caps enforced from DB records (daily + monthly).
  Mandate can be revoked instantly at any time.

LAYER 2 — HITL confirmation gates (soft limits with human override)
  Orchestrator enforces confirmation before irreversible actions.
  Risk-tiered: soft confirm (<$30, trusted vendor) → explicit CONFIRM (>$100)
  Agent confidence <0.8 → escalate regardless of amount.

LAYER 3 — Guardrails (structural enforcement)
  VendorGate: allowlist/blocklist checked before every merchant call.
  SpendingLimiter: checked before every purchase tool call.
  ConfidenceChecker: checked before every recommendation.

LAYER 4 — Audit log (immutable trail)
  Every agent action written to audit_log before execution, not after.
  Includes: agent, tool, arguments, timestamp, mandate_id.
  Never deleted. Used for debugging, compliance, user review.

LAYER 5 — Payment isolation (structural)
  payment_method_id lives only in UserProfile + PaymentGateway.
  Agents receive mandate_id strings only.
  Stripe tokenizes before merchant sees anything.
  PCI-DSS scope is Stripe's problem, not ours.
```

---

## Resilience & Failure Modes

| Failure | System response |
|---|---|
| Shopify MCP unavailable | Log error, inform user, suggest retry |
| UCP profile fetch fails | Fall back to direct adapter, cache failure for 60s |
| Stripe payment fails | Halt, log, escalate to user — never auto-retry purchases |
| Agent confidence <0.8 | Escalate to human before any action |
| Mandate expired | Refuse purchase, prompt user to renew mandate |
| Merchant blocklisted | Refuse silently (don't tell agent why — prevents manipulation) |
| DB write fails | Abort transaction, never execute payment without confirmed DB write |

---

## File Structure

```
agentic-commerce/
├── VISION.md                         ← product vision + goals (this project)
├── ARCHITECTURE.md                   ← this document
├── PLAN.md                           ← implementation plan (MVP phases)
│
├── main.py                           ← CLI entry point
├── requirements.txt
├── .env / .env.example
│
├── config/
│   ├── settings.py                   ← typed config, model constants
│   └── merchant_profiles.json        ← UCP profile stubs for known merchants (MVP)
│
├── models/                           ← UCP-vocabulary canonical types (STABLE CONTRACT)
│   ├── ucp_profile.py                ← UCPProfile, UCPCapability, UCPService, PaymentHandler
│   ├── product.py                    ← ProductResult, CartItem, RankedProduct
│   ├── order.py                      ← CheckoutSession, PurchaseOrder, OrderStatus, TrackingInfo
│   ├── user.py                       ← UserProfile, Address, BudgetConstraints
│   └── mandate.py                    ← AgentMandate, AuthResult
│
├── gateway/                          ← L4: routing layer (the scalability investment)
│   ├── merchant_gateway.py           ← try-UCP-first routing, client resolution, caching
│   └── payment_gateway.py           ← mandate resolution → payment token (Stripe)
│
├── ucp/                              ← L5: UCP protocol implementation
│   ├── discovery.py                  ← UCPProfileDiscovery (real fetch + stub fallback)
│   ├── client.py                     ← UCPClient ABC, UCPRestClient, UCPMCPClient
│   ├── signing.py                    ← RFC 9421 HTTP Message Signatures + JWK
│   ├── capabilities.py               ← CapabilityNegotiator, UCPCapability constants
│   ├── ap2_extension.py              ← AP2MandateEngine (local, no merchant dep in MVP)
│   └── webhooks.py                   ← webhook signature verification (target, not MVP)
│
├── adapters/                         ← L6: concrete merchant integrations
│   ├── shopify_mcp.py                ← ShopifyMCPAdapter (MVP primary)
│   ├── ucp_generic.py                ← GenericUCPAdapter (any UCP merchant)
│   └── stripe.py                     ← StripeAdapter (payment tokenization)
│
├── tools/                            ← L3: tool functions given to agents (STABLE INTERFACE)
│   ├── discovery_tools.py
│   ├── evaluation_tools.py
│   ├── purchase_tools.py
│   ├── tracking_tools.py
│   └── shared_tools.py               ← audit_log, get_user_profile, check_spending_limits
│
├── agents/                           ← L2: agent definitions + system prompts
│   ├── orchestrator.py
│   ├── discovery.py
│   ├── evaluation.py
│   ├── purchase.py
│   └── tracking.py
│
├── guardrails/
│   ├── spending.py
│   ├── vendors.py
│   └── confidence.py
│
├── storage/
│   ├── db.py                         ← TinyDB wrapper (swap for Postgres in target)
│   └── state.py                      ← SessionState (conversation history, active mandate)
│
└── cli/
    └── display.py                    ← Rich output helpers
```

---

## Adding a New Merchant — The Scalability Test

**Case 1: UCP-native merchant (e.g., Walmart ships `/.well-known/ucp`)**
```python
# No code changes. Just add domain to the router's search list.
gateway.add_merchant_domain("walmart.com")
# UCPProfileDiscovery auto-discovers capabilities, routes to UCPRestClient
```

**Case 2: Non-UCP merchant (e.g., Amazon)**
```python
# Write one adapter class. Zero changes to agents, tools, or gateway logic.
class AmazonAdapter(MerchantClient):
    async def search(self, query, filters) -> list[ProductResult]:
        # call Amazon API, map response → ProductResult (UCP types)
        ...

gateway.register_direct_adapter("amazon.com", AmazonAdapter(...))
```

**Case 3: MVP → target upgrade (Shopify becomes UCP-native)**
```python
# No code changes at all. When Shopify ships /.well-known/ucp:
# UCPProfileDiscovery.try_discover("mystore.myshopify.com") returns a real profile
# MerchantGateway automatically routes to UCPRestClient instead of ShopifyMCPAdapter
# ShopifyMCPAdapter becomes dead code, removed at cleanup
```

---

## Evolution Roadmap

```
PHASE 1 — MVP (now)
  ✓ CLI interface, local deployment
  ✓ Shopify MCP + Stripe test mode
  ✓ Single-item purchase flow with HITL
  ✓ Local AP2 mandate engine
  ✓ UCPRestClient wired but uses UCP reference server for testing
  ✓ RFC 9421 signing implemented (forward-compatible)
  ✓ Profile stubs for known merchants

PHASE 2 — Protocol Activation (as UCP adoption grows)
  ○ Real /.well-known/ucp discovery replaces stubs
  ○ Capability negotiation goes live
  ○ Webhook receiver replaces polling
  ○ Multi-merchant search via router

PHASE 3 — Scale (production)
  ○ Web/mobile UI + REST API
  ○ PostgreSQL + Redis replaces TinyDB
  ○ AP2 merchant-side extension support
  ○ Multi-item basket optimization
  ○ Autonomous replenishment within mandate limits
  ○ Multi-user profiles
```
