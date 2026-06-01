# Agentic Commerce — Claude Code Instructions

## Project overview
A FastAPI + Jinja2 + HTMX agentic shopping assistant. Three demo merchants
(Athletic Co, Audio Hub, Coffee Bar) served via stub Shopify adapters.
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

3. **Test coverage must span diverse user journeys, product types, and merchants.**
   Never test a product feature against a single product category in isolation.
   Every product-facing test suite must cover:
   - All three merchants: Athletic Co (shoes/apparel/earbuds), Audio Hub
     (headphones/earbuds/speakers), Coffee Bar (mugs/beans/brewing)
   - Cross-merchant comparisons and single-merchant filters
   - Price ranges: under $30 (soft gate), $100–$500 (explicit gate), >$500
   - Out-of-stock handling (Trail Runner Pro, ath_002)
   - Cart operations from all entry points (Explore click, chat card, typed text)
   - Multi-item and cross-merchant baskets
   - Cancel/retry flows and agent cache re-use

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
