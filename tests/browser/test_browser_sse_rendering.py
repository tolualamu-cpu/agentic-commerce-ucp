"""Browser e2e — the SSE → DOM rendering pipeline (cards, summary, click, badge).

These behaviours are pure CLIENT-SIDE JavaScript in ``_chat_sse.html`` and
``_toast.html`` and normally require a live model to reproduce the orchestrator
burst. Here we inject an EXACT ordered SSE burst via the env-gated
``/__test__/sse/emit`` hook and assert the browser renders it correctly:

  * product cards render ABOVE the summary text (the cards-before-text gate),
  * many ``text`` deltas coalesce into ONE summary bubble,
  * a ``click`` event renders a SINGLE in-log confirmation and NO toast,
  * a ``cart_update`` frame sets the absolute header badge.

A TestClient/source test can assert this wiring is present but cannot prove the
event dispatcher behaves correctly — only a real browser can.
"""

from __future__ import annotations

_HEADPHONES = {
    "merchant_domain": "audio-hub.myshopify.com",
    "product_id": "aud_001",
    "name": "Studio Wireless Headphones",
    "price": "199.00",
    "currency": "USD",
    "rating": 4.6,
    "review_count": 88,
    "description": "Over-ear noise-cancelling headphones.",
    "in_stock": True,
    "images": [
        "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=800&q=80",
    ],
}


def _reveal_chat(page, base_url: str) -> None:
    page.goto(f"{base_url}/chat")
    page.wait_for_selector("#chat-form")
    page.evaluate("window.__chatRevealActive && window.__chatRevealActive()")


def test_cards_render_above_summary(page, live_server, sse_emit):
    """Product cards appear ABOVE the summary bubble (no text-then-jump)."""
    base_url = live_server
    _reveal_chat(page, base_url)

    sse_emit(
        page,
        base_url,
        [
            {"type": "products", "data": {"products": [_HEADPHONES]}},
            {"type": "text", "data": {"delta": "Here is a solid option."}},
            {"type": "done", "data": {}},
        ],
    )

    page.wait_for_selector(".chat-product-card", timeout=8000)
    page.wait_for_selector("#chat-log :text('Here is a solid option.')", timeout=8000)

    # The card's container must come BEFORE the summary bubble in document
    # order. compareDocumentPosition: DOCUMENT_POSITION_FOLLOWING (4) means the
    # summary FOLLOWS the card.
    follows = page.evaluate(
        """() => {
            const card = document.querySelector('.chat-product-card');
            const bubbles = Array.from(document.querySelectorAll('#chat-log .rounded-tl-sm'));
            const summary = bubbles.find(b => b.textContent.includes('Here is a solid option.'));
            if (!card || !summary) return null;
            return (card.compareDocumentPosition(summary) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
        }"""
    )
    assert follows is True


def test_text_deltas_coalesce_into_single_bubble(page, live_server, sse_emit):
    """Multiple text deltas accumulate into ONE agent bubble per turn."""
    base_url = live_server
    _reveal_chat(page, base_url)

    sse_emit(
        page,
        base_url,
        [
            {"type": "text", "data": {"delta": "These "}},
            {"type": "text", "data": {"delta": "three "}},
            {"type": "text", "data": {"delta": "options fit."}},
            {"type": "done", "data": {}},
        ],
    )

    page.wait_for_selector("#chat-log :text('These three options fit.')", timeout=8000)

    # Exactly one agent (assistant) bubble — deltas must not each spawn a bubble.
    agent_bubbles = page.eval_on_selector_all(
        "#chat-log .rounded-tl-sm", "els => els.map(e => e.textContent.trim())"
    )
    assert agent_bubbles == ["These three options fit."], agent_bubbles


def test_click_event_single_confirmation_no_toast(page, live_server, sse_emit):
    """A `click` event renders one in-log confirmation and no toast."""
    base_url = live_server
    _reveal_chat(page, base_url)

    sse_emit(
        page,
        base_url,
        [
            {
                "type": "click",
                "data": {"action": "added", "name": "Studio Wireless Headphones"},
            }
        ],
    )

    confirmation = page.wait_for_selector("#chat-log :text('Added to cart')", timeout=8000)
    assert confirmation is not None
    assert "Studio Wireless Headphones" in page.inner_text("#chat-log")

    # No toast — the in-log bubble is the sole feedback.
    page.wait_for_timeout(300)
    toasts = page.eval_on_selector_all("#toast-stack > div", "els => els.length")
    assert toasts == 0, f"expected no toast on click event, found {toasts}"


def test_cart_update_sets_absolute_badge(page, live_server, sse_emit):
    """A `cart_update` frame sets the header badge to the absolute count."""
    base_url = live_server
    _reveal_chat(page, base_url)

    sse_emit(page, base_url, [{"type": "cart_update", "data": {"count": 3}}])

    page.wait_for_function(
        "() => document.getElementById('cart-badge') "
        "&& document.getElementById('cart-badge').textContent.trim() === '3'",
        timeout=5000,
    )
    assert page.query_selector("#cart-badge").inner_text().strip() == "3"
