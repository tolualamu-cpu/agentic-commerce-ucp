# Agentic Commerce — Claude Code Instructions

## Project overview
A FastAPI + Jinja2 + HTMX agentic shopping assistant. Three demo merchants
(Athletic Co, Audio Hub, Coffee Bar) served via stub Shopify adapters, plus
live merchants fetched at runtime (currently: Kith via LiveShopifyTransport).
See ARCHITECTURE.md for the full system design.

## Running the server
```bash
uvicorn web.app:app --reload
```

## Running tests
```bash
python3 -m pytest tests/ -x -q
```

## Testing requirements (system rule — never waive)

1. **All existing tests must pass before any new feature ships.** No regressions.
   Baseline: `python3 -m pytest tests/ -x -q` exits 0.

2. **Every new feature must have thorough tests at two levels:**
   - Unit tests covering individual functions, edge cases, and error paths.
   - E2E / user journey tests covering realistic shopper flows end-to-end.

3. **Test coverage must span every merchant on the platform — demo and live.**
   Never test a product feature against a single merchant or category in isolation.
   The authoritative merchant lists are `MERCHANTS` and `LIVE_MERCHANTS` in
   `config/catalogue.py`. Tests must iterate these dicts rather than hardcoding
   merchant names so that any new entry is automatically covered without test
   changes. Every product-facing test suite must cover:
   - **All demo merchants** (currently Athletic Co, Audio Hub, Coffee Bar) —
     iterate `config.catalogue.MERCHANTS`.
   - **All live merchants** (currently Kith) — iterate
     `config.catalogue.LIVE_MERCHANTS`. Live merchant tests must mock HTTP
     (no real network calls); use the fixture pattern in
     `tests/test_kith_merchant_journeys.py` as the template.
   - **Cross-merchant comparisons** and single-merchant filters across both
     demo and live merchant populations.
   - Price ranges: under $30 (soft gate), $100–$500 (explicit gate), >$500
   - Out-of-stock handling (Trail Runner Pro, ath_002)
   - Cart operations from all entry points (Explore click, chat card, typed text)
   - Multi-item and cross-merchant baskets (demo + live merchants mixed)
   - Cancel/retry flows and agent cache re-use
   - **Gateway registration check**: every domain in `MERCHANTS` and
     `LIVE_MERCHANTS` must appear in `MerchantGateway.direct_adapters` at
     session start. Write this as a parametrised test so it covers all current
     and future merchants automatically.

4. **New test files must not contaminate the event loop** for pre-existing tests.
   The codebase runs on Python 3.9, where `asyncio.run()` closes the loop and
   sets `_set_called=True`, preventing subsequent `asyncio.get_event_loop()`
   from creating a new loop. Rule: in test files that sort alphabetically
   **before** `test_user_journeys.py`, use
   `asyncio.get_event_loop().run_until_complete()` instead of `asyncio.run()`.
   Files that sort after `test_user_journeys` may use `asyncio.run()` freely;
   when they create `asyncio.Queue()` at the synchronous level they must do so
   inside an `async def` run via `asyncio.run()`.

5. **Approval gate**: a feature is considered approved only when
   `pytest tests/ -x -q` exits 0 (all tests pass, count increases with each
   new feature).

