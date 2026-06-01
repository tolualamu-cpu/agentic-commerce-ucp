# Test Tracker — Agentic Commerce

> Living index of every test in the suite. Updated as tests are added/removed.
> Last updated: 2026-05-13
> Current status: **425 / 425 passing** — Phase 0–6 + basket-edit refinements A–F + post-refinement fixes (15 issues from live testing) + 140+ catalogued user journeys
> See `docs/USER_JOURNEYS.md` for the full shopper journey catalogue.

Run the full suite:
```bash
python3 -m pytest tests/ -v
```

---

## Reading this document

Each test row answers three questions:

| Column | Meaning |
|---|---|
| **What** | The specific behaviour or invariant the test asserts |
| **How** | The mechanism — fixture, mock, fake transport, mutation, etc. |
| **Why** | The architectural claim or safety property the test protects. If this test fails, what real-world failure becomes possible? |

A test that can't answer all three should be deleted or rewritten.

---

## Test pyramid — coverage by architectural layer

```
                                                 # tests
Schemas (canonical types)                            6
Mandate engine (AP2)                                13      ← spending authority
Signing (RFC 9421)                                   4      ← UCP wire compliance
Guardrails (defence in depth)                        8
                       ─── Phase 0 / Phase 1 line ───
Capabilities (CapabilityNegotiator)                  4
Discovery (UCPProfileDiscovery)                      4      ← MVP→production seam
UCP client (UCPRestClient)                           3      ← wire format vs UCP spec
Shopify adapter                                      2      ← vocabulary normalisation
Merchant gateway (routing)                           5      ← scalability claim
Payment gateway (mandate→token)                      5      ← payment isolation claim
                       ─── Phase 1 / Phase 2 line ───
Shared tools (audit, profile, limits)                3
Discovery tools (search + gating)                    5
Evaluation tools (weighted ranking)                 10
Purchase tools (full chain + isolation)              5      ← end-to-end Phase 2 proof
Tracking tools (status, returns, refunds)            6
                       ─── cross-phase integration ───
End-to-end journeys (Phase 0→2 over both adapters)   6      ← whole-system proof
                       ─── Phase 2 / Phase 3 line ───
Base agent (tool loop, schema, parsing)             10
Discovery agent                                      3
Evaluation agent                                     2
Purchase agent (chain + isolation)                   3
Tracking agent                                       2
Orchestrator (HITL tiers + dispatch)                 9
Orchestrator e2e (Claude-mocked full journey)        3      ← agentic-system proof
Conversation memory + history cap                    2      ← multi-turn coherence
                       ─── Phase 3 / Phase 4 line ───
Multi-merchant + cross-merchant comparison           6      ← scalability tests
Buyer-info defensive injection                       2      ← shipping-address bug fixed
CLI display (renderers + RichConfirmProvider)       25      ← Phase 4 visual layer
Main REPL command dispatch                          15      ← Phase 4 entry-point coverage
Spend-limit override resistance                      9      ← defence-in-depth invariant
                       ─── Phase 4b / Phase 6 line ───
Phase 6 multi-item basket                            6      ← single-merchant basket
                       ─── Phase 6 / live-test follow-up ───
Gate question-loop (single + multi item)             7      ← question-not-cancel
Last-discovered cache + new tool                     5      ← no re-search waste
Ranking quality (4 mugs, 3+ shoes, beans)            5      ← real rank_products coverage
Agent tone (no emoji + no sales nudge in prompts)    8      ← prompt-level enforcement
Catalogue regression (4 mugs, 3 tiers, etc.)         4      ← lock new minimums
Multi-merchant updates (incl. coffee bean variants)  +2     ← extended
                       ─── Live-test bugs + 120-journey audit ───
User journeys (8 sections, 120 catalogued, 76 auto)  76     ← full shopper catalogue
                       ─── in-place basket editing at gate ───
Basket-edit unit tests (helpers + apply logic)      19     ← _normalise/_compute/_apply
Basket-edit e2e flows (mutate + cap refusal)        14     ← end-to-end with mutations
Friendly cap-refusal messages (per-tx/daily/etc.)    3     ← customer-friendly safety rails
User journeys part 2 (feasible 🟢 → ✅)              20     ← bumping prior manual journeys
                       ─── Basket-edit UX refinements A–F ───
Refinement A — cap-exceeded with droppable-item suggestions   6
Refinement B — numbered disambiguation + numeric resolver      8
Refinement C — search-and-add sub-flow (inline picker)         7
Refinement D — empty-basket non-cancel state                   3
Refinement E — clear-basket intent                             4
Refinement F — swap intent (atomic remove+add)                 6
Dual-user picker (human + agent patterns)                      4
Security (merchant isolation, cap re-validation, etc.)         4
                       ─── Post-refinement live-test fixes (15 issues) ───
Mandate awareness (Fix #1/#2)                                  8
Stub merchant robustness (Fix #3)                              7
Empty-basket indefinite + no formal panel (#4/#5/#7)           4
Search heuristic (#6) — phrasing tolerance                     7
Swap position stability (#10)                                  2
Single-item cap-refusal phrasing (#13)                         1
Numeric disambiguation enforced by prompt (#14)                1
Other (cap suggestion, prompt content)                         4
                                                   ────
                                                   425
```

---

## Phase 0 — Foundation tests

### [tests/test_models.py](tests/test_models.py) · 6 tests

Catches contract violations in the UCP-vocabulary types before any downstream code is built on them.

| Test | What | How | Why |
|---|---|---|---|
| `test_user_profile_strips_payment_method` | `UserProfile.agent_safe_view()` does not include `payment_method_id` | Construct profile with `pm_secret_123`; assert key absent from returned dict | Guards the **payment isolation rule** at the source. The agent layer must never see Stripe IDs |
| `test_ucp_profile_capability_lookup` | `UCPProfile.preferred_transport()` picks REST when available, `has_capability()` is false for missing namespaces | Build profile with REST service, no capabilities | Routing decisions in MerchantGateway depend on these helpers — wrong return = wrong client selection |
| `test_cart_item_line_total` | `CartItem.line_total` = `price × quantity` | `Decimal("50") × 3 == Decimal("150")` | Pricing arithmetic is Decimal-correct (no float drift in money) |
| `test_product_confidence_score_bounds` | Confidence score stays in `[0.0, 1.0]` | Pydantic `Field(ge=0, le=1)` validation | Bad scores would break the <0.8 escalation rule |
| `test_mandate_status_transitions` | ACTIVE → REVOKED (flag) and ACTIVE → EXPIRED (time) transitions | `model_copy(update={...})` and shifted expiry | The three-state model is the contract every downstream check assumes |
| `test_checkout_session_defaults` | Default status is OPEN; default subtotal is Decimal("0") | Build minimal session | Defensive defaults so partial responses don't crash downstream |

