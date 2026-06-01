# Agentic Commerce — Architecture Diagrams

> Companion to ARCHITECTURE.md. All diagrams render in GitHub, VS Code (Markdown Preview), and any Mermaid-compatible viewer.
> Last updated: 2026-05-10

---

## 1. System Layer Overview

The full system from user to external APIs, showing all seven layers and how they connect.

```mermaid
graph TD
    subgraph L1["L1 · Conversation Layer"]
        User(["👤 User\nNatural Language"])
        CLI["Rich CLI\nStreaming · HITL Gates"]
    end

    subgraph L2["L2 · Agent Layer  —  claude-sonnet-4-6 / claude-haiku-4-5"]
        Orch["OrchestratorAgent\nsonnet-4-6 · streaming"]
        DA["DiscoveryAgent\nhaiku-4-5"]
        EA["EvaluationAgent\nhaiku-4-5"]
        PA["PurchaseAgent\nhaiku-4-5"]
        TA["TrackingAgent\nhaiku-4-5"]
    end

    subgraph L3["L3 · Tool Layer  —  stable interface contract"]
        DT["discovery_tools\nsearch_products · get_product"]
        ET["evaluation_tools\nrank_products · fetch_reviews"]
        PT["purchase_tools\ncreate_session · complete_order"]
        TT["tracking_tools\nget_order_status · initiate_return"]
        ST["shared_tools\naudit_log · validate_mandate"]
    end

    subgraph L4["L4 · Gateway Layer  —  routing + progressive enhancement"]
        MG["MerchantGateway\ntry-UCP-first · fallback to direct"]
        PG["PaymentGateway\nmandate → token · never raw card"]
    end

    subgraph L5["L5 · Protocol Layer"]
        UCP["UCP Clients\nUCPRestClient · UCPMCPClient\nRequestSigner RFC 9421\nCapabilityNegotiator"]
        AP2["AP2 Mandate Engine\nHMAC-signed · local · no merchant dep\nSpending caps · revocation"]
        STR["StripeAdapter\ntest mode · tokenization"]
    end

    subgraph L6["L6 · Integration Layer"]
        SMCP["ShopifyMCPAdapter\nMVP primary"]
        GUCP["GenericUCPAdapter\nany UCP merchant"]
        DISC["UCPProfileDiscovery\n/.well-known/ucp + stub fallback"]
    end

    subgraph L7["L7 · Data Layer"]
        DB["TinyDB\nmandates · orders\naudit_log · spend_records\nprofile_cache"]
    end

    subgraph EXT["External Systems"]
        SHP["Shopify\nMCP server"]
        STRIPE["Stripe API\ntest mode"]
        MERCH["UCP Merchants\nReference server · future"]
        ANTH["Anthropic API\nclaude-sonnet-4-6\nclaude-haiku-4-5"]
    end

    User --> CLI --> Orch
    Orch -->|tool calls| DA & EA & PA & TA
    DA --> DT
    EA --> ET
    PA --> PT & ST
    TA --> TT & ST
    DT & ET --> MG
    PT --> MG & PG
    TT --> MG
    ST --> DB
    MG --> UCP & DISC
    MG -->|fallback| SMCP
    PG --> AP2 & STR
    UCP --> SMCP & GUCP
    DISC -.->|try real fetch| MERCH
    DISC -.->|stub fallback| DB
    SMCP --> SHP
    GUCP --> MERCH
    STR --> STRIPE
    AP2 --> DB
    MG --> DB
    Orch --> ANTH
    DA & EA & PA & TA --> ANTH

    style L1 fill:#e8f4f8,stroke:#2196F3
    style L2 fill:#e8f5e9,stroke:#4CAF50
    style L3 fill:#fff8e1,stroke:#FFC107
    style L4 fill:#fce4ec,stroke:#E91E63
    style L5 fill:#f3e5f5,stroke:#9C27B0
    style L6 fill:#e0f2f1,stroke:#009688
    style L7 fill:#efebe9,stroke:#795548
    style EXT fill:#eceff1,stroke:#607D8B
```

---

## 2. Purchase Flow — Sequence Diagram

End-to-end lifecycle of a single purchase from user intent to order confirmation.