6. **Standing regression gate — these MUST pass before any approval.**
   This list grows whenever a user-reported regression has been fixed; the
   bug is locked down by a test and added here. Removing any test from this
   list is a hard veto unless the underlying behaviour has changed by design.

   **Unit / integration tests (run with `pytest tests/ -x -q`):**
   - `tests/test_chat_products_fragment.py` — chat product card fragment
     endpoint correctness (incl. Kith URL path + Buy-on badge).
   - `tests/test_chat_product_card_no_inline_js.py` — no inline JS in any
     template that gets `innerHTML`-injected (CSS class hover only).
   - `tests/test_chat_dom_uniqueness.py` — only ONE `#chat-log` per render.
   - `tests/test_merchant_brand_separation.py` — UCP rule: `merchant` is the
     storefront, `brand` is the manufacturer. `Buy on {merchant}` MUST read
     the storefront (Kith), never the brand (Stone Island/Jordan).
   - `tests/test_enrich_url_brand_backfill.py` — `_enrich_products_with_images`
     backfills `url` and `brand` when the discovery agent drops them; the
     DISCOVERY prompt requires both fields.
   - `tests/test_discovery_partial_json_recovery.py` — discovery JSON can be
     recovered when truncated by max_tokens; max_tokens is at least 8192.
   - `tests/test_chat_typing_indicator.py` — typing indicator lifecycle.
   - `tests/test_chat_user_bubble_and_cart_badge.py` — optimistic user
     bubble + pinned-bubble autoscroll path.
   - `tests/test_tone_post_cancellation.py` — orchestrator forbids prose
     product listings and post-cancellation chatter.
   - `tests/test_kith_merchant_journeys.py` — full journey suite for Kith
     (live merchant); covers product data integrity, discovery, cart, purchase
     flow divergence, visual components, chat cards, gateway registration, and
     (per `TestKithVariants` / `TestKithFamilyGroupingBreadth` /
     `TestKithVariantCartOperations`) the 21-product variant/family fixture
     set in `tests/fixtures/kith_products.py`.
     **This file is the required template for every new live merchant.**
     When a new merchant is added to `LIVE_MERCHANTS`, a corresponding
     `tests/test_{merchant}_merchant_journeys.py` must exist and pass before
     the merchant ships, including an equivalent `TestXxxVariants`-style class
     backed by an equivalent `tests/fixtures/{merchant}_products.py` fixture
     set (>=20 products, spanning no-variant / single-dimension /
     multi-dimension variants, multi-member families across more than one
     splitting dimension, out-of-stock, and price-spread products). The
     `TestMerchantGatewayIntegration` class in this file includes parametrised
     tests that automatically cover all merchants in `LIVE_MERCHANTS` — do not
     break that parametrisation.

   **Phase 1 — Variants / SKUs / product families (added 2026-06-10):**
   - `tests/test_product_variants_model.py` — `ProductVariant` schema
     validation, defaults, serialization round-trip.
   - `tests/test_catalogue_variants.py` — every `MERCHANTS` product's
     `variants`/`options` seed data is well-formed (parametrised over
     `MERCHANTS`, every product, not a sample).
   - `tests/test_cart_variant_lines.py` — `(product_id, variant_id)` composite
     cart-line identity: two variants of the same product → two lines, same
     variant_id twice → quantity bump, missing `variant_id` on a variant
     product → 400, no-variant products unaffected.
   - `tests/test_add_to_cart_tool_variants.py` — `_add_to_cart` is
     variant-aware and self-fetches the product: `variant_required`,
     `invalid_variant`, `product_not_found`, price-override resolution, and
     no-variant regression.
   - `tests/test_get_product_variants_tool.py` — `get_product_variants` tool
     shape, parametrised over `MERCHANTS` + `LIVE_MERCHANTS`.
   - `tests/test_product_grouping.py` / `test_product_grouping_integration.py`
     — `group_into_families`: family-of-1 pass-through (default), multi-member
     grouping by Color/Material/Capacity (and other) dimensions, synthesized
     `variants`/`option_names` correctness, and the negative case (titles that
     share a variant-vocabulary word but are different products are NOT
     merged). Generalizes the **"one card per product family"** standing rule
     beyond color.
   - `tests/test_variant_picker_templates.py` — `_variant_picker_modal.html`
     / `_variant_picker_controls.html` rendering: per-dimension controls
     (Color → chips, others → `<select>`), disabled/enabled "Add to cart"
     state, out-of-stock messaging.
   - `tests/test_chat_variant_flow.py` — mocked-Anthropic orchestrator flow:
     `add_to_cart` → `variant_required` → model retries with the resolved
     `variant_id` → cart line carries `selected_options`. Parametrised across
     one variant product per merchant in `MERCHANTS`/`LIVE_MERCHANTS`.
   - `tests/test_discovery_query_variants.py` — agent query-type matrix
     (no-variant, variant-without-value, variant-with-named-value covering
     multiple dimension types, cross-product family comparison, and explicit
     within-product variant comparison) across `MERCHANTS` + `LIVE_MERCHANTS`;
     asserts the orchestrator never names a variant value as if it were a
     separate product.
   - `tests/fixtures/kith_products.py` — the shared 21-product Kith fixture
     set (footwear/tops/outerwear/bottoms/accessories; no-variant,
     single-dimension, and multi-dimension variants; 6 multi-member families
     across Color/Material/Capacity dimensions; out-of-stock and
     price-spread/override products). **This is the reference-depth template
     for every future live merchant's fixture set** (see
     `tests/test_kith_merchant_journeys.py` above).
   - `tests/test_user_journeys.py` (J121/J122/J123) — variant + no-variant
     cart journey for every `MERCHANTS` domain (J121, parametrised) and for
     `kith.com` (J122), plus a gateway-registration documentation check (J123)
     that any future `LIVE_MERCHANTS` entry needs an equivalent journey.
   - `tests/test_live_shopify_transport.py` (`TestVariantNormalisation`) —
     `_normalise_variants` over Size-only, Default-Title (no-variant), and
     Size+Color (with price-override + out-of-stock combo) Shopify product
     shapes, including the full `_shopify_product_to_dict` → `_to_product`
     pipeline.

   **Phase 1 bugfix addendum (added 2026-06-10):**
   - `tests/test_variant_vocabulary.py` — multi-word colors (`"Clear Yellow"`,
     `"Light Grey"`, `"Dark Grey"`, `"Light Blue"`, `"Dark Green"`) are present
     in `VARIANT_VOCABULARY` as `("Color", value)` and take precedence over
     shorter single-word suffixes (e.g. `"Clear Yellow"` over `"Yellow"`) via
     `_strip_dimension_suffixes`'s longest-match-first ordering.
   - `tests/test_cart_variant_lines.py`
     (`TestFamilyCacheResolutionDemoMerchants`) — for every `MERCHANTS`
     domain, a family-of-1 product (variant and no-variant) is never cached in
     `ctx.session.product_families`, and `/cart/add` still resolves correctly
     (documents the pass-through behaviour `_group_discovered_products`
     relies on).
   - `tests/test_kith_merchant_journeys.py` — `test_add_family_synthesized_variant_id_succeeds`
     and `test_add_family_invalid_variant_id_still_400` cover
     `web/routers/cart.py::add_to_cart` resolving a family-synthesized
     `"{member_id}:{member_variant_id}"` `variant_id` (Bug 3a): 200 with
     correct `selected_options`/price/name for a valid synthesized id, 400 for
     an invalid one.
   - `tests/test_chat_variant_flow.py`
     (`TestMultiDimensionFamilyPartialSelection`) — multi-dimension family
     (Size+Color), partial-then-complete variant selection: user names one
     dimension (Color), agent asks only for the missing dimension (Size), then
     resolves and adds to cart with the correct synthesized `variant_id` and
     `selected_options`. Asserts no "cancelled" status/language at any point
     (Bug 3b / screenshot-4 fix).
   - `tests/browser/test_browser_variant_picker.py`
     (`TestProductDetailPageVariantControls`) — on `/product/{merchant}/{id}`,
     selecting a variant dimension enables the previously-permanently-disabled
     "Select options"/"Add to Cart" button with the correct price and
     `variant_id`, submits correctly, and no-variant PDPs are unaffected
     (Bug 1/2).
   - **Template note**: `web/templates/_variant_picker_controls.html` MUST
     look up `#pdp-variant-id`/`#pdp-price`/`#pdp-add-btn` via
     `document.getElementById(...)` freshly inside `render()` (and at
     pre-selection time) — never cache them as module-level `const`s computed
     once at script-include time. `product_detail.html` intentionally
     `{% include %}`s this controls partial BEFORE the add-to-cart `<form>` it
     controls, so elements it references don't exist yet at script-execution
     time; caching `null` lookups permanently breaks the submit button (Bug
     1/2).

   **Browser E2E tests (run with `pytest tests/browser -q`):**
   - `tests/browser/test_browser_chat_autoscroll.py` — the user's submitted
     bubble must be visible after submit AND after the agent reply finishes
     (the "I have to scroll up to see my own message" regression).
   - `tests/browser/test_browser_chat_cards_appear_without_reload.py` —
     product cards render live without page reload.
   - `tests/browser/test_browser_chat_prose_no_product_names.py` — agent
     text after a products event must not name the products in prose.
   - `tests/browser/test_browser_chat_user_bubble.py` — user bubble appears
     immediately, no duplicate SSE echo.
   - `tests/browser/test_browser_sse_rendering.py` — cards render above the
     summary, click event renders confirmation, badge updates absolute count.
   - `tests/browser/test_browser_typing_indicator.py` — typing wave shown only
     while loading.
   - `tests/browser/test_browser_variant_picker.py` — clicking "Add to cart"
     on a variant product opens the variant picker modal; selecting all
     dimensions (Size, Color, Material, etc.) enables the button; submit adds
     to cart and closes the modal; out-of-stock combinations disable the
     button; no-variant products add directly with no modal. Covers
     single-dimension, two-dimension (Color as chips + non-color as
     `<select>`), and Kith-family variant shapes.

   **Standing visual-verification rule:** for any UX-affecting change, drive
   Playwright against the live server (`/tmp/playwright_verify.py` pattern)
   and capture (a) DOM positions and (b) console + network logs before
   claiming the fix works. Trust unit tests for logic; trust Playwright +
   the actual rendered DOM for UX. Never rely on static analysis or "it
   should work" reasoning for visible behaviour — that produced multiple
   false "fixed" claims across the chat-rendering regression chain.