---

### [tests/test_mandate.py](tests/test_mandate.py) · 13 tests

AP2 is the single source of spending authority. Bug here = agent spends money it shouldn't.

| Test | What | How | Why |
|---|---|---|---|
| `test_create_and_sign` | Mandate creation produces a non-null HMAC signature; `verify_signature()` returns True | Create mandate, inspect `digital_signature`, call verify | Signature is the tamper-detection primitive — must produce a valid one |
| `test_tampered_signature_detected` | Mutating `max_amount` after signing invalidates the signature | Create mandate, mutate field, re-verify → False | An attacker raising their own cap must be detected |
| `test_authorize_happy_path` | Authorised purchase returns `authorized=True`, correct headroom math | Verify amounts in `headroom_per_tx` / `headroom_daily` | The happy path's headroom values feed UI / agent reasoning — wrong math = wrong agent decisions |
| `test_per_transaction_cap` | Amount > max_amount → rejected with `exceeds_per_transaction_cap` | Mandate cap = $100, request $150 | Per-tx cap is the first-line spending guardrail |
| `test_daily_cap_enforced` | Existing spend record + new request > daily cap → rejected | Record $80 spend; request $50 against $100 daily cap | Daily caps must aggregate across the day, not per-call |
| `test_monthly_cap_enforced` | Existing spend record + new request > monthly cap → rejected | Same pattern, monthly window | Monthly cap is the long-window safety net |
| `test_vendor_blocked` | Request against blocked vendor → rejected | `blocked_vendors=["sketchy.com"]`; request that vendor | User blocklist is honoured at the mandate level |
| `test_vendor_allowlist` | Non-empty allowlist restricts to listed vendors; off-list → rejected | Allow `nike.com`, request `adidas.com` | Scope-bound mandates ("only buy from these merchants") work |
| `test_category_allowlist` | Off-category request → rejected | Allow apparel/electronics, request firearms | Category scoping prevents drift into prohibited spend |
| `test_revocation_takes_effect_immediately` | After revoke, next authorisation fails with `mandate_revoked` | Revoke, then re-authorise | Revocation must be instant, not eventually-consistent |
| `test_expired_mandate_rejected` | Authorising after expiry → `mandate_expired` | Time-shifted `now` parameter | Time-bounded authority is enforced (deterministically — no real sleep) |
| `test_mandate_not_found` | Bad mandate ID → graceful failure with reason | Authorise non-existent ID | No crashes on bad input — caller surfaces the reason cleanly |
| `test_spend_records_aggregate_correctly` | Multiple spend records sum correctly for cap math | Two $100 records, request $150 → daily cap fail; request $99 → pass | The DB aggregation behaviour matches the cap-enforcement logic |

---

### [tests/test_signing.py](tests/test_signing.py) · 4 tests

RFC 9421 is forward-compatible. Malformed signatures = every UCP merchant rejects us the moment they turn on verification.

| Test | What | How | Why |
|---|---|---|---|
| `test_sign_and_verify_roundtrip` | Sign → verify with same JWK returns True; required headers present | `generate_keypair()` → sign → verify | The basic correctness loop for RFC 9421 |
| `test_tampered_body_fails_verify` | Mutating body after signing → verification fails | Re-compute Content-Digest for mutated body to simulate tamper | Content-Digest binding to signature actually works |
| `test_signature_input_includes_components` | `Signature-Input` header covers `@method`, `@target-uri`, `host`, plus `keyid` and `alg="ed25519"` | String assertions on header | RFC 9421 conformance — merchants will check these fields exist |
| `test_wrong_key_fails_verify` | Signing with key A, verifying with public B → False | Two independent keypairs | Cryptographic identity actually means something |

---

### [tests/test_guardrails.py](tests/test_guardrails.py) · 8 tests

Defence in depth. Even if AP2 is bypassed, these structural checks block bad calls.

| Test | What | How | Why |
|---|---|---|---|
| `test_spending_limiter_passes_under_cap` | Independent cap check (not AP2) succeeds when under | Create mandate via AP2, call SpendingLimiter directly | The limiter is a separate enforcement layer — must work standalone |
| `test_spending_limiter_blocks_over_per_tx` | Limiter blocks > per-tx cap | $150 vs $100 cap | Redundant per-tx check survives even if AP2 is bypassed |
| `test_spending_limiter_aggregates_daily` | Limiter respects existing spend records | $90 prior + $20 new vs $100 daily | DB-backed aggregation works in the standalone layer too |
| `test_vendor_gate_user_blocklist` | User blocklist blocks merchant | `vendor_blocklist=["bad.com"]` | UserProfile blocklist is enforced at the gate, not just in mandate |
| `test_vendor_gate_user_allowlist_restricts` | Allowlist excludes non-listed merchants | `vendor_allowlist=["nike.com"]`, check adidas | "Only these merchants" pattern works |
| `test_vendor_gate_case_insensitive` | `BAD.COM` blocklist matches `bad.com` request | Mixed-case comparison | Domain matching is case-insensitive (DNS reality) |
| `test_confidence_checker_above_threshold` | 0.9 score → pass, no escalation | Default threshold 0.8 | Confident recommendations proceed normally |
| `test_confidence_checker_below_threshold_escalates` | 0.6 score → fail + escalate=True | Same threshold | Low-confidence picks force human review regardless of amount |

---

## Phase 1 — Gateway + Protocol tests

### [tests/test_capabilities.py](tests/test_capabilities.py) · 4 tests

