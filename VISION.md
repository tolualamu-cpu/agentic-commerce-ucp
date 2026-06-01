# Agentic Commerce — Vision & Product Goals

## The Ultimate Goal

Build an autonomous purchasing agent that acts as a personal commerce concierge — understanding user intent in natural language, discovering products across any merchant, making informed recommendations, and executing purchases safely on the user's behalf. The end-to-end shopping experience goes from hours of manual browsing to a natural conversation.

## The Problem

Online shopping is fragmented. Users visit multiple sites, compare prices manually, manage separate accounts, and repeat checkout flows for every merchant. As AI agents become capable of autonomous action, there is an opportunity to delegate the entire purchasing lifecycle to an agent that acts with the user's interests, preferences, and budget in mind — while keeping the human in control of consequential decisions.

## Product Vision

A conversational agent (or team of agents) that:

1. **Understands intent** — user says what they want in natural language; the agent parses preferences, constraints, and urgency
2. **Discovers options** — searches across any merchant using universal commerce protocols where available, direct integrations otherwise
3. **Evaluates and ranks** — scores results against user preferences, budget, merchant trust, shipping speed, and reviews
4. **Confirms with the user** — presents ranked options and gets explicit approval before spending any money
5. **Executes safely** — completes checkout and payment without exposing card details to the agent layer
6. **Tracks to delivery** — monitors order status and handles post-purchase actions (returns, refunds) proactively

## Protocol Strategy

The system is built protocol-first. **UCP (Universal Commerce Protocol)** is the long-term standard we build toward — it defines the vocabulary every internal component speaks. But UCP merchant adoption is still growing, so:

- **MVP** uses direct integrations (Shopify MCP, Stripe) behind UCP-vocabulary interfaces
- **Progressive enhancement**: as merchants publish `/.well-known/ucp` profiles, the system automatically routes through the UCP client — zero agent code changes required
- **The abstraction layer is the investment** — every merchant we add, whether UCP-native or via a custom adapter, slots into the same interface

## MVP Scope (Phase 1)

| Dimension | MVP |
|---|---|
| Interface | CLI (Rich terminal) |
| Merchants | Shopify (via MCP) |
| Payments | Stripe test mode |
| Purchase flow | Single-item discovery → comparison → HITL confirm → purchase |
| Spending control | Local AP2-style mandates (HMAC-signed, no merchant-side verification needed) |
| Order tracking | Polling (no webhooks) |
| Deployment | Local only |

## Target State (Phase 2+)

| Dimension | Target |
|---|---|
| Interface | Web/mobile UI + REST API |
| Merchants | Any UCP/ACP-compliant merchant; custom adapters for others |
| Payments | AP2 mandate-native, crypto via x402, multi-currency |
| Purchase flow | Multi-item basket, cross-merchant optimization, autonomous replenishment |
| Spending control | Merchant-side AP2 extension, revocable mandates, real-time spend tracking |
| Order tracking | Webhook-driven, proactive notifications |
| Deployment | Cloud-hosted, multi-user, persistent profiles |

## What Success Looks Like

**Phase 1:** User types "find me running shoes under $150 size 10" → agent searches, ranks, shows options, confirms → executes purchase → shows order confirmation. Full loop in under 60 seconds, zero manual browser navigation.

**Phase 2+:** User says "keep my home office stocked — reorder when anything runs low" → agent monitors, recommends restocks, executes within pre-approved limits, notifies when done.

## Non-Goals (for now)

- Price negotiation with merchants (interesting, out of scope)
- Selling on behalf of the user (commerce agent, not merchant agent)
- Financial advice or investment products
- Physical-world integrations (stores, delivery coordination)