## Key directories
| Path | Purpose |
|------|---------|
| `config/catalogue.py` | Seed product data for all three demo merchants |
| `agents/` | OrchestratorAgent + four subagents (discovery, evaluation, purchase, tracking) |
| `web/routers/` | FastAPI route handlers (chat, cart, products, account) |
| `web/templates/` | Jinja2 HTML partials |
| `tests/` | pytest test suite (unit + e2e) |

## Tone and Model Behavior Rules

These rules are encoded in `agents/prompts.py` (TONE_RULES + ORCHESTRATOR_TEMPLATE).
Any new agent behavior must be added there AND tested. Never change these without
updating tests.

| Rule | Where encoded |
|------|---------------|
| No emojis in responses | TONE_RULES |
| No sales nudges ("would you like to…") | TONE_RULES |
| No exclamation points about purchases | TONE_RULES |
| No editorialising about search difficulty | TONE_RULES |
| No markdown in prose responses | TONE_RULES |
| Numeric disambiguation (1,2,3 not A,B,C) | TONE_RULES |
| Post-cancellation: one sentence, then stop | TONE_RULES |
| No open invitations ("let me know", "feel free") | TONE_RULES |
| No re-acknowledgement of cached results | TONE_RULES |
| Product cards shown by UI — no prose listing | ORCHESTRATOR_TEMPLATE |
| Brief 2–4 sentence summary after product search | ORCHESTRATOR_TEMPLATE |
| Description inside each card, not below grid | ORCHESTRATOR_TEMPLATE |

