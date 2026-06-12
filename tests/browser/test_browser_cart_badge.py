"""Browser e2e — Bug B: cart badge must equal the cart's TRUE item total.

The recurring bug: clicking "Add to cart" on a chat product card popped a
DUPLICATE notification AND the header badge read "2" for a one-item cart, until
a page navigation forced a resync (then it corrected to "1").

Two root causes, both fixed in ``_chat_sse.html``:
  1. The click handler set the badge from the server's ABSOLUTE ``item_count``
     (``window.__setCartBadge``) instead of a drift-prone relative bump, so the
     badge can never double-count.
  2. The duplicate toast on ``click`` was removed — the in-log confirmation is
     the single source of cart-action feedback.

These tests drive the REAL toggle handler in a browser: inject a product card,
click it, and assert the badge equals exactly 1 (not 2) and the button flips to
"In cart". A source-string assertion cannot prove the click handler computes the
right count at runtime.
"""

from __future__ import annotations

# A real catalogue product so the /cart/add round-trip resolves to a true item.
# Must be a NO-VARIANT product (single SKU): the cart-toggle button does a
# variantless POST to /cart/add, which a variant product (e.g. ath_001, which
# now has Size variants) would reject with 400 "choose a size". ath_005
# (Athletic Wireless Earbuds) is a genuine single-SKU product, so the toggle
# add resolves directly and the badge updates — exactly the path under test.
_ATH_EARBUDS = {
    "merchant_domain": "athletic-co.myshopify.com",
    "product_id": "ath_005",
    "name": "Athletic Wireless Earbuds",
    "price": "79.00",
    "currency": "USD",
    "rating": 4.5,
    "review_count": 240,
    "description": "True-wireless earbuds with active noise cancellation.",
    "in_stock": True,
    "images": [
        "https://images.unsplash.com/photo-1590658268037-6bf12165a8df?w=800&q=80",
    ],
}


def _open_chat_with_one_card(page, base_url: str, sse_emit) -> None:
    """Open /chat, reveal the log in place, and inject one product card."""
    page.goto(f"{base_url}/chat")
    page.wait_for_selector("#chat-form")
    # Reveal the active log (empty state starts hidden) without a reload so the
    # injected cards have a visible home and the single EventSource is alive.
    page.evaluate("window.__chatRevealActive && window.__chatRevealActive()")
    # Inject a `products` SSE burst onto THIS session's queue. The client
    # fetches /chat/products-fragment and renders the real card with its
    # wired-up cart-toggle button.
    sse_emit(page, base_url, [{"type": "products", "data": {"products": [_ATH_EARBUDS]}}])
    page.wait_for_selector(".chat-product-card .cart-toggle-btn", timeout=8000)


def test_add_to_cart_badge_reads_one_not_two(page, live_server, sse_emit):
    """A single add sets the badge to exactly 1 (the Bug-B regression)."""
    base_url = live_server
    _open_chat_with_one_card(page, base_url, sse_emit)

    btn = page.query_selector(".chat-product-card .cart-toggle-btn")
    assert btn.inner_text().strip().startswith("Add to cart")

    btn.click()

    # Badge must become exactly "1". If the relative-bump bug regressed this
    # would read "2".
    page.wait_for_selector("#cart-badge", timeout=5000)
    page.wait_for_function(
        "() => document.getElementById('cart-badge') "
        "&& document.getElementById('cart-badge').textContent.trim() === '1'",
        timeout=5000,
    )
    badge = page.query_selector("#cart-badge")
    assert badge.inner_text().strip() == "1"

    # The button flips to the in-cart state.
    page.wait_for_function(
        "() => document.querySelector('.chat-product-card .cart-toggle-btn')"
        ".textContent.includes('In cart')",
        timeout=5000,
    )


def test_toggle_off_removes_badge(page, live_server, sse_emit):
    """Clicking again removes the item; badge drops to 0 (removed)."""
    base_url = live_server
    _open_chat_with_one_card(page, base_url, sse_emit)

    btn = page.query_selector(".chat-product-card .cart-toggle-btn")
    btn.click()
    page.wait_for_function(
        "() => document.getElementById('cart-badge') "
        "&& document.getElementById('cart-badge').textContent.trim() === '1'",
        timeout=5000,
    )

    # Toggle off: re-query the button (its text/classes were swapped in place).
    btn = page.query_selector(".chat-product-card .cart-toggle-btn")
    btn.click()

    # Badge is removed entirely at count 0.
    page.wait_for_function(
        "() => document.getElementById('cart-badge') === null",
        timeout=5000,
    )
    assert page.query_selector("#cart-badge") is None
    page.wait_for_function(
        "() => document.querySelector('.chat-product-card .cart-toggle-btn')"
        ".textContent.includes('Add to cart')",
        timeout=5000,
    )


def test_add_to_cart_emits_no_duplicate_toast(page, live_server, sse_emit):
    """Adding to cart shows NO toast — the in-log bubble is the sole feedback."""
    base_url = live_server
    _open_chat_with_one_card(page, base_url, sse_emit)

    page.query_selector(".chat-product-card .cart-toggle-btn").click()
    page.wait_for_selector("#cart-badge", timeout=5000)

    # Give any stray toast a beat to render, then assert the toast stack is
    # empty. The duplicate-notification bug would have popped one here.
    page.wait_for_timeout(300)
    toasts = page.eval_on_selector_all("#toast-stack > div", "els => els.length")
    assert toasts == 0, f"expected no toast on cart add, found {toasts}"
