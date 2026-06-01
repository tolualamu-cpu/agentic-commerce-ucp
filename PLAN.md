# Agentic Commerce — Implementation Plan

> Living document. Updated as implementation progresses.
> See VISION.md for goals. See ARCHITECTURE.md for system design.
> See TEST_TRACKER.md for the full test inventory (what / how / why per test).
> Last updated: 2026-05-11

---

## Guiding Principle

Build the abstraction layer correctly now. Use direct integrations (Shopify MCP, Stripe) behind it for MVP. As UCP merchant adoption grows, implementations swap automatically — agents and tools never change.

**Stability hierarchy (most stable → least stable):**
```
Data models (UCP vocabulary) → Tool signatures → Agent system prompts
→ Gateway routing → Protocol clients → Direct adapters → External APIs
```

---

## What's Available Now vs What Requires UCP Maturity

### Tier 1 — Build now (production-ready today)
- Shopify MCP for product search, cart, checkout
- Stripe SDK for payment tokenization + payment intents
- Local AP2 mandate engine (HMAC-signed, no merchant-side support needed)
- RFC 9421 request signing (implement now, forward-compatible)
- All agent logic, tool functions, guardrails

### Tier 2 — Stub now, activate as UCP matures
| Feature | MVP stub | Activates when |
|---|---|---|
| `/.well-known/ucp` discovery | Config stub (`merchant_profiles.json`) | Merchants publish profiles |
| Capability negotiation | Hardcoded defaults per known merchant | Real profiles available |
| UCP checkout API | Works against UCP reference server (test) | Merchant adoption |
| AP2 as merchant extension | Local mandate only | Merchant AP2 support |
| Request signature verification | We sign; merchants may not verify yet | Merchant-side RFC 9421 |

### Tier 3 — Skip for MVP
- Webhooks → use polling
- A2A transport → not needed for purchasing flow
- Identity Linking capability → use API keys
- Multi-user profiles → single local user

---

## Implementation Phases

### Phase 0 — Foundations (no external dependencies)

**Goal:** Core types, storage, mandate engine, signing — all testable without Stripe or Shopify.

- [x] `models/` — Pydantic v2 schemas (UCPProfile, ProductResult, CheckoutSession, PurchaseOrder, AgentMandate, UserProfile)
- [x] `storage/db.py` — TinyDB wrapper (tables: mandates, orders, audit_log, spend_records, profile_cache)
- [x] `storage/state.py` — SessionState (conversation history, active mandate, open sessions)
- [x] `ucp/signing.py` — RFC 9421 HTTP Message Signatures, JWK key generation (Ed25519)
- [x] `ucp/ap2_extension.py` — AP2MandateEngine: create/verify/revoke/record_spend (local HMAC, no merchant dep)
- [x] `guardrails/spending.py` — SpendingLimiter: cap enforcement from DB records
- [x] `guardrails/vendors.py` — VendorGate: allowlist/blocklist
- [x] `guardrails/confidence.py` — ConfidenceChecker: <0.8 escalation logic
- [x] `config/settings.py` + `.env.example`

**Test:** Unit tests for mandate signing/verification, spending cap enforcement, guardrails.
**Status (2026-05-11):** ✅ Complete — 31/31 tests passing (`tests/test_mandate.py`, `test_signing.py`, `test_guardrails.py`, `test_models.py`).

---

### Phase 1 — Gateway + Protocol Layer (UCP shape, Shopify heart)

**Goal:** MerchantGateway routes calls. UCP clients exist. Shopify MCP is the live implementation.

- [x] `gateway/base.py` — MerchantClient ABC (shared interface)
- [x] `ucp/capabilities.py` — UCPCapability constants, CapabilityNegotiator
- [x] `ucp/discovery.py` — UCPProfileDiscovery: real fetch + stub fallback from `merchant_profiles.json`
- [x] `config/merchant_profiles.json` — UCP profile stubs
- [x] `ucp/client.py` — UCPClient ABC + UCPRestClient (3-step checkout) + UCPMCPClient stub
- [x] `adapters/shopify_mcp.py` — ShopifyMCPAdapter + StubShopifyTransport (real MCP wired in Phase 3)
- [x] `adapters/stripe.py` — StripeAdapter: live test mode + offline tokeniser fallback
- [x] `gateway/merchant_gateway.py` — try-UCP-first routing, 60s client cache, direct adapter fallback
- [x] `gateway/payment_gateway.py` — mandate → payment_method_id → Stripe token (boundary)