```mermaid
sequenceDiagram
    actor U as User
    participant O as Orchestrator
    participant D as DiscoveryAgent
    participant E as EvaluationAgent
    participant G as MerchantGateway
    participant M as Merchant
    participant P as PurchaseAgent
    participant S as Stripe
    participant DB as TinyDB

    U->>O: "Find running shoes under $150 size 10"
    O->>D: call_discovery_agent(query, filters)

    rect rgb(232, 245, 233)
        note over D,M: Discovery Phase
        D->>G: search_products(query, filters, domains)
        G->>M: GET /.well-known/ucp
        M-->>G: UCPProfile (capabilities, payment_handlers)
        note over G: Negotiate capabilities<br/>Select transport: REST or MCP
        G->>M: search via UCPRestClient / ShopifyMCPAdapter
        M-->>G: raw product data
        G-->>D: ProductResult[] (UCP-normalised)
        D-->>O: ProductResult[] + confidence_scores
    end

    rect rgb(255, 248, 225)
        note over E: Evaluation Phase
        O->>E: call_evaluation_agent(products, user_profile)
        E->>E: weighted scoring (preference 30%,<br/>price 25%, trust 20%, shipping 15%, reviews 10%)
        E-->>O: RankedProduct[] + risk_flags
    end

    O->>U: Display ranked product cards (Rich)
    U->>O: "Buy the top pick"

    rect rgb(252, 228, 236)
        note over O,U: HITL Safety Gate
        O->>O: Check risk tier\n(amount, merchant trust, mandate)
        O->>U: "⚠ Confirmation Required\n$129.99 · Nike.com\nType CONFIRM to proceed"
        U->>O: "CONFIRM"
        O->>DB: audit_log(action=purchase_approved)
    end

    rect rgb(243, 229, 245)
        note over P,DB: Purchase Phase
        O->>P: call_purchase_agent(product, mandate_id)
        P->>DB: validate_mandate(mandate_id, amount, vendor)
        DB-->>P: AuthResult(authorized=true, headroom=$370)

        P->>G: create_checkout_session(merchant_domain)
        G->>M: POST /checkout-sessions
        M-->>G: CheckoutSession(session_id, status=open)

        P->>G: update_checkout_session(session_id, items, buyer_info)
        G->>M: PUT /checkout-sessions/{id}
        M-->>G: CheckoutSession(total=$129.99, tax, shipping)

        P->>S: get_payment_token(payment_method_id, amount)
        note over P,S: payment_method_id resolved from mandate<br/>never visible to agent
        S-->>P: payment_token (opaque)

        P->>G: complete_order(session_id, "stripe", token)
        G->>M: POST /checkout-sessions/{id}/complete
        M->>S: charge via Stripe (Trust Triangle)
        S-->>M: charge confirmed
        M-->>G: PurchaseOrder(order_id, status=confirmed)

        P->>DB: save_order(order)
        P->>DB: record_mandate_spend(mandate_id, $129.99)
        P-->>O: PurchaseOrder
    end

    O->>U: ✓ Order confirmed! #ORD-12345\nEstimated delivery: 2–3 days
```

---

## 3. MerchantGateway — Routing Logic

How the gateway resolves which client to use for any merchant domain. This is the core progressive enhancement mechanism.

```mermaid
flowchart TD
    A(["Tool call\ne.g. search_products(domain)"]) --> B{"Client\ncached?\n60s TTL"}

    B -->|Yes| Z["Return cached client"]

    B -->|No| C["UCPProfileDiscovery\n.try_discover(domain)"]

    C --> D{"Real /.well-known/ucp\nfound and valid?"}

    D -->|"Yes (merchant is UCP-native)"| E["Parse UCPProfile\nNegotiate capabilities"]
    E --> F{"Preferred\ntransport?"}
    F -->|REST| G["UCPRestClient\n+ RequestSigner RFC 9421"]
    F -->|MCP| H["UCPMCPClient\nJSON-RPC 2.0"]
    F -->|A2A| I["UCPA2AClient\n(Phase 2+)"]

    D -->|"No (stub or missing)"| J{"Known direct\nadapter registered?"}
    J -->|Yes| K["ShopifyMCPAdapter\nor custom adapter"]
    J -->|No| L["⚠ Warn user\nmerchant not supported"]

    G & H & K --> M["Cache resolved client\n60s TTL"]
    M --> N(["Execute operation\nProductResult[] · CheckoutSession · etc."])

    style G fill:#e8f5e9,stroke:#4CAF50
    style H fill:#e8f5e9,stroke:#4CAF50
    style K fill:#fff8e1,stroke:#FFC107
    style I fill:#eceff1,stroke:#607D8B
    style L fill:#ffebee,stroke:#F44336

    classDef today fill:#e8f5e9,stroke:#4CAF50
    classDef future fill:#eceff1,stroke:#90A4AE,stroke-dasharray:5 5
    class G,H,K today
    class I future
```