Wrong intersection = wrong client selection = silent feature breakage.

| Test | What | How | Why |
|---|---|---|---|
| `test_full_match` | All agent capabilities → all in `shared`, none in `agent_only` | Profile with every AGENT_CAPABILITIES entry | Baseline: same on both sides means full functionality |
| `test_partial_match` | Subset overlap → correct shared / agent_only split | Profile with CHECKOUT + ORDER_MANAGEMENT only | Real merchants will support partial sets |
| `test_merchant_only_capabilities_tracked` | Merchant-only namespaces tracked separately, don't break negotiation | Add `dev.ucp.fancy.custom_thing` | Future merchant capabilities won't crash negotiation |
| `test_empty_profile` | No capabilities → shared is empty list (not crash) | Bare profile | Defensive degradation |

---

### [tests/test_discovery.py](tests/test_discovery.py) · 4 tests

The seam where MVP (stub) becomes production (real fetch). Wrong precedence = the progressive-enhancement story breaks.

| Test | What | How | Why |
|---|---|---|---|
| `test_stub_fallback` | Real fetch 404s → stub loaded from JSON | `httpx.MockTransport` returning 404 + temp stub file | Phase 1 must work today before merchants publish profiles |
| `test_real_fetch_wins` | When real `/.well-known/ucp` returns 200, that wins over stub | MockTransport returning a real profile | Day-one progressive enhancement: real merchants auto-upgrade |
| `test_cache_hit_skips_fetch` | Within 60s TTL, second `try_discover` doesn't hit network | Call counter incremented in MockTransport handler | Cache works (60s spec compliance) |
| `test_unknown_domain_returns_none` | No real profile + no stub → None | Domain not in stub file | Clean failure — caller falls back to direct adapter or surfaces error |

---

### [tests/test_ucp_client.py](tests/test_ucp_client.py) · 3 tests

UCPRestClient runs the moment a merchant ships `/.well-known/ucp`. The 3-step checkout must be wire-correct today even though no real merchant exercises it yet.

| Test | What | How | Why |
|---|---|---|---|
| `test_search_products` | POST `/products/search` returns parsed `ProductResult` with `source_protocol="ucp_rest"`; request is signed | MockTransport asserts `Signature` header present, returns one product | Search is the entry point — must be RFC 9421 compliant |
| `test_full_checkout_lifecycle` | Complete 3-step flow: POST create → PUT update (items + buyer) → POST complete (payment_handler_id + token) | MockTransport routes by `method + path`, asserts `Signature` + `Signature-Input` on every request, validates body shape against UCP spec | **The critical Phase 1 test.** Asserts our wire format matches the UCP checkout spec and every request is signed. If a merchant ships UCP tomorrow, this is the contract we honour |
| `test_get_order_status` | GET `/orders/{id}` → parses status/tracking/carrier into `TrackingInfo` | MockTransport returns "shipped" with tracking | Tracking endpoint correctness |

---

### [tests/test_shopify_adapter.py](tests/test_shopify_adapter.py) · 2 tests

The adapter must normalise Shopify's shape into UCP types. Leak in either direction = abstraction breaks.

| Test | What | How | Why |
|---|---|---|---|
| `test_search_returns_ucp_types` | Shopify's `title`/`vendor`/`available` map to UCP `name`/`merchant`/`in_stock`; `source_protocol="shopify_mcp"` | StubShopifyTransport returns Shopify-shaped products | Vocabulary normalisation is the adapter's only job — must work |
| `test_checkout_lifecycle` | Shopify cart → UCP CheckoutSession (with subtotal + tax + total); cart completion → UCP PurchaseOrder | StubShopifyTransport carries cart state across mutations | Both directions of mapping survive the full lifecycle |

---

### [tests/test_merchant_gateway.py](tests/test_merchant_gateway.py) · 5 tests

The gateway is the architectural pivot. These tests are the scalability claim: "swap adapter without touching agents."

| Test | What | How | Why |
|---|---|---|---|
| `test_ucp_route_chosen_when_profile_supports_checkout` | Profile with CHECKOUT capability → returns `UCPRestClient` | Stub file declares profile with CHECKOUT | The UCP-first promise is real |
| `test_direct_adapter_fallback_when_no_ucp` | No UCP profile + registered direct adapter → returns adapter | Register `ShopifyMCPAdapter` for the domain | Today's reality (no merchants on UCP) works |
| `test_profile_with_empty_capabilities_falls_through` | Profile exists but empty capabilities → not UCP-routed | Stub profile with empty `capabilities` array | "Profile exists" ≠ "UCP-supported" — must check capabilities |
| `test_unknown_domain_returns_none` | No profile, no adapter, no factory → None | Domain absent from everything | No silent defaults — caller decides how to surface this |
| `test_client_cache_returns_same_instance` | Two `resolve_client(domain)` calls return the same object | Identity comparison (`is`) | 60s cache works at the gateway level too |

---

### [tests/test_payment_gateway.py](tests/test_payment_gateway.py) · 5 tests

The single trust boundary where `payment_method_id` is resolved. The system's security model hinges on these.

| Test | What | How | Why |
|---|---|---|---|
| `test_authorised_purchase_returns_token` | Valid mandate + within caps → returns `PaymentToken` with `tok_test_*` + `pi_test_*` | Create mandate with `payment_method_id`, request token | Happy path: bridge from mandate authority to opaque token works |
| `test_unauthorised_no_token` | Over-cap request → no token, reason="exceeds_per_transaction_cap" | $100 request against $50 mandate | Tokenisation never happens for unauthorised purchases — Stripe call is skipped |
| `test_no_payment_method_on_mandate` | Mandate with no `payment_method_id` → clean failure | Create mandate without pm_id | Graceful failure mode, no crash |
| `test_record_purchase_writes_spend` | `record_completed_purchase()` writes to `spend_records` table | Inspect DB row after call | The post-purchase loop (which feeds future cap checks) actually closes |
| `test_token_response_does_not_expose_payment_method_id` | `repr(result)` does not contain the raw `payment_method_id` string | Use `pm_secret_value` as id, scan `repr()` | **The load-bearing assertion for payment isolation.** If this test ever fails, the agent layer has been contaminated — fix immediately |