## Product Card Rules

- Every product shown anywhere on the site must be rendered as a product card
  (with image, name, price, rating, description). Plain-text product listings
  are not acceptable in the web UI.
- Product cards in chat persist across page reloads via `SessionState.product_card_sets`.
- Each product's `images` list must have 2+ unique entries across the entire
  catalogue. No two products may share the same Unsplash photo ID.
- Chat product cards must show the correct image — the discovery agent may omit
  images; `_enrich_products_with_images()` in `chat.py` fills them from adapters.

## Image Management Rules

- All product images live in `config/catalogue.py` (the `images` field on each product).
- Images propagate through the Shopify adapter → ProductResult → all UI surfaces.
- **Use `images.unsplash.com/photo-{id}?w=800&q=80` format** for all product images.
- Each product's `images` list must have 2+ unique entries across the entire
  catalogue. No two products may share the same Unsplash photo ID.
- Every surface where a product is shown (Explore, Search, Chat cards, Cart drawer,
  Product detail, Orders list, Order detail) must render the image or a 🛍️ fallback.
- Run the URL format test to verify: `pytest tests/test_catalogue_images_unique.py`.

## Asyncio test patterns
```python
# ✅ Safe for test files alphabetically before test_user_journeys.py
loop = asyncio.get_event_loop()
result = loop.run_until_complete(some_coroutine())

# ✅ Safe anywhere — asyncio.Queue() created inside the loop
def test_something(self):
    async def _run():
        queue = asyncio.Queue()
        # ... test logic ...
    asyncio.run(_run())

# ❌ Creates event loop contamination if file sorts before test_user_journeys.py
result = asyncio.run(some_coroutine())
asyncio.Queue()  # at module/sync level after any asyncio.run() call
```
