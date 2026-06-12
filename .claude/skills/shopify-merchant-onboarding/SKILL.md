description: Playbook for onboarding a real Shopify merchant onto the agentic commerce platform — from discovery through testing and visual verification.

# Shopify Merchant Onboarding Skill

Step-by-step playbook for adding a real Shopify merchant to the platform.
Covers architecture, code changes, template updates, testing, and verification.
Evolves as we onboard more merchants and discover issues.

**First merchant onboarded:** Kith (kith.com) — June 2026

---

## Prerequisites

Before onboarding a merchant, verify:

1. **The store has a public `/products.json` endpoint** — Shopify stores expose
   this by default. Test: `curl -s https://{domain}/products.json | head -c 200`
2. **Products have handles** — needed to construct URLs (`/products/{handle}`)
3. **Images are on Shopify CDN** — `cdn.shopify.com` URLs load without auth

---

## Architecture: Real vs Demo Merchants

### Transport layer separation
```
StubShopifyTransport  — demo merchants, in-memory seed data from config/catalogue.py
LiveShopifyTransport  — real merchants, fetches from /products.json, caches in-memory
```

Both implement the same `ShopifyTransport` Protocol, so `ShopifyMCPAdapter`
works identically with either. The adapter normalizes everything to UCP-vocabulary
`ProductResult` objects.

### Source protocol distinction
```python
SourceProtocol = Literal["shopify_mcp", "shopify_storefront", ...]
# "shopify_mcp"        — demo merchants (StubShopifyTransport)
# "shopify_storefront"  — live merchants (LiveShopifyTransport)
```

### Purchase flow divergence
- **Demo merchants:** Full checkout lifecycle (create -> update -> complete) via stub
- **Live merchants:** `complete_cart()` raises `NotImplementedError` — purchasing
  redirects to the merchant's own website via "Buy on {Merchant}" CTAs

### External URL detection pattern
All templates use `product.url.startswith('http')` to distinguish live vs demo:
- Live products have full URLs: `https://kith.com/products/some-handle`
- Demo products have no URL or relative paths

---

## Step-by-Step Onboarding

### Step 1: Add to LIVE_MERCHANTS config

**File:** `config/catalogue.py`

Add an entry to the `LIVE_MERCHANTS` dict:
```python
LIVE_MERCHANTS: dict[str, dict] = {
    "kith.com": {
        "store_url": "https://kith.com",
        "display_name": "Kith",
        "logo_url": "https://kith.com/cdn/shop/files/favicon3_32x32.png?v=1613503289",
    },
    # Add new merchant here:
    "newmerchant.com": {
        "store_url": "https://newmerchant.com",
        "display_name": "New Merchant",
        "logo_url": "https://newmerchant.com/path/to/logo.png",  # find via favicon or <link rel="icon">
    },
}
```

**Finding the logo URL:** Check `<link rel="shortcut icon">` in the merchant's
HTML, or try `https://{domain}/favicon.ico`. Shopify stores typically have
favicons at `cdn/shop/files/...`. The brand row tile shows this logo instead of
the generic SVG storefront icon.

### Step 2: Verify registration in session and main

The registration loop in `web/session.py` and `main.py` automatically picks up
new `LIVE_MERCHANTS` entries — no code changes needed unless the merchant needs
custom transport parameters.

**Registration code (web/session.py, ~line 180):**
```python
for domain, meta in LIVE_MERCHANTS.items():
    direct_adapters[domain] = ShopifyMCPAdapter(
        domain,
        LiveShopifyTransport(meta["store_url"]),
        source_protocol="shopify_storefront",
    )
```

The same pattern exists in `main.py` for the CLI path.

### Step 3: Verify templates handle external URLs

All templates already support external merchants via the `startswith('http')` pattern.
No changes needed unless the merchant requires special UI treatment.

**Templates that use external URL detection:**
| Template | What it does |
|----------|-------------|
| `_brand_row.html` | Shows merchant logo (from `logo_url`) instead of generic SVG icon |
| `_product_card.html` | "Buy on {Merchant}" badge at top-right of card |
| `_chat_product_card.html` | "Buy on {Merchant}" button next to "Add to cart" |
| `product_detail.html` | "Buy on {Merchant}" primary CTA above "Add to cart" |
| `_cart_drawer.html` | Per-item "Buy on" link + per-merchant checkout button in footer |