---

## Phase 2 — Tools Layer tests

### [tests/test_shared_tools.py](tests/test_shared_tools.py) · 3 tests

Tools every agent uses. Bugs here cascade everywhere.

| Test | What | How | Why |
|---|---|---|---|
| `test_audit_log_writes_immutable_row` | Audit entry persisted with agent, tool, mandate_id, args, timestamp | Call tool, inspect `db.audit_log` | Audit is Layer 4 of defence in depth — every action must leave a trail |
| `test_get_user_profile_excludes_payment_method` | Returned dict has no `payment_method_id` key | Inspect returned dict | Agent-facing profile view enforces payment isolation at the tool boundary |
| `test_check_spending_limits_returns_auth_result` | Returns AuthResult; passes under cap, fails over | Two calls with $50 and $150 against $100 cap | Pre-flight cap check works for agent reasoning |

### [tests/test_discovery_tools.py](tests/test_discovery_tools.py) · 5 tests

Search/detail fetch with silent vendor gating.

| Test | What | How | Why |
|---|---|---|---|
| `test_search_fans_out_across_merchants` | Returns products from merchant gateway | Stub Shopify adapter returns demo product | Search wraps the gateway correctly |
| `test_search_silently_drops_blocklisted_merchants` | Blocklisted domain → empty results, no reason exposed | Set `vendor_blocklist`, call search | Agent never learns *why* a merchant was filtered (prevents prompt manipulation) |
| `test_search_writes_audit_entry` | Every search produces an audit row | Inspect audit log after call | Discovery is auditable, even when no products returned |
| `test_get_product_details_returns_product` | Fetches a specific product by ID | Stub returns shop_001 | Detail fetch routes through gateway like search |
| `test_get_product_details_returns_none_for_blocked` | Blocklisted merchant → None | Set blocklist, fetch product | Gating enforced on both search AND detail paths |

### [tests/test_evaluation_tools.py](tests/test_evaluation_tools.py) · 10 tests

Weighted ranking + price comparison.

| Test | What | How | Why |
|---|---|---|---|
| `test_weights_sum_to_one` | Scoring weights sum to 1.0 | Sum dict values | Composite score must be in [0,1] — invariant for confidence threshold |
| `test_ranking_prefers_cheaper_when_other_factors_equal` | $50 product ranks above $200 with otherwise identical attributes | Two products, only price differs | Price weight (25%) dominates when nothing else discriminates |
| `test_ranking_flags_out_of_stock` | Out-of-stock product gets `OUT_OF_STOCK` risk flag | Set `in_stock=False` | Risk flags surface in product cards before purchase |
| `test_ranking_flags_low_confidence` | `confidence_score=0.5` → `LOW_CONFIDENCE` flag | Set low confidence | Feeds the escalation rule in Phase 3 orchestrator |
| `test_ranking_respects_user_allowlist` | At equal price, allowlisted merchant ranks above non-allowlisted | Same price, different domain | Trust weight (20%) decides ties — allowlist actually influences ranking |
| `test_empty_input_returns_empty` | `[]` in → `[]` out, no crash | Empty list | Defensive — agents will occasionally pass no products |
| `test_fetch_reviews_summary` | Returns rating + count for known product | Stub Shopify provides 4.5/240 | Reviews summary surfaces in product cards |
| `test_check_vendor_allowlist_passes` | Allowed domain → True | Default user, demo merchant | Tool-level vendor check works |
| `test_check_vendor_allowlist_blocks` | Blocklisted domain → False | Set blocklist | Tool-level vendor check honours blocklist |
| `test_compare_prices_sorts_by_price` | Returned per-merchant lists are sorted ascending by price | Inspect order of price strings | Price comparison output is agent-friendly (cheapest first) |

### [tests/test_purchase_tools.py](tests/test_purchase_tools.py) · 5 tests

The full checkout chain wrapped in tool functions. Most critical Phase 2 surface.

| Test | What | How | Why |
|---|---|---|---|
| `test_validate_mandate_passes` | Pre-flight check returns authorized=True for valid request | $100 against $500 mandate | Mandate validation tool exposes AP2 cleanly to agents |
| `test_get_payment_token_returns_dict_without_payment_method_id` | Returned dict has `token`, `payment_intent_id` — never `payment_method_id` | Use `pm_test_secret` as id, `repr(result)` scan + key check | **The load-bearing Phase 2 isolation test.** Even at the tool boundary, the agent never sees the raw payment method ID |
| `test_get_payment_token_unauthorised_returns_reason` | Over-cap request → `authorized=False`, no `token` key | $100 against $50 mandate | Tool surfaces the reason for agent reasoning, never returns a token to spend with |
| `test_full_purchase_chain` | End-to-end: create → update → token → complete → save → record. Asserts session state, mandate stamp on order, all DB writes, full audit trail | Run all six tools sequentially against stub Shopify | Phase 2 integration proof: the chain composes correctly without an agent driving it |
| `test_unknown_merchant_returns_none` | `create_checkout_session` for unknown domain → None | Use unregistered domain | Graceful failure when merchant unsupported (no crash, no silent default) |

### [tests/test_tracking_tools.py](tests/test_tracking_tools.py) · 6 tests

Post-purchase: status polling, returns, refund lookup.

| Test | What | How | Why |
|---|---|---|---|
| `test_get_order_status_polls_merchant` | Returns TrackingInfo with status | Stub returns "pending" for unknown order | Tracking polling works against the gateway |
| `test_initiate_return_requires_known_order` | Known order → `accepted=True, status="submitted"` | Seed an order, call tool | Returns are gated on local order knowledge (prevents agent fabricating order IDs) |
| `test_initiate_return_rejects_unknown_order` | Unknown order → `accepted=False, reason="order_not_found"` | Skip the seed step | Clean failure mode |
| `test_check_refund_status_finds_by_intent` | Refunded order lookup by `payment_intent_id` returns `status="refunded"` | Seed order with REFUNDED status | Refund lookup works (will route to Stripe in production) |
| `test_check_refund_status_unknown` | Unknown intent → `status="unknown"` | No seeded order | No silent default to a wrong order |
| `test_tracking_actions_are_audited` | `initiate_return` writes an audit entry | Inspect audit log | Tracking actions are auditable like every other action |