---

## 4. Safety Architecture — Defence in Depth

Five independent layers of protection. A purchase must pass all five.

```mermaid
flowchart LR
    PI(["Purchase\nIntent"]) --> L1

    subgraph L1["Layer 1 · Mandate Bounds"]
        M1["AgentMandate\nHMAC-SHA256 signed\nper-tx · daily · monthly caps\ninstant revocation"]
    end

    subgraph L2["Layer 2 · HITL Gates"]
        M2["Risk-tiered confirmation\n< $30 trusted vendor → soft confirm\n> $100 → type CONFIRM\n> $500 → CONFIRM + summary panel"]
    end

    subgraph L3["Layer 3 · Guardrails"]
        M3["VendorGate\nallowlist / blocklist\n\nSpendingLimiter\nDB-backed cap check\n\nConfidenceChecker\n< 0.8 → escalate"]
    end

    subgraph L4["Layer 4 · Audit Log"]
        M4["Every agent action\nlogged before execution\nnever deleted\nmandate_id on every entry"]
    end

    subgraph L5["Layer 5 · Payment Isolation"]
        M5["payment_method_id\nnever in agent context\nPaymentGateway only\nStripe tokenises first"]
    end

    EX(["Execute\nPurchase"])

    L1 -->|"✓ mandate valid\nheadroom ok"| L2
    L2 -->|"✓ user confirmed"| L3
    L3 -->|"✓ vendor allowed\ncaps ok\nconfidence ok"| L4
    L4 -->|"✓ action logged"| L5
    L5 -->|"✓ token only\nno raw card"| EX

    L1 -->|"✗ revoked / expired\ncap exceeded"| FAIL(["🚫 Blocked\nInform user"])
    L2 -->|"✗ user cancelled\nor no response"| FAIL
    L3 -->|"✗ blocklisted\nor low confidence"| FAIL
    L5 -->|"✗ Stripe error"| FAIL

    style L1 fill:#e3f2fd,stroke:#1565C0
    style L2 fill:#e8f5e9,stroke:#2E7D32
    style L3 fill:#fff8e1,stroke:#F57F17
    style L4 fill:#fce4ec,stroke:#AD1457
    style L5 fill:#f3e5f5,stroke:#6A1B9A
    style FAIL fill:#ffebee,stroke:#C62828
    style EX fill:#e8f5e9,stroke:#2E7D32
```

---

## 5. Data Model Relationships

Core entities and how they relate. `payment_method_id` is shown isolated — it never crosses into agent-visible entities.

```mermaid
erDiagram
    UserProfile {
        string user_id PK
        string name
        string email
        string[] addresses
        string[] vendor_allowlist
        string[] vendor_blocklist
    }

    PaymentCredential {
        string payment_method_id PK
        string user_id FK
        string last_four
        string brand
    }

    AgentMandate {
        string mandate_id PK
        string user_id FK
        decimal max_amount
        decimal daily_cap
        decimal monthly_cap
        string[] allowed_categories
        string[] allowed_vendors
        datetime expiry
        bool revoked
        string digital_signature
    }

    UCPProfile {
        string merchant_domain PK
        string[] capability_namespaces
        string preferred_transport
        datetime cached_at
    }

    CheckoutSession {
        string session_id PK
        string merchant_domain FK
        decimal subtotal
        decimal tax
        decimal total
        string currency
        string status
    }

    PurchaseOrder {
        string order_id PK
        string session_id FK
        string mandate_id FK
        string merchant_domain
        decimal total
        string status
        string tracking_number
        datetime created_at
    }

    CartItem {
        string item_id PK
        string order_id FK
        string product_id
        string name
        decimal price
        int quantity
    }

    AuditEntry {
        string entry_id PK
        string mandate_id FK
        string agent
        string tool
        string action
        datetime timestamp
    }

    UserProfile ||--o{ AgentMandate : "grants"
    UserProfile ||--|| PaymentCredential : "has (isolated)"
    AgentMandate ||--o{ PurchaseOrder : "authorises"
    AgentMandate ||--o{ AuditEntry : "referenced in"
    CheckoutSession ||--|| PurchaseOrder : "becomes"
    PurchaseOrder ||--o{ CartItem : "contains"
    UCPProfile ||--o{ CheckoutSession : "serves"
```

