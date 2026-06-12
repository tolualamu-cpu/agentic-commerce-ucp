"""Browser e2e — product cards from the SSE `products` event must render
LIVE in the chat log without any page reload.

The recurring bug: cards never appeared in the live chat after an SSE
`products` event. Page reload (which re-renders cards server-side from
SessionState.product_card_sets) made them appear — proving the server-side
storage worked but live injection silently failed. The agent ended up
explaining products in prose because the visual cards were absent.

Multiple defensive fixes guard this regression:
  * `.chat-product-card { opacity: 1 }` default so animation failure
    cannot leave cards invisible at opacity:0.
  * .ac-card-in animation uses `forwards` fill-mode (was `both`) so
    the FROM state (opacity:0) is not applied before animation starts.
  * `_attachCardHandlers` wrapped in try/catch so a throw cannot abort
    the subsequent animation/class-add step.
  * `_skipUntilDone` self-heals on `products` (a products event is
    proof of a new turn).
  * Inline onmouseenter/onmouseleave handlers stripped from
    `_chat_product_card.html` to remove an entire class of innerHTML
    injection failure modes.

This test pins the regression: a `products` event injected onto a fresh
chat page MUST produce a visible card without any navigation or reload.
"""

from __future__ import annotations


def _reveal_chat(page, base_url: str) -> None:
    page.goto(f"{base_url}/chat")
    page.wait_for_selector("#chat-form")
    page.evaluate("window.__chatRevealActive && window.__chatRevealActive()")


# Kith-shaped real-merchant product — the path that previously broke
# (every prior fixture had url=None, so the "Buy on {merchant}" link
# code path was completely unexercised in browser tests).
_KITH_SHIRT = {
    "merchant_domain": "kith.com",
    "product_id": "8286509301888",
    "merchant": "Kith",
    "name": "Kith Crinkled Nylon Ugo Shirt",
    "price": "185.00",
    "currency": "USD",
    "rating": None,
    "review_count": None,
    "description": "Lightweight nylon button-up. Relaxed fit.",
    "in_stock": True,
    "images": [
        "https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=800&q=80",
    ],
    "url": "https://kith.com/products/crinkled-nylon-ugo-shirt",
    "source_protocol": "shopify_storefront",
}


def test_kith_card_appears_live_without_reload(page, live_server, sse_emit):
    """A products SSE event with a Kith URL product renders a card live."""
    base_url = live_server
    url_before = f"{base_url}/chat"
    _reveal_chat(page, base_url)

    sse_emit(
        page,
        base_url,
        [
            {"type": "products", "data": {"products": [_KITH_SHIRT]}},
            {"type": "text", "data": {"delta": "Here is a Kith option."}},
            {"type": "done", "data": {}},
        ],
    )

    card = page.wait_for_selector(".chat-product-card", timeout=8000)
    assert card is not None

    # Prove we never navigated — no page.reload(), no page.goto().
    assert page.url == url_before, f"URL changed during card render: {page.url} != {url_before}"


def test_kith_card_is_actually_visible_in_dom(page, live_server, sse_emit):
    """The card must have non-zero size AND opacity > 0 — not visually hidden."""
    base_url = live_server
    _reveal_chat(page, base_url)

    sse_emit(
        page,
        base_url,
        [
            {"type": "products", "data": {"products": [_KITH_SHIRT]}},
            {"type": "text", "data": {"delta": "Found one."}},
            {"type": "done", "data": {}},
        ],
    )

    page.wait_for_selector(".chat-product-card", timeout=8000)

    # Give animation 1.1s to complete (it's 1000ms). Even if it never fires,
    # the .chat-product-card { opacity:1 } default keeps the card visible.
    page.wait_for_timeout(1100)

    metrics = page.evaluate(
        """() => {
            const card = document.querySelector(".chat-product-card");
            if (!card) return null;
            const rect = card.getBoundingClientRect();
            const style = window.getComputedStyle(card);
            return {
                width: rect.width,
                height: rect.height,
                opacity: parseFloat(style.opacity),
                display: style.display,
                visibility: style.visibility,
            };
        }"""
    )
    assert metrics is not None
    assert metrics["width"] > 0, f"card has zero width: {metrics}"
    assert metrics["height"] > 0, f"card has zero height: {metrics}"
    assert metrics["opacity"] > 0.5, (
        f"card opacity is {metrics['opacity']} — animation likely stuck on "
        f"FROM keyframe (opacity:0). Should be ~1 (default fallback)."
    )
    assert metrics["display"] != "none", f"card display:none: {metrics}"
    assert metrics["visibility"] != "hidden", f"card visibility:hidden: {metrics}"


def test_kith_card_has_buy_on_link(page, live_server, sse_emit):
    """The card for a product with external `url` MUST render the Buy-on
    link with class='ucp-buy-badge' (CSS hover, no inline JS)."""
    base_url = live_server
    _reveal_chat(page, base_url)

    sse_emit(
        page,
        base_url,
        [
            {"type": "products", "data": {"products": [_KITH_SHIRT]}},
            {"type": "done", "data": {}},
        ],
    )

    badge = page.wait_for_selector(".chat-product-card .ucp-buy-badge", timeout=8000)
    assert badge is not None
    href = badge.get_attribute("href")
    assert href == "https://kith.com/products/crinkled-nylon-ugo-shirt"
    target = badge.get_attribute("target")
    assert target == "_blank"


def test_no_inline_onmouseenter_in_rendered_card(page, live_server, sse_emit):
    """The rendered card HTML must contain NO inline onmouseenter handlers.
    This was the regression class that broke live injection."""
    base_url = live_server
    _reveal_chat(page, base_url)

    sse_emit(
        page,
        base_url,
        [
            {"type": "products", "data": {"products": [_KITH_SHIRT]}},
            {"type": "done", "data": {}},
        ],
    )

    page.wait_for_selector(".chat-product-card", timeout=8000)

    has_inline = page.evaluate(
        """() => {
            const card = document.querySelector(".chat-product-card");
            return card ? card.outerHTML.includes("onmouseenter") : null;
        }"""
    )
    assert has_inline is False, (
        "Rendered card HTML contains inline onmouseenter handlers — "
        "use class='ucp-buy-badge' (CSS :hover) instead."
    )


def test_demo_product_card_also_renders_live(page, live_server, sse_emit):
    """A demo product (no url) must ALSO render live — guards against
    regressions that affect the no-url path."""
    base_url = live_server
    _reveal_chat(page, base_url)

    demo_product = dict(_KITH_SHIRT)
    demo_product["url"] = None
    demo_product["merchant"] = "Athletic Co"
    demo_product["merchant_domain"] = "athletic-co.myshopify.com"
    demo_product["product_id"] = "ath_001"
    demo_product["name"] = "Demo Running Shoes"
    demo_product["source_protocol"] = "shopify_mcp"

    sse_emit(
        page,
        base_url,
        [
            {"type": "products", "data": {"products": [demo_product]}},
            {"type": "done", "data": {}},
        ],
    )

    page.wait_for_selector(".chat-product-card", timeout=8000)
    # Demo product has no `url` so no Buy-on badge should render.
    badge_count = page.evaluate(
        """() => document.querySelectorAll(".chat-product-card .ucp-buy-badge").length"""
    )
    assert badge_count == 0, (
        f"Demo product with url=None should have no Buy-on badge; got {badge_count}"
    )