---

## End-to-end (cross-phase) tests

### [tests/test_end_to_end.py](tests/test_end_to_end.py) · 6 tests

The whole-system proof: full user journey (discovery → evaluation → validate → purchase → tracking) run **twice** — once through `ShopifyMCPAdapter` (today's MVP path), once through `UCPRestClient` against a `UCPMockMerchant` (target path). Same tools, same assertions, different routing underneath.

| Test | What | How | Why |
|---|---|---|---|
| `test_shopify_journey_end_to_end` | Complete journey via Shopify adapter; audit covers all 6 stages; spend record written | `_run_journey()` helper drives all tools; inspect audit + spend tables | Proves Phase 0–2 compose end-to-end on today's MVP path |
| `test_ucp_journey_end_to_end` | Identical journey via `UCPRestClient` against a `UCPMockMerchant` MockTransport that implements the full UCP REST API (search, 3-step checkout, order status) | Mock transport replaces httpx network; gateway routes to UCP because stub profile declares CHECKOUT capability | **The scalability claim made testable.** Same `_run_journey()` runs unchanged when the gateway switches paths — exactly what Phase 3 agents will inherit |
| `test_daily_cap_exhaustion_blocks_second_purchase` | First purchase consumes ~70% of daily cap; second purchase rejected with `exceeds_daily_cap`. Both `validate_mandate` and `get_payment_token` refuse | Set daily cap = $200, run journey, attempt second purchase | The whole point of mandate caps. Cross-phase: AP2 record (Phase 0) → SpendingLimiter check (Phase 0) → PaymentGateway refusal (Phase 1) → purchase tool returns `authorized=False` (Phase 2) |
| `test_vendor_blocklist_propagates_through_full_journey` | Adding a merchant to blocklist mid-session → discovery returns `[]` silently | Set `vendor_blocklist`, call `search_products` | Vendor gating works at the discovery boundary, not just at purchase. Prevents agent from even seeing blocked merchants |
| `test_routing_decision_visible_via_source_protocol` | Shopify-routed search → `source_protocol="shopify_mcp"` | Build Shopify-only ctx, search | The routing decision is observable in the data — useful for telemetry / debugging in production |
| `test_routing_decision_ucp_path` | UCP-routed search → `source_protocol="ucp_rest"` | Build UCP-only ctx, search | Counterpart to above — confirms the gateway actually swapped paths when given a UCP-capable profile |

**The `UCPMockMerchant` helper class** lives in this file. It implements the same UCP REST API a real merchant would: `POST /products/search`, `POST /checkout-sessions`, `PUT /checkout-sessions/{id}`, `POST /checkout-sessions/{id}/complete`, `GET /orders/{id}`. State persists across requests within one test so the full lifecycle composes realistically.

---

## Phase 3 — Agent Layer tests

All Phase 3 tests use a `FakeAnthropicClient` ([tests/fake_anthropic.py](tests/fake_anthropic.py)) with pre-scripted Claude responses. Deterministic, no API key, no network. The fake records every call so tests assert on tool dispatch, advertised tool schemas, message contents, and prompt caching.

### [tests/test_base_agent.py](tests/test_base_agent.py) · 10 tests

The tool-loop mechanics every subagent inherits. Bugs here cascade to every agent.

| Test | What | How | Why |
|---|---|---|---|
| `test_tool_loop_terminates_on_end_turn` | When Claude stops calling tools, `run()` returns parsed result | Single `end_turn` response | The base case — without this nothing else works |
| `test_tool_loop_dispatches_then_continues` | After a tool call, the result is sent back to Claude, loop continues | `tool_use` → `end_turn` sequence; inspect tool_result block | The Anthropic SDK tool-loop contract |
| `test_multiple_tool_calls_in_one_turn` | Claude can request multiple tools in one assistant turn; all dispatched | Two ToolUseBlocks in one response | Real models batch when possible — we must handle it |
| `test_unknown_tool_returns_error_to_model` | Bad tool name → error dict sent back to model, loop continues | Script unknown tool call + recovery | Graceful degradation; never crash on model hallucination |
| `test_tool_exception_does_not_crash_loop` | Tool raises `ValueError` → caught, error returned to model | Tool body raises | Tool errors must surface to the model so it can react, not propagate up |
| `test_parses_fenced_json_code_block` | Model wraps JSON in ```` ```json … ``` ```` fences → still parses | Final response in fenced block | Models often add fences; the parser handles them |
| `test_returns_parse_error_for_non_json` | Pure-text final response → `{"parse_error": "non_json", "raw": text}` | Non-JSON final | Subagent contract is JSON; bad output must be surfaced, not silently accepted |
| `test_iteration_cap_prevents_runaway` | If Claude loops forever, abort after `MAX_ITERATIONS` (16) | Queue 20 tool_use responses | Safety net against pathological loops |
| `test_make_tool_spec_infers_schema_from_signature` | `inspect.signature` → JSON Schema for tool input (str/int/list/optional) | Define an `_example` function, inspect generated schema | Tool schemas must stay in sync with function signatures — generated, not hand-written |
| `test_pydantic_results_are_serialised_for_tool_results` | A Pydantic-returning tool produces JSON-safe `tool_result.content` | Tool returns `ProductResult`; inspect tool_result string | Decimals + datetimes must serialise cleanly for the model |

### [tests/test_discovery_agent.py](tests/test_discovery_agent.py) · 3 tests

| Test | What | How | Why |
|---|---|---|---|
| `test_emits_search_call_with_expected_args` | DiscoveryAgent dispatches `search_products` with correct query + domains | Script tool_use, inspect dispatched args | The agent wires its prompt to the right tool |
| `test_search_passes_through_to_real_tool` | Tool dispatch actually invokes the Phase 2 function (audit row written) | Run agent, check `db.audit_log` for `search_products` | Proves the agent isn't just talking to Claude — it's running real tools |
| `test_tool_schema_advertises_search_to_the_model` | The first `messages.create` call advertises all three Discovery tools | Inspect `client.calls[0].tools` | The model only knows about tools we advertise — schema generation must be complete |

### [tests/test_evaluation_agent.py](tests/test_evaluation_agent.py) · 2 tests

| Test | What | How | Why |
|---|---|---|---|
| `test_invokes_rank_products_and_returns_ranked_list` | Agent calls `rank_products`, parses RankedProduct[] from final JSON | Script tool_use + final JSON | Ranking is the agent's primary job |
| `test_advertises_full_tool_set` | All three evaluation tools advertised | Inspect tools list | Schema completeness |

### [tests/test_purchase_agent.py](tests/test_purchase_agent.py) · 3 tests

| Test | What | How | Why |
|---|---|---|---|
| `test_full_purchase_chain_via_agent` | Agent dispatches the full 7-step chain starting with `validate_mandate` | Script all 7 tool_use responses + final JSON; assert order of dispatched names | The agent enforces the lifecycle order — mandate validation must come first |
| `test_purchase_agent_never_sees_payment_method_id` | Across every message turn, the raw `pm_test_secret` string never appears in agent inputs or tool_result blobs | Use `pm_test_secret` as id, serialise every recorded `client.calls[*].messages`, substring-check | **The load-bearing Phase 3 isolation test.** Proves payment isolation survives the agent layer — Claude literally cannot see the credential |
| `test_advertises_all_purchase_tools` | All 8 purchase tools advertised including `audit_log` | Inspect tools list | Schema completeness |

### [tests/test_tracking_agent.py](tests/test_tracking_agent.py) · 2 tests

| Test | What | How | Why |
|---|---|---|---|
| `test_polls_order_status` | Agent dispatches `get_order_status` and parses TrackingInfo | Script tool_use + final | Primary tracking flow works |
| `test_advertises_tracking_tools` | All four tracking tools advertised | Inspect tools list | Schema completeness |

### [tests/test_orchestrator.py](tests/test_orchestrator.py) · 9 tests

| Test | What | How | Why |
|---|---|---|---|
| `test_classify_tier_soft_for_small_known_merchant` | $25 → "soft" tier | Pure-function call to `classify_gate` | Threshold table in ARCHITECTURE.md must match code |
| `test_classify_tier_explicit_for_moderate_amount` | $150 → "explicit" tier | Pure function | Same |
| `test_classify_tier_explicit_with_summary_for_large` | $600 → "explicit_with_summary" | Pure function | Same |
| `test_classify_tier_upgrades_when_confidence_low` | Confidence <0.8 escalates regardless of amount | Pure function with low confidence | The confidence rule from architecture is non-negotiable |
| `test_classify_tier_upgrades_for_new_merchant` | First purchase from merchant escalates from soft to explicit | Pure function | New-merchant rule |
| `test_purchase_gate_approve_runs_subagent` | Approved gate → PurchaseAgent dispatched, gate recorded as "explicit" | AutoConfirmProvider(explicit=True), inspect `gates_seen` | The orchestrator dispatches correctly after approval |
| `test_purchase_gate_deny_returns_cancelled_without_running_subagent` | Denied gate → `cancelled_by_user` returned to model, subagent never called, audit row written | AutoConfirmProvider(explicit=False); assert fake client has exactly N calls (subagent never consumed responses) | **The safety claim**: a denied gate guarantees no payment is attempted |
| `test_no_mandate_id_blocks_purchase` | If orchestrator has no `mandate_id`, gate never fires and purchase fails fast | Construct orchestrator with `mandate_id=None` | Defensive — purchases require an active mandate |
| `test_streaming_callbacks_fire_for_subagent_calls` | `on_tool_start`, `on_tool_end`, `on_gate` callbacks fire in order | Inject async callbacks, run, assert collected lists | CLI/UI integration surface works (Phase 4 will rely on these) |

### [tests/test_orchestrator_e2e.py](tests/test_orchestrator_e2e.py) · 3 tests

The big agent-level e2e tests. A single user message produces the full agent chain.

| Test | What | How | Why |
|---|---|---|---|
| `test_orchestrated_journey_discovery_eval_confirm_purchase` | User message → Orchestrator → Discovery (mocked Claude turn) → Evaluation → HITL gate (auto-approve) → Purchase (mocked Claude turn) → final reply. All 7 scripted responses consumed exactly. Audit log captures `hitl_gate` row | 7 carefully-ordered scripted responses; assert `client.remaining() == 0`, gate fired once at `explicit` tier, audit contains gate entry | **The agentic-system proof.** Phase 0→2 is mechanically correct; this test proves the agent layer correctly orchestrates that chain end-to-end |
| `test_user_cancellation_stops_purchase_subagent` | Denied gate → subagent never consumes any scripted response → no spend record written | Queue NO PurchaseAgent responses; if it ran, fake client would error | Cancellation is total — no side effects past the gate |
| `test_no_payment_method_id_in_orchestrator_message_history` | Across the entire orchestrated journey, raw `pm_secret_DO_NOT_LEAK` never appears in any message sent to Claude | Use a sentinel pm_id, scan `repr` of every recorded message + system prompt | **End-to-end payment-isolation invariant.** Even with the full agent layer in play, the credential never crosses into the model's context |

### Mock pattern: [tests/fake_anthropic.py](tests/fake_anthropic.py)

Not a test itself. A scripted-response fake that:
- Implements `client.messages.create(*, model, system, tools, messages, max_tokens)` as a coroutine that pops the next pre-scripted response from a queue
- Provides `text_response()`, `tool_use_response()`, `text_then_tool_use()` helpers
- Records every call so tests can assert on advertised tools, message history, dispatched tool names + inputs
- Exposes `dispatched_tool_names()` and `tool_inputs(name)` helpers for common assertions

This is the deterministic substrate that makes 27 stochastic-feeling agent tests run in <100ms with zero flakiness.

---

## Phase 4 + multi-merchant tests

### [tests/test_multi_merchant.py](tests/test_multi_merchant.py) · 6 tests

Uses the production catalogue (`config/catalogue.py`) via the `multi_merchant_ctx` fixture — same data the user sees live.

| Test | What | How | Why |
|---|---|---|---|
| `test_multi_merchant_discovery_returns_results_from_correct_merchants` | Search "headphones" → Audio Hub returns results, others correctly drop | 3-merchant fan-out via gateway | Routing across merchants works |
| `test_cross_merchant_overlap_when_same_product_class_appears_at_multiple` | Search "earbuds" → both Athletic Co AND Audio Hub return results | Both catalogues seeded with earbud-class items | Proves cross-merchant comparison is real, not theoretical |
| `test_compare_prices_groups_by_merchant_sorted` | `compare_prices` returns dict grouped by merchant_domain, each list price-ascending | Decimal comparison after string parse | Power-user "where's it cheapest" flow works |
| `test_ranking_across_merchants_picks_best_overall` | Candidates from 3 merchants ranked together with contiguous ranks | Search "coffee" then `rank_products` | Cross-merchant ranking doesn't break |
| `test_oos_item_propagates_risk_flag` | Athletic Co's Trail Runner Pro (deliberately OOS) gets `OUT_OF_STOCK` flag | Catalogue seed sets `available=False` | Risk flags actually flow through |
| `test_catalogue_satisfies_gate_tier_diversity_checklist` | Catalogue has items in ≤$30, $100-500, >$500 ranges | Aggregate prices across 5 queries | Live demo can exercise all gate tiers |

### [tests/test_purchase_tools.py](tests/test_purchase_tools.py) · 2 new tests (7 total)

| Test | What | How | Why |
|---|---|---|---|
| `test_update_session_injects_default_buyer_when_omitted` | Tool fills BuyerInfo from `user.default_shipping()` when agent omits `buyer` | Set address, call without buyer; audit row `has_buyer=True` | Closes the "no address" bug from live testing |
| `test_update_session_no_buyer_when_no_address` | Empty addresses + no buyer arg → audit shows `has_buyer=False` (clean fallback, no crash) | Empty addresses list | Defence in depth — the tool handles missing data gracefully |

### [tests/test_cli_display.py](tests/test_cli_display.py) · 25 tests

Smoke coverage for every Rich renderer plus `RichConfirmProvider`. None assert exact ANSI output (brittle); all assert "did not raise". The CONFIRM case-insensitivity test is the one that prevents typo regressions.

| Test cluster | What it proves |
|---|---|
| `test_display_welcome_runs`, `_mandate_*`, `_profile_*` | Welcome/mandate/profile panels render with full and minimal data |
| `test_display_products_*` | Product cards render for empty list, in-stock, and risk-flagged items |
| `test_display_checkout_summary_*`, `_order_*`, `_tracking_*`, `_orders_*` | Checkout/order/tracking displays survive all field combinations |
| `_fmt_dt_*`, `_short_dt_*`, `_pct_*` | Helper functions handle None, strings, zero caps |
| `test_explicit_confirm_accepts_case_insensitive_confirm` | `confirm`, `Confirm`, `CONFIRM`, `  CONFIRM  ` all accepted; `cnfirm`, `yes`, `no`, empty all cancel — covers the typo regression we already saw |
| `test_soft_confirm_default_proceeds`, `_no_cancels` | Soft gate: Enter to confirm; `no`/`N`/`cancel`/`stop` to cancel |

### [tests/test_main_commands.py](tests/test_main_commands.py) · 15 tests

`_handle_command` REPL dispatch, with a `_FakeOrchestrator` so we test command routing without spinning up the orchestrator stack.

| Test cluster | What it proves |
|---|---|
| `exit`, `quit`, empty line | Lifecycle works |
| `orders`, `mandate`, `revoke mandate` | Persistent-state commands work and mutate DB correctly |
| `profile` | New Phase 4 command runs |
| `block <merchant>`, idempotency | Vendor blocklist add works; duplicate adds don't duplicate |
| `audit` | Audit display works on empty and populated logs |
| `track <id>` | Known + unknown order paths both safe |
| Free text | Routes to orchestrator (the only thing that hits Claude) |

## Phase 5 — Safety hardening tests

### [tests/test_spend_limit_override_resistance.py](tests/test_spend_limit_override_resistance.py) · 9 tests

**The most important test class in the suite.** Each test scripts a "bad agent" path — agent skipping validation, ignoring authorisation, escalating amounts, attempting purchases against revoked or tampered mandates — and asserts the dangerous side effect (unauthorised payment, over-cap spend, ignored revocation) does NOT happen.

If any of these break, the trust model is broken.

| Test | Bad behaviour scripted | What must hold |
|---|---|---|
| `test_agent_skipping_validate_mandate_still_blocked` | Agent skips `validate_mandate`, jumps straight to `get_payment_token` for over-cap amount | PaymentGateway re-runs AP2 internally → `authorized=false`, no spend record |
| `test_agent_ignoring_authorized_false_cannot_complete` | Agent receives `authorized=false`, then tries `complete_order` with forged token | No forged-token order ever persists; no spend recorded |
| `test_agent_cannot_exceed_per_tx_cap` | Agent requests $999 against $100 cap | `validate_mandate` returns `authorized=false, reason=exceeds_per_transaction_cap` |
| `test_agent_cannot_exceed_daily_cap_via_repeated_calls` | Existing $400 spend + new $200 request, $500 daily cap | Second request rejected at `validate_mandate` |
| `test_agent_cannot_purchase_after_revoke` | Mandate revoked mid-session, agent tries purchase | PaymentGateway refuses with `mandate_revoked`; no token issued |
| `test_agent_cannot_route_to_blocklisted_merchant` | User blocklists merchant; agent searches | Empty results, silently — agent can't even see products to attempt purchase |
| `test_agent_cannot_override_max_amount_in_get_payment_token` | Agent validates with $50 (safe), then requests token for $999 | PaymentGateway re-validates with actual amount → refused |
| `test_revoked_mandate_payment_gateway_refuses` | First purchase succeeds, then revoke, then second purchase | Second fails — every call re-verifies, no stale auth |
| `test_tampered_mandate_signature_blocks_payment` | Direct DB edit bumps `max_amount` without re-signing | `verify_signature` fails → PaymentGateway refuses with `invalid_signature` |

These collectively prove: **mandate enforcement is not a function of agent good behaviour. It's enforced at every layer.** A jailbroken or hallucinating agent cannot route around the safety controls.

---

## Phase 7 — Web UI tests

The web layer is a thin FastAPI/Jinja/HTMX adapter over the existing
agent stack. Tests use FastAPI's `TestClient` (sync wrapper around the
ASGI app) and exercise both HTML and JSON responses via the
`Accept: application/json` seam.