---

## 6. Progressive Enhancement Timeline

What works today vs what activates as UCP merchant adoption grows.

```mermaid
timeline
    title Capability Activation as UCP Matures

    section MVP — Works Today
        Shopify MCP product search    : ShopifyMCPAdapter
        Stripe test payments          : StripeAdapter (test mode)
        Local AP2 mandates            : HMAC-signed, no merchant dep
        RFC 9421 request signing      : Implemented, forward-compatible
        Profile stubs                 : merchant_profiles.json fallback
        Order tracking                : Polling GET /order/{id}

    section Phase 2 — Unlocked by Merchant Adoption
        Real profile discovery        : /.well-known/ucp goes live
        Live capability negotiation   : UCPProfile drives routing
        UCPRestClient auto-activates  : GenericUCPAdapter for all UCP merchants
        Webhook-driven tracking       : Event-based, replaces polling
        Multi-merchant fan-out        : Router queries all registered domains

    section Target — Full Protocol Stack
        AP2 merchant-side extension   : dev.ucp.extensions.ap2 in profiles
        Verified mandate proofs       : Merchants validate AP2 headers
        A2A transport                 : Agent-to-agent direct flows
        Web and mobile UI             : REST API replaces CLI
        Autonomous replenishment      : Within mandate limits, no HITL
```

---

## 7. Agent Interaction Model

How the Orchestrator coordinates subagents via tool calls, and where humans stay in the loop.

```mermaid
flowchart TD
    U(["👤 User"]) -->|natural language| O["OrchestratorAgent\nclaude-sonnet-4-6\nstreaming"]

    O -->|"tool: call_discovery_agent()"| D["DiscoveryAgent\nhaiku-4-5\nown tool loop"]
    D -->|"ProductResult[]"| O

    O -->|"tool: call_evaluation_agent()"| E["EvaluationAgent\nhaiku-4-5\nown tool loop"]
    E -->|"RankedProduct[]"| O

    O -->|display options| U
    U -->|intent to purchase| O

    O -->|"HITL gate\n(blocks until response)"| GATE{"User\nconfirms?"}
    GATE -->|"CONFIRM"| P
    GATE -->|"cancel"| U

    O -->|"tool: call_purchase_agent()"| P["PurchaseAgent\nhaiku-4-5\nown tool loop"]
    P -->|"PurchaseOrder"| O

    O -->|display confirmation| U

    O -->|"tool: call_tracking_agent()\n(async, post-purchase)"| T["TrackingAgent\nhaiku-4-5\nown tool loop"]
    T -->|"TrackingInfo"| O
    O -->|status updates| U

    subgraph RULE["Design Rules"]
        R1["Agents never communicate\ndirectly with each other"]
        R2["All coordination via\nOrchestrator tool calls"]
        R3["Subagents return\nPydantic objects only"]
        R4["HITL gate blocks stream\nbefore irreversible action"]
    end

    style GATE fill:#fff8e1,stroke:#FFC107
    style O fill:#e8f5e9,stroke:#4CAF50
    style D fill:#e3f2fd,stroke:#1565C0
    style E fill:#e3f2fd,stroke:#1565C0
    style P fill:#e3f2fd,stroke:#1565C0
    style T fill:#e3f2fd,stroke:#1565C0
    style RULE fill:#eceff1,stroke:#90A4AE
```

---

*Diagrams render with [Mermaid](https://mermaid.js.org). Open this file in GitHub, VS Code with Markdown Preview Enhanced, or [mermaid.live](https://mermaid.live) to view rendered output.*