**"Buy on" badge styling rules:**
- Always single line (`whitespace-nowrap`)
- Default: charcoal text (`text-slate-600`) on white/translucent bg with subtle border
- Hover: blue button (`bg-blue-600`) with white text
- Cart footer CTA: same dimensions as "Review purchase" button (`w-full px-3 py-2`)
- Product detail: remains dark bg (primary CTA for external products)

### Step 4: Verify cart.py passes URL and merchant name

**File:** `web/routers/cart.py` — the `add_to_cart` handler stores:
```python
item = {
    ...,
    "url": product.url or "",
    "merchant_name": product.merchant or "",
}
```

This is already generic — works for any merchant.

### Step 5: Test the new merchant

Run these in order:

```bash
# 1. Verify the store's /products.json is accessible
curl -s "https://newmerchant.com/products.json?limit=2" | python3 -m json.tool | head -30

# 2. Run existing tests (no regressions)
python3 -m pytest tests/ -x -q

# 3. Write merchant-specific journey tests (see Testing section below)

# 4. Run full suite including new tests
python3 -m pytest tests/ -x -q
```

### Step 6: Visual verification

Start the server and verify:
```bash
uvicorn web.app:app --port 8000 --loop asyncio --http h11
```

**Checklist:**
- [ ] Merchant appears in brand row on home page
- [ ] Product cards show with images from Shopify CDN
- [ ] "Buy on {Merchant}" badge on product cards
- [ ] Product detail page loads with title, price, images
- [ ] "Buy on {Merchant}" primary CTA on detail page links to merchant site
- [ ] "Add to cart" works, cart drawer shows item
- [ ] Cart drawer shows "Buy on {Merchant}" per-item and in footer
- [ ] Search returns merchant's products alongside demos
- [ ] Demo merchants still work identically (no regressions)
- [ ] Images actually render (HTTP 200, real data, not broken)

---

## Testing Requirements

Every new merchant must have comprehensive tests at two levels.

### Test file naming
```
tests/test_{merchant}_merchant_journeys.py
```

### Required test classes (see `tests/test_kith_merchant_journeys.py` as template)

| Class | Coverage |
|-------|----------|
| `TestProductDataIntegrity` | Fields present, images valid, URLs correct, descriptions non-empty |
| `TestDiscoveryAndSearch` | Merchant in brand row, search returns products, filtering works |
| `TestProductDetail` | Detail page loads, images render, Buy on CTA, breadcrumbs |
| `TestCartOperations` | Add/remove/quantity for merchant products, click notes, idempotency |
| `TestCartDrawerRendering` | Buy on links in cart, mixed carts, checkout button divergence |
| `TestPurchaseFlowDivergence` | LiveShopifyTransport raises NotImplementedError on complete |
| `TestCrossMerchantJourneys` | Mixed baskets with demo + live merchants, navigation between |
| `TestVisualComponents` | Images render, names show, badges present/absent correctly |
| `TestChatProductCard` | Buy on badge in chat, demo cards clean |
| `TestMerchantGatewayIntegration` | Registered, resolvable, in orchestrator's available_merchants |

### Test fixture pattern (mock HTTP, no real network calls)
```python
MOCK_PRODUCTS = [{"id": 123, "title": "...", "variants": [...], ...}]

@pytest.fixture()
def client():
    """TestClient with mocked LiveShopifyTransport for the merchant."""
    from unittest.mock import AsyncMock, patch
    mock_transport = AsyncMock(spec=LiveShopifyTransport)
    mock_transport.search_products.return_value = [...]
    mock_transport.get_product.return_value = ...

    def _patched_make_session(*a, **kw):
        sess = _original_make_session(*a, **kw)
        sess.ctx.merchant_gateway.direct_adapters["newmerchant.com"] = ShopifyMCPAdapter(
            "newmerchant.com", mock_transport, source_protocol="shopify_storefront"
        )
        return sess

    with patch("web.session._make_session", side_effect=_patched_make_session):
        with TestClient(app) as c:
            yield c
```