### [tests/test_web_phase7a.py](tests/test_web_phase7a.py) · 21 tests

Foundation: app boot, home / search / product detail routes, dual-format
seam (HTML vs JSON via Accept header), signed-cookie sessions, healthz.

### [tests/test_web_phase7b.py](tests/test_web_phase7b.py) · 10 tests

Chat router + SSE streaming + chat sidebar. POST `/chat` validation, the
offline-friendly path when `ANTHROPIC_API_KEY` is missing, user-message
echo onto the session SSE queue, SSE route registration, two-client
session isolation, sidebar rendering on every page, callback adapter
that pushes orchestrator callbacks onto the queue.

### [tests/test_web_phase7c.py](tests/test_web_phase7c.py) · 14 tests

Click-to-cart: add (with idempotent quantity bump), remove (silent
no-op on missing item), change quantity (0 removes), clear, view as
HTML drawer or JSON summary. Synthesised `[via UI click] …` notes
appended to `session.conversation` so the agent sees clicks on the
next turn. Per-session cart isolation.

### [tests/test_web_phase7d.py](tests/test_web_phase7d.py) · 9 tests

`WebsocketConfirmProvider` + `/gate/ws` + gate modal. Provider
duck-types `ConfirmationProvider`; confirm / cancel / question
trichotomy round-trips through outbox + inbox queues; WS bridge
delivers `gate.open` to the browser and routes browser replies onto
the provider's inbox; disconnect pushes a synthetic `cancel` so the
orchestrator never deadlocks; modal markup is present in `base.html`.