**Test:** Run UCPRestClient against UCP reference server (clone https://github.com/Universal-Commerce-Protocol/ucp, run locally). ShopifyMCPAdapter against Shopify sandbox store.
**Status (2026-05-11):** ✅ Complete — 23 new tests passing (capabilities, discovery, UCP client checkout lifecycle with signing assertions, Shopify adapter, gateway routing + cache, payment gateway isolation). Total 54/54.

---

### Phase 2 — Tools Layer

**Goal:** Tool functions wrap the gateway. Agents can call them without knowing what runs underneath.

- [x] `tools/context.py` — ToolContext (dependency container — db, ap2, gateways, guardrails, user, session)
- [x] `tools/discovery_tools.py` — `search_products`, `get_product_details` (vendor gating, silent filtering)
- [x] `tools/evaluation_tools.py` — `rank_products` (weighted 30/25/20/15/10), `fetch_reviews`, `check_vendor_allowlist`, `compare_prices`
- [x] `tools/purchase_tools.py` — `validate_mandate`, `create/update/complete` lifecycle, `get_payment_token` (returns dict — never PaymentToken object), `save_order`, `record_mandate_spend`
- [x] `tools/tracking_tools.py` — `get_order_status`, `initiate_return`, `check_refund_status`
- [x] `tools/shared_tools.py` — `audit_log`, `get_user_profile` (agent-safe view), `check_spending_limits`

**Test:** Call each tool function directly in isolation (no agent needed).
**Status (2026-05-11):** ✅ Complete — 29 new tests passing (shared/discovery/evaluation/purchase/tracking). Total 83/83. Includes end-to-end purchase chain integration test that exercises all six purchase tools sequentially.

---

### Phase 3 — Agent Layer

**Goal:** Subagents wired to tool functions. Each agent runs its own tool loop. Orchestrator coordinates.

- [x] `agents/base.py` — BaseAgent tool-loop runner + inspect-based schema generation
- [x] `agents/prompts.py` — cached system prompts (one cache key per agent)
- [x] `agents/discovery.py` — DiscoveryAgent [claude-haiku-4-5]
  - Tools: `search_products`, `get_product_details`, `check_vendor_allowlist`
- [x] `agents/evaluation.py` — EvaluationAgent [claude-haiku-4-5]
  - Tools: `rank_products`, `fetch_reviews`, `compare_prices`
- [x] `agents/purchase.py` — PurchaseAgent [claude-haiku-4-5]
  - Tools: 7 purchase tools + `audit_log`
  - Never receives `payment_method_id` (asserted by `test_purchase_agent_never_sees_payment_method_id`)
- [x] `agents/tracking.py` — TrackingAgent [claude-haiku-4-5]
  - Tools: `get_order_status`, `initiate_return`, `check_refund_status`, `audit_log`
- [x] `cli/confirmation.py` — `ConfirmationProvider` protocol + `AutoConfirmProvider` (tests) + `classify_gate` tier function
- [x] `agents/orchestrator.py` — OrchestratorAgent [claude-sonnet-4-6]
  - Subagent tools: `call_discovery_agent`, `call_evaluation_agent`, `call_purchase_agent`, `call_tracking_agent`
  - Shared tools: `get_user_profile`, `validate_mandate`, `audit_log`, `check_spending_limits`
  - HITL confirmation gates:

  | Condition | Gate |
  |---|---|
  | Routine replenishment, <$30, trusted vendor | Soft confirm (Enter to proceed) |
  | First purchase or total >$100 | Explicit "Type CONFIRM" |
  | Total >$500 | "CONFIRM" + full order summary panel |
  | Agent confidence <0.8 | Escalation warning before any gate |
  | Merchant not in allowlist | Warn user, require explicit override |

**Status (2026-05-11):** ✅ Complete — 32 new tests passing (base agent, 4 subagents, orchestrator HITL, orchestrator e2e). Total 121/121. HITL gates enforced as Python control flow (not Claude tools) — `test_purchase_gate_deny_returns_cancelled_without_running_subagent` proves denied gates fully short-circuit the subagent.

---

### Phase 4 — CLI + Polish

**Goal:** Working end-to-end demo from terminal.

- [x] `cli/display.py` — Rich helpers
  - `display_welcome`, `display_products`, `display_checkout_summary`, `display_order`, `display_tracking`, `display_mandate_status`, `display_orders`
  - `RichConfirmProvider` — concrete `ConfirmationProvider` for the CLI (soft + explicit-CONFIRM gates)
  - Streaming callbacks: `on_text`, `on_tool_start`, `on_tool_end`, `on_gate` (wire `OrchestratorAgent.StreamingCallbacks`)
- [x] `main.py` — CLI entry point
  - Bootstrap: ephemeral or persisted keys (signing + AP2), Shopify stub adapter, MandateGateway/PaymentGateway wiring
  - Mandate auto-created: $500/$1000/$5000 caps, 24h expiry
  - REPL commands: `orders`, `track <id>`, `mandate`, `revoke mandate`, `audit`, `block <merchant>`, `exit`
  - Everything else routes to `OrchestratorAgent.run()`
  - Graceful errors when `ANTHROPIC_API_KEY` or `anthropic` SDK missing
- [x] `.env.example` finalised with all variables + setup instructions
- [x] `requirements.txt` finalised (already in place from Phase 0)

**Status (2026-05-11):** ✅ Complete — `python3 main.py` boots, every display helper renders, all 121 tests still pass. Phase 5 (tests for Phase 4) deferred per user's request — they will be added next.

---

### Phase 4b — Multi-merchant catalogue + safety hardening (live-test follow-up)

After live REPL testing, two classes of gaps were addressed:

- [x] `config/catalogue.py` — 3 demo merchants (Athletic Co, Audio Hub, Coffee Bar) with 15 products spanning all gate tiers (soft $14-$30, explicit $79-$329, full-summary $649), OUT_OF_STOCK items, cross-merchant overlap (earbuds appear at 2 merchants)
- [x] `main.py` — registers all 3 merchant adapters; user has a default shipping address; new `profile` REPL command
- [x] `cli/display.py` — `display_profile(user)` helper, RichConfirmProvider now accepts case-insensitive `confirm`
- [x] `tools/purchase_tools.py` — defensive injection: `update_checkout_session` auto-fills BuyerInfo from `user.default_shipping()` when agent omits it (closes the "order placed with no address" bug)
- [x] `agents/prompts.py` — ORCHESTRATOR prompt nudged toward default-to-all-merchants + comparison language; PURCHASE prompt requires `buyer` arg before update
- [x] Conversation memory across REPL turns (Orchestrator only — subagents stay stateless) + 40-entry soft cap
- [x] **Spend-limit override resistance test suite** — 9 tests proving defence-in-depth: jailbroken/malicious agent cannot route around mandate enforcement

**Status (2026-05-13):** ✅ Complete — 180/180 tests passing. Live REPL exercises every gate tier, multi-merchant comparison works, shipping address auto-injection plugged the order-without-address bug. Demo-ready.

---

---

### Phase 5 — Conformance + Hardening

- [ ] Run UCP conformance tests against local reference merchant server:
  - `protocol_test.py` — profile discovery, capability negotiation
  - `checkout_lifecycle_test.py` — create → update → complete session
  - `ap2_test.py` — mandate creation and spend verification
  - `idempotency_test.py` — duplicate request handling
- [ ] End-to-end demo run: full flow from CLI input to order confirmation
- [ ] Audit log review: every agent action captured with mandate reference
- [ ] Mandate edge cases: revoked, expired, cap exceeded — all handled gracefully

---

### Phase 6 — Single-merchant multi-item basket

**Status (2026-05-14):** ✅ Complete — 6 new tests (186/186 total). Multi-item basket works end-to-end: server-side total prevents agent under-reporting, single HITL gate shows basket sub-table, PurchaseAgent sends all items in one `update_checkout_session` call. Also fixed `FakeAnthropicClient.tool_inputs()` deduplication bug surfaced by these tests.

---

### Phase 6b — Gate Q&A + catalogue depth + agent tone (live-test follow-up)

Live REPL test of Phase 6 exposed 9 issues — 4 critical, 5 UX/polish. All fixed.

- [x] `cli/confirmation.py` — `GateResponse` trichotomy (`confirm` / `cancel` / `question`); `AutoConfirmProvider` supports scripted responses
- [x] `cli/display.py` — `RichConfirmProvider` returns `GateResponse`; gate prompt now invites questions; `on_tool_start` shows merchant name when one; no emojis in spinner labels
- [x] `agents/orchestrator.py` — gate loops on `question` responses up to `MAX_GATE_QUESTIONS=5`; new `_answer_question_at_gate` helper preserves basket state during Q&A; `last_discovered_products` populated after every discovery call; new `get_last_discovered_products` tool advertised to model
- [x] `agents/prompts.py` — `TONE_RULES` block appended to all 5 prompts (no emojis, no sales nudges, answer-and-stop); ORCHESTRATOR adds: named-merchant rule with examples, batched-discovery rule, `get_last_discovered_products` tool mention, "answer questions, don't nudge" section
- [x] `config/catalogue.py` — 9 new products: 3 mugs + 2 coffee variants at Coffee Bar, 2 headphone variants at Audio Hub, 2 shoe variants at Athletic Co. Real ranking comparisons now possible.

**Tests added (+31):**
- 7 gate Q&A loop tests (single-item + multi-item flows + bounds)
- 5 last-discovered cache tests (single query + multi query + tool exposure)
- 5 ranking quality tests (4 mugs, 3 shoes, 3 beans, OOS flag, solo input)
- 8 agent tone tests (TONE_RULES present in every prompt, no stray emojis)
- 4 catalogue regression tests (4 mugs, 3 headphone tiers, multiple shoes, multiple beans)
- 2 additional multi-merchant coverage extensions

**Status (2026-05-14):** ✅ Complete — 217/217 tests passing. The "question-at-gate is cancellation" bug is fixed; the agent retains the basket through Q&A; ranking has real depth in the catalogue; tone rules in every prompt suppress emojis and sales nudges.

---

### Phase 6c — Live-test bug-fix + 120-journey shopper audit

Second round of live REPL testing surfaced 6 more bugs, including a hard crash (API 400) on the gate Q&A path. Also acted on the user's directive: *"think like a shopper going through all the phases. come up with at least 100 distinct paths."*

- [x] **Bug #1 fixed: API 400 on confirm after gate Q&A** — `_answer_question_at_gate` was appending user/assistant text turns to `ctx.session.conversation` mid-tool-execution, which corrupted the `tool_use`→`tool_result` adjacency the Anthropic API requires. Now buffered in `self._pending_gate_history` and flushed to session.conversation AFTER `super().run()` completes.
- [x] **Bug #2 fixed: agent denying its own prior statements** — `_answer_question_at_gate` now receives the last 8 conversation turns + pending gate Q&A as context. Helper sees what the orchestrator said, can reference it honestly.
- [x] **Bug #3/#4 fixed: hallucinated basket modifications** — gate-helper system prompt strengthened: "The basket is FIXED. Do NOT show 'new basket total' or 'removing X leaves you with Y' calculations. If user asks to change the basket, say: 'To change the basket, type cancel, then re-request with the new items.'"
- [x] **Bug #6 fixed: compact re-prompt** — `RichConfirmProvider` tracks a per-gate signature; second+ prompts at the same gate show a one-line "Gate still open" instead of re-rendering the full panel.
- [x] **Bug #7 fixed: anti-editorialising rule** — TONE_RULES now explicitly bans phrasings like "straightforward", "easy choice", "the obvious pick".
- [x] **`docs/USER_JOURNEYS.md`** — 120 catalogued shopper journeys across 8 sections (Discovery, Evaluation, Single-item, Multi-item, Payment, Tracking, Account, Edge cases). Each tagged with status: ✅ auto-tested / 🟢 manual-only / 🟡 partial / 🔴 broken / 📋 documented-only.
- [x] **`tests/test_user_journeys.py`** — 76 deterministic journey tests covering every automatable path.

**Status (2026-05-14):** ✅ Complete — 293/293 tests passing. Gate Q&A is now crash-free and contextually aware. The 120-journey catalogue is the new system-test contract — any future change must keep them all green.

---

### Phase 6d — In-place basket editing at the gate (live-test follow-up)

User feedback: *"typing cancel and re-requesting is a really poor user experience"*. Built genuine basket editing at the confirmation gate. Users can now type "remove the tumbler", "change to 2 mugs", or "add the Ethiopia beans" directly at the gate; the system applies the mutation, recomputes the total, re-validates the mandate, and re-presents the gate — all without losing the purchase context.

- [x] **`GateAction` dataclass** in `agents/orchestrator.py` — 5 intents: `answer`, `remove`, `change_quantity`, `add`, `refused`
- [x] **`_handle_gate_input`** — replaces `_answer_question_at_gate`. Makes a Claude call with a strict JSON output contract; falls back to plain-text "answer" if model doesn't comply
- [x] **`_apply_gate_action`** — Python-side mutation logic with **customer-friendly safety-rail messages** for every refusal case
- [x] **`_friendly_cap_refusal`** — generates messages that include WHAT the user tried, WHY it didn't go through (which cap was hit, by how much), and WHAT they can do instead
- [x] **Mandate re-validation on every mutation** — basket changes go through `ap2.verify_and_authorize` so caps stay enforced even mid-session
- [x] **Discovery-cache verification** — `add` intents must reference an item the user has actually seen, preventing the agent from inventing products
- [x] **Empty-basket auto-cancellation** — removing the last item cancels the flow with a friendly message rather than leaving an invalid state
- [x] **Gate prompt copy** updated: *"Type CONFIRM, cancel, ask a question, or edit your basket (e.g. 'remove the tumbler', 'change to 2 mugs', 'add the Ethiopia beans')"*
- [x] **ORCHESTRATOR prompt** updated to advertise basket editing capability

**Tests added (+56):**
- 19 basket-edit unit tests (normalise, compute_total, apply for each intent + every refusal path)
- 14 end-to-end mutation flows (remove, change_quantity, add from cache, refusals for not-in-cache / not-in-basket / cap-exceeded / negative-qty / invalid-price / mandate-revoked-mid-flow / multiple sequential edits / empty-basket cancel / plain-text fallback)
- 3 friendly cap-refusal message format assertions
- 20 user journey part-2 tests (J007/J008/J012-J013/J017-J020/J044/J048/J058-J059/J061/J084/J095/J101/J107/J114/J118)

**Journey catalogue:** added Section 9 (J121-J140) covering the 20 new basket-edit scenarios. J069, J112, J113 moved from 🟡 to ✅.

**Status (2026-05-14):** ✅ Complete — 349/349 tests passing. Basket editing is seamless, every safety rail returns a customer-friendly explanation instead of an abrupt cut, and the AP2 enforcement layer re-validates on every mutation.

---

### Phase 6e — Basket-edit UX refinements A–F (post-critique)

User critique of Phase 6d's refusal messages: *"is this really a good user experience?"* Rethought all four refusal paths plus added swap, clear, and search-from-gate.

**Refinement A — Cap-exceeded with concrete drop suggestions:**
`_friendly_cap_refusal` now calls `_suggest_drops_to_fit` which finds basket items whose `line_total ≥ overage`, sorts cheapest-sacrifice first, and lists up to 3 with post-remove totals. *"You could remove Mug ($14) → basket becomes $500 exactly."* No cap override.

**Refinement B — Numbered disambiguation:**
`_format_basket_numbered` generates a numbered list with product_ids for every disambiguation message. `_resolve_numeric_reference` handles "1"/"remove 2"/"#3" in Python (no LLM round-trip, deterministic for agent callers too). The regex is strict — "cof_001" is NOT mistaken for basket position #1.

**Refinement C — Search-and-add sub-flow:**
When the model returns "I'll search for that now", `_search_and_offer_sub_flow` runs `search_products` on the current merchant only (security R5), caps at 8 results, populates the discovery cache, and presents a numbered picker. Picker state tracked in `pending_search_query`; numeric input goes to cache picker, not basket removal, while the sub-flow is active.

**Refinement D — Empty-basket non-cancel:**
Removing the last item now enters an empty-basket warning state instead of auto-cancelling. CONFIRM on empty basket is a no-op with a clear message. User can add items (via sub-flow) or explicitly cancel.

**Refinement E — Clear-basket intent:**
New `clear` GateAction kind. "clear basket"/"empty my basket" empties all items atomically and transitions to the empty-basket state (Refinement D).

**Refinement F — Swap intent:**
New `swap` GateAction kind. "swap mug for large mug" removes `target_product_id` and adds `new_item` as one atomic operation — single mandate re-validation, single audit entry, price delta shown clearly in the message.

**Dual-user framing:** picker works for both humans (numbered list) and agent callers (bare integer strings, machine-parseable product_ids in every message).

**Tests added (+42):** 6 cap-refusal, 8 disambiguation, 7 search-sub-flow, 3 empty-basket, 4 clear, 6 swap, 4 dual-user picker, 4 security.

**Status (2026-05-15):** ✅ Complete — 391/391 tests passing. Shopper paths S2–S7 all ✅.

---

### Phase 6f — Post-refinement live-test fixes (15 issues)

A second live REPL session of the refined basket-edit UX exposed 15 issues
across 9 functional areas, including critical security gaps (agent didn't
know its own mandate caps, accepted user-asserted budgets). All fixed.

**Critical fixes:**
- [x] **#1 + #2 — Mandate awareness** — new `get_active_mandate_summary`
      tool wired into orchestrator; ORCHESTRATOR_TEMPLATE explicitly tells
      the model to treat the mandate as the source of truth; TONE_RULES bars
      accepting user-asserted budgets. When user asserts a different limit,
      the agent responds with the mandate's actual caps in neutral framing
      ("Your spending limit is set at $X. If you'd like to change that,
      you'll need to update your mandate.") — no security-internal leaks.
- [x] **#3 — Stub merchant robustness** — `StubShopifyTransport` now defensively
      normalises every line item (price/quantity coercion, drops qty=0,
      handles malformed inputs gracefully). Verbose stderr logging via
      `STUB_VERBOSE` env var. `complete_cart` raises explicit error if cart
      is empty (security backstop against zero-amount transactions).

**High-priority UX fixes:**
- [x] **#4 + #5 + #7 — Empty-basket truly indefinite** — gate loop now
      tracks `non_empty_questions` separately from `total_iterations`; empty
      states don't count toward `MAX_GATE_QUESTIONS` cancellation. Hard
      ceiling of 50 iterations as absolute backstop. Empty basket NO LONGER
      renders the formal "PURCHASE CONFIRMATION REQUIRED" Rich Panel —
      compact prompt only. Cancellation status differentiates `gate_closed`
      (timeout) from `cancelled_by_user`.
- [x] **#6 — Search-from-gate heuristic** — `_looks_like_search_intent`
      detects "I'll search", "Let me look up", etc. Replaces brittle exact-
      string match.
- [x] **#12 — Cross-merchant recovery** — helper prompt updated to offer
      concrete recovery path when user mentions a different merchant.

**Polish fixes:**
- [x] **#8 + #9 — Markdown forbidden** — TONE_RULES forbids markdown
      syntax in agent prose (no `**bold**`, no `# headings`, no ASCII pipe
      tables).
- [x] **#10 — Swap preserves position** — `_apply_gate_action` for swap now
      substitutes in-place via list comprehension instead of append-to-end.
- [x] **#11 — Compact empty re-prompt** — `empty_banner_shown` flag
      ensures the full warning shows once, not on every loop.
- [x] **#13 — Single-item cap-refusal copy** — special-case branch:
      "Your basket has only one item ($X), so there's nothing to drop."
- [x] **#14 — Numeric disambiguation enforced** — TONE_RULES requires
      1/2/3 labels (NOT A/B/C) so the numeric resolver works.
- [x] **#15 — Hint suppression** — `last_assistant_gave_hint` flag scaffold
      in place for future tightening (currently still emits hint).

**Tests added (+34):**
- 8 mandate-awareness (tool, headroom, revoked/expired, prompt content, tone)
- 7 stub-merchant robustness (qty, malformed, empty, verbose)
- 4 empty-basket indefinite + panel suppression
- 7 search heuristic (positive + negative)
- 2 swap position stability
- 1 single-item cap-refusal phrasing
- 1 numeric disambiguation prompt rule
- 4 other

**Status (2026-05-15):** ✅ Complete — 425/425 tests passing. Live REPL
flow that previously cancelled mid-conversation now stays open indefinitely
on empty-basket browsing. User-asserted budgets no longer override mandate
caps. Stub merchant correctly handles quantity > 1 without crashing.

**Goal:** Let a user say *"buy a coffee mug, a tumbler, and a bag of beans from Coffee Bar"* and have one checkout session contain all three items, gated by a single combined-total HITL prompt. Multi-merchant baskets remain out of scope (deferred to a future Phase 7 — see "Not in this phase" below).

**Why now:** The data models (`CheckoutSession.line_items`, `PurchaseOrder.items`) and tool layer (`update_checkout_session` already accepts `list[CartItem]`) were built multi-item from day one. What's missing is the **agent + HITL plumbing** so Claude can actually compose a basket and the user can confirm it as one decision.

**Why single-merchant only:** Multi-merchant baskets introduce hard problems — partial-failure rollback (one merchant succeeds, another fails — refund?), per-merchant vs combined gates, parallel session lifecycle. Those need real-user design input. Single-merchant is a clean 1-2 day scope with no new architectural questions.

#### Plumbing changes

- [ ] `agents/orchestrator.py` — update `call_purchase_agent` tool schema:
  - Replace single `product_id, name, price` fields with `items: list[{product_id, name, price, quantity}]`
  - Keep `amount` as the total (sum of `price × quantity`) so HITL classification works unchanged
  - Keep `merchant_domain` singular — basket must be at one merchant
- [ ] `agents/prompts.py` — PURCHASE prompt:
  - Replace *"a single purchase"* with *"a basket of 1+ items at one merchant"*
  - Add rule: *"Every item in the basket must be at the same `merchant_domain`. If the user wants items from multiple stores, ask the Orchestrator to invoke you separately for each merchant."*
- [ ] `agents/prompts.py` — ORCHESTRATOR prompt:
  - Add: *"When the user adds multiple items, call `call_purchase_agent` once with the full list — do NOT call it per-item."*
  - Add: *"If the user wants items from multiple merchants in one breath, explain you'll need to process one merchant at a time, then proceed sequentially."*

#### HITL gate changes

- [ ] `cli/confirmation.py` — `GateData`:
  - Add `items: list[dict]` field (each `{name, quantity, price, line_total}`) so the gate panel can list the basket
  - `classify_gate` unchanged — still uses the combined `amount`
- [ ] `cli/display.py` — `RichConfirmProvider.explicit_confirm`:
  - When `gate.items` has > 1 entry, render a sub-table inside the confirmation panel showing each line
  - Total stays prominent

#### Purchase chain changes

- [ ] `tools/purchase_tools.py` — `record_mandate_spend` already records one row per order_id; no change needed since the basket = one order
- [ ] Orchestrator's `_call_purchase`:
  - Compute `total_amount` from `items` (server-side, not from the model's claim — guards against agent under-reporting to evade gate tier)
  - Pass the full item list through to `PurchaseAgent` brief

#### Tests to add

- [ ] `tests/test_orchestrator.py::test_multi_item_basket_routes_to_single_gate` — orchestrator calls `call_purchase_agent` with 3 items at one merchant → one gate fires with combined total → one purchase chain executes
- [ ] `tests/test_orchestrator.py::test_multi_item_basket_rejects_cross_merchant_in_one_call` — items array spans 2 merchants → tool returns failed (forces orchestrator to split)
- [ ] `tests/test_orchestrator.py::test_agent_cannot_under_report_total_to_evade_gate` — orchestrator computes total from items, ignoring the model's `amount` field if it disagrees → gate tier reflects true total
- [ ] `tests/test_purchase_agent.py::test_basket_with_three_items_completes_in_one_session` — single `update_checkout_session` call with all 3 items, single `complete_order`, single audit chain
- [ ] `tests/test_cli_display.py::test_confirm_panel_lists_basket_items` — multi-item gate renders the per-line table
- [ ] `tests/test_spend_limit_override_resistance.py::test_basket_total_enforced_against_per_tx_cap` — basket sum exceeds `max_amount` → blocked even if individual items are under cap

Expected: 180 → ~190+ tests.

#### Live verification

```bash
python3 main.py
> Buy a coffee mug, a travel tumbler, and the Ethiopia beans from Coffee Bar
↳ Discovery returns all three from Coffee Bar
↳ One gate fires: $14 + $28 + $18 = $60 → explicit tier
↳ One CONFIRM → one PurchaseOrder with 3 line items
↳ `orders` command shows order with 3 items
```

#### Not in this phase (deferred to Phase 7+)

- **Multi-merchant baskets** — buying from 2+ merchants atomically. Needs design: how to handle partial failure? Per-merchant gates or combined? Sequential vs parallel session lifecycle? Refund-rollback semantics? These are product decisions, not engineering ones — needs real-user input.
- **Variable quantities at gate time** — gate panel won't yet let user say "actually make that 2 mugs instead of 1" without restarting the flow.
- **Substitution suggestions** — if one item OOS, suggest alternatives within the basket.
- **Basket persistence across conversation turns** — basket is per-purchase; if the user abandons and comes back, no "saved basket" exists.

---

## Phase 7 — Web UI (COMPLETE)

Status: ✅ shipped 2026-05-16. Full suite 498/498.

Consumer-facing FastAPI + Jinja + HTMX site that wraps the existing
agent stack — backend code untouched. Adds 73 web tests across six
sub-phases (7a-7f). See [docs/WEB_DEVELOPMENT.md](docs/WEB_DEVELOPMENT.md)
for full details: architecture, API contract, click-action catalogue,
extension guide, security model.

Sub-phases:

- **7a Foundation** — `web/app.py`, cookie-based `WebSession` (one
  `ToolContext` + one `OrchestratorAgent` per browser), products router
  (home / search / product detail), Tailwind via CDN, dual HTML/JSON
  format via `Accept` header. (21 tests)
- **7b Chat + SSE** — `POST /chat` + `GET /chat/stream`,
  `_chat_sidebar.html` with EventSource binding, `web/callbacks.py`
  adapter from `StreamingCallbacks` to a per-session async queue. (10 tests)
- **7c Cart clicks** — `POST /cart/add|remove|quantity|clear`,
  `_cart_drawer.html`. Every click appends a `[via UI click] …` note to
  `session.conversation`. (14 tests)
- **7d Gate** — `WebsocketConfirmProvider` (implementing the existing
  `ConfirmationProvider` Protocol), `/gate/ws` bridge router,
  `_gate_modal.html` with CONFIRM/cancel/inline-question. (9 tests)
- **7e Account** — profile (payment redacted), mandate (caps + revoke),
  orders (list + detail + return-via-button), audit. (14 tests)
- **7f Polish + docs** — `_picker_overlay.html` for the search sub-flow,
  `_toast.html` notification stack, `docs/WEB_DEVELOPMENT.md`. (5 tests)

Boot: `uvicorn web.app:app --reload`.

---

## Environment Variables

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
STRIPE_TEST_KEY=sk_test_...          # test mode for MVP
SHOPIFY_ACCESS_TOKEN=...             # Shopify sandbox store Admin API token
SHOPIFY_SHOP_DOMAIN=...              # e.g., dev-store.myshopify.com
AGENT_PRIVATE_KEY_PEM=...            # RFC 9421 signing key (generate at setup)
AGENT_KEY_ID=...                     # identifier for JWK lookup
AP2_SIGNING_KEY=...                  # 32-byte hex, HMAC key for mandate signatures
```

Node packages (installed at runtime via npx, no global install needed):
- `@shopify/dev-mcp@latest` — Shopify MCP server
- (Stripe MCP optional — Stripe SDK used directly in MVP)

---

## Key Decisions Log

| Decision | Rationale |
|---|---|
| UCP vocabulary as internal types, not Shopify/Stripe types | Stable interface; swapping adapters never ripples upward |
| MerchantGateway try-UCP-first pattern | Progressive enhancement without code changes as UCP adoption grows |
| AP2 mandate engine runs locally in MVP | No merchant-side dependency; full mandate safety still enforced |
| RFC 9421 signing implemented now | Forward-compatible; costs nothing; required for UCP compliance |
| Profile stubs in `merchant_profiles.json` | Simulates `/.well-known/ucp` for development without real merchant support |
| Polling instead of webhooks for MVP | Eliminates infrastructure requirement (public endpoint, webhook receiver) |
| claude-haiku-4-5 for subagents | Fast + cheap for focused single-task agents; sonnet-4-6 reserved for orchestrator reasoning |
| Prompt caching on all system prompts | Reduces cost on repeated agent invocations |
| TinyDB for MVP storage | Zero setup, file-based, easy to inspect; swap for Postgres when multi-user needed |
| A2A transport skipped for MVP | Not needed for purchasing flow; adds complexity without v1 benefit |