### Event loop safety rule
If the test file sorts alphabetically **before** `test_user_journeys.py`, use:
```python
loop = asyncio.get_event_loop()
result = loop.run_until_complete(some_coroutine())
```
NOT `asyncio.run()`. See CLAUDE.md for details.

---

## LiveShopifyTransport Details

**File:** `adapters/shopify_mcp.py` (class `LiveShopifyTransport`)

### Product fetching
- Endpoint: `{store_url}/products.json?limit=50&page={page}`
- Paginates up to `max_pages` (default 3, = 150 products max)
- Caches in-memory with `cache_ttl` (default 300s / 5 min)
- User-Agent: `AgenticCommerce/1.0 (UCP Research)`

### Product mapping (`_shopify_product_to_dict`)
```
Shopify field          -> Internal field
product.id             -> id (str)
product.title          -> title
variants[0].price      -> price
any(v.available)       -> available
product.body_html      -> description (HTML stripped)
product.images[].src   -> images[]
product.handle         -> url ({store_url}/products/{handle})
product.vendor         -> vendor
product.product_type   -> attributes.product_type
```

### Search
Matches query (case-insensitive) against: title, description, vendor, product_type.
Falls back to returning first `limit` products if no matches.

### Cart operations
Local in-memory cart only. `complete_cart()` raises `NotImplementedError` with
message: "Live Shopify stores require purchasing on the merchant's website."

---

## Known Issues and Lessons Learned

### Kith onboarding (June 2026)

1. **Flaky API responses:** Kith's `/products.json` occasionally returns empty or
   times out. The `except Exception: break` in `_fetch_products` silently handles
   this — acceptable for resilience but means network failures = empty results.
   **Mitigation:** Tests use mocked HTTP, not real Kith API.

2. **HTML in descriptions:** Shopify `body_html` contains raw HTML tags.
   `_strip_html()` uses regex + `html.unescape()` to clean. Truncates to 500 chars.

3. **Variant pricing:** Uses first variant's price. Products with widely different
   variant prices (e.g. sizes at different price points) will show the first
   variant's price only.

4. **Preview tool sandbox:** The Claude Preview tool's sandbox blocks uvicorn's
   imports (PermissionError on h11, uvloop, httptools). Visual verification must
   use Chrome or curl-based checking.

5. **Domain naming:** Demo merchants use `*.myshopify.com` domains (e.g.
   `athletic-co.myshopify.com`), while live merchants use their public domain
   (e.g. `kith.com`). URLs must use the exact domain from `MERCHANTS` / `LIVE_MERCHANTS`.

---

## Files Modified When Onboarding a Merchant

### Always modified
| File | Change |
|------|--------|
| `config/catalogue.py` | Add entry to `LIVE_MERCHANTS` |

### Already generic (no changes needed for additional merchants)
| File | Why |
|------|-----|
| `adapters/shopify_mcp.py` | `LiveShopifyTransport` + `ShopifyMCPAdapter` are merchant-agnostic |
| `web/session.py` | Registration loop iterates `LIVE_MERCHANTS` automatically |
| `main.py` | Same registration loop for CLI |
| `models/product.py` | `SourceProtocol` already includes `"shopify_storefront"` |
| `web/templates/*` | All use `startswith('http')` pattern, merchant-agnostic |
| `web/routers/cart.py` | Stores `url` and `merchant_name` generically |

### Must create
| File | What |
|------|------|
| `tests/test_{merchant}_merchant_journeys.py` | Comprehensive journey tests |

---

## Quick Onboarding Checklist

For adding the Nth merchant (after Kith), only these steps are needed:

- [ ] Verify `/products.json` is accessible: `curl -s https://{domain}/products.json?limit=1`
- [ ] Add to `LIVE_MERCHANTS` in `config/catalogue.py` (2 lines)
- [ ] Write `tests/test_{merchant}_merchant_journeys.py` (use Kith tests as template)
- [ ] Run `python3 -m pytest tests/ -x -q` — all tests pass, count increases
- [ ] Start server, verify visually: brand row, cards, images, Buy on CTA, cart, search
- [ ] Commit

**Estimated time for Nth merchant: 30 minutes** (mostly writing tests)