### [tests/test_web_phase7e.py](tests/test_web_phase7e.py) · 14 tests

Profile (verifies `payment_method_id` redaction), mandate (caps + live
spend, JSON shape, revoke flips status and redirects), orders (empty
state, 404 on unknown id, seeded list + detail), return-via-button
calls `initiate_return` tool, audit log page renders entries. Header
navigation links present.

### [tests/test_web_phase7f.py](tests/test_web_phase7f.py) · 5 tests

Picker overlay (numbered-list shell for the search sub-flow) present in
`base.html`, toast stack with `window.__toast` helper, chat sidebar
routes click + error SSE events to toasts, `docs/WEB_DEVELOPMENT.md`
exists with all the sections enumerated in the plan, gate modal and
picker persist across every page extending `base.html`.

The web layer adds 73 tests; full suite is 498/498.

---

## What we are NOT testing (intentionally)

| Not tested | Why deferred | Phase that adds it |
|---|---|---|
| Real Stripe API calls | Requires `STRIPE_TEST_KEY`; live network | Phase 5 (conformance) |
| Real Shopify MCP subprocess | Requires Anthropic SDK + Node MCP server | Phase 3 (agent layer) |
| Real UCP reference server | Requires cloning + running FastAPI server | Phase 5 (conformance) |
| Agent reasoning behaviour | Stochastic; needs golden traces / eval framework | Phase 3 (agent layer) |
| HITL gate UX | Rich CLI is human-facing | Phase 4 (CLI polish) |
| Webhook signature verification | Webhooks deferred per architecture decision | Phase 2+ activation |
| Multi-merchant fan-out search beyond unit level | Requires concurrent live merchants | Phase 5 |

These gaps are deliberate, documented in `PLAN.md` tier breakdown.

---

## How to extend this tracker

When you add a test:

1. Add a row to the relevant section's table.
2. Fill in all three columns honestly — **What** is observable, **How** is mechanism, **Why** is the architectural claim. If you can't write the Why, the test is probably not worth keeping.
3. Update the count in the header and pyramid diagram.
4. If you add a new test file, add a new ### subsection with the same column structure.

When you remove a test:

1. Delete its row.
2. Note the removal reason if non-obvious (e.g. "obsoleted by Phase 3 agent-level test").

When the Phase boundary shifts:

- Move section ordering to match the new phase structure.
- Update the test-pyramid diagram.

---

## Running subsets

```bash
# Just Phase 0 foundation
python3 -m pytest tests/test_models.py tests/test_mandate.py tests/test_signing.py tests/test_guardrails.py -v

# Just Phase 1 protocol + gateway
python3 -m pytest tests/test_capabilities.py tests/test_discovery.py tests/test_ucp_client.py tests/test_shopify_adapter.py tests/test_merchant_gateway.py tests/test_payment_gateway.py -v

# Just Phase 2 tools
python3 -m pytest tests/test_shared_tools.py tests/test_discovery_tools.py tests/test_evaluation_tools.py tests/test_purchase_tools.py tests/test_tracking_tools.py -v

# Just end-to-end cross-phase journeys
python3 -m pytest tests/test_end_to_end.py -v

# Just Phase 3 agent layer
python3 -m pytest tests/test_base_agent.py tests/test_discovery_agent.py tests/test_evaluation_agent.py tests/test_purchase_agent.py tests/test_tracking_agent.py tests/test_orchestrator.py tests/test_orchestrator_e2e.py -v

# Just Phase 4 (CLI + REPL + multi-merchant)
python3 -m pytest tests/test_cli_display.py tests/test_main_commands.py tests/test_multi_merchant.py -v

# Just the safety-critical tests (run before every release)
python3 -m pytest tests/test_spend_limit_override_resistance.py tests/test_payment_gateway.py tests/test_mandate.py -v

# Just the load-bearing isolation tests
python3 -m pytest tests/test_payment_gateway.py::test_token_response_does_not_expose_payment_method_id tests/test_models.py::test_user_profile_strips_payment_method tests/test_purchase_tools.py::test_get_payment_token_returns_dict_without_payment_method_id tests/test_shared_tools.py::test_get_user_profile_excludes_payment_method -v

# Just the UCP wire-format tests
python3 -m pytest tests/test_signing.py tests/test_ucp_client.py -v
```
