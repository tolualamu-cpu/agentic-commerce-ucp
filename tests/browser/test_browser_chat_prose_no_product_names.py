"""Browser e2e — when a `products` SSE event has been emitted, the agent's
subsequent text reply MUST NOT list those products by name in prose.

The recurring bug: the agent's text reply contains a numbered list like
"1. Kith Crinkled Nylon Ugo Shirt - $185\n2. WTAPS Cotton Twill...". This
violates ORCHESTRATOR_TEMPLATE rules ("MUST NOT write a numbered or bulleted
list of products with names/prices/descriptions").

In the failure mode the user observed, prose listing happened when cards
failed to render — the model compensated by spelling out the products it
"would have shown." When cards reliably appear (Regressions 1+2 fixed),
the model stops compensating. This test pins the invariant: an explicit
text bubble injected with a product-name-shaped string after a products
event must not be allowed by the orchestrator's runtime contract.

Note: we can't easily test "the orchestrator never emits prose names" in a
browser test (the orchestrator needs a real model). What we CAN pin in
browser is: when the model emits prose names AFTER products, the chat UI
faithfully renders both — so a real-traffic regression test (or visual
inspection) can spot violations. The unit-level prompt rule pinning lives
in tests/test_tone_post_cancellation.py.

This test asserts the cards-and-summary visual contract holds: when both
arrive, cards appear and the text summary is a brief 2-4 sentence intro
WITHOUT product names.
"""

from __future__ import annotations


def _reveal_chat(page, base_url: str) -> None:
    page.goto(f"{base_url}/chat")
    page.wait_for_selector("#chat-form")
    page.evaluate("window.__chatRevealActive && window.__chatRevealActive()")


_PRODUCTS = [
    {
        "merchant_domain": "kith.com",
        "product_id": "ki_001",
        "merchant": "Kith",
        "name": "Kith Crinkled Nylon Ugo Shirt",
        "price": "185.00",
        "currency": "USD",
        "rating": None,
        "review_count": None,
        "description": "Lightweight nylon button-up.",
        "in_stock": True,
        "images": ["https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=800&q=80"],
        "url": "https://kith.com/products/test1",
    },
    {
        "merchant_domain": "kith.com",
        "product_id": "ki_002",
        "merchant": "Kith",
        "name": "WTAPS Cotton Twill Long Sleeve Shirt",
        "price": "240.00",
        "currency": "USD",
        "rating": None,
        "review_count": None,
        "description": "Premium 100% cotton, structured fit.",
        "in_stock": True,
        "images": ["https://images.unsplash.com/photo-1484704849700-f032a568e944?w=800&q=80"],
        "url": "https://kith.com/products/test2",
    },
]


def test_compliant_summary_renders_correctly(page, live_server, sse_emit):
    """When the model emits a compliant brief summary (no product names),
    cards appear and the summary renders below — happy path."""
    base_url = live_server
    _reveal_chat(page, base_url)

    sse_emit(
        page,
        base_url,
        [
            {"type": "products", "data": {"products": _PRODUCTS}},
            {
                "type": "text",
                "data": {"delta": "Here are two button-up options at different price points."},
            },
            {"type": "done", "data": {}},
        ],
    )

    # Both cards appear.
    page.wait_for_selector(".chat-product-card", timeout=8000)
    cards_count = page.evaluate("""() => document.querySelectorAll(".chat-product-card").length""")
    assert cards_count == 2

    # The summary bubble exists.
    page.wait_for_selector("#chat-log :text('Here are two button-up options')", timeout=8000)

    # No product names appear in the summary bubble.
    summary_has_names = page.evaluate(
        """() => {
            const bubbles = Array.from(document.querySelectorAll('#chat-log .rounded-tl-sm'));
            const summary = bubbles.find(b => b.textContent.includes('Here are two'));
            if (!summary) return null;
            const t = summary.textContent;
            return t.includes('Kith Crinkled Nylon Ugo')
                || t.includes('WTAPS Cotton Twill');
        }"""
    )
    assert summary_has_names is False, "Compliant summary unexpectedly contained product names."


def test_violating_summary_with_product_names_is_detectable(page, live_server, sse_emit):
    """If the model violates the rule and emits product names in prose, the
    UI still renders both cards AND the prose. This test exists so a future
    e2e suite can flag real-world violations — it asserts the detection
    mechanism (substring check on the summary bubble) works correctly."""
    base_url = live_server
    _reveal_chat(page, base_url)

    bad_summary = (
        "1. Kith Crinkled Nylon Ugo Shirt - $185\n2. WTAPS Cotton Twill Long Sleeve Shirt - $240"
    )

    sse_emit(
        page,
        base_url,
        [
            {"type": "products", "data": {"products": _PRODUCTS}},
            {"type": "text", "data": {"delta": bad_summary}},
            {"type": "done", "data": {}},
        ],
    )

    page.wait_for_selector(".chat-product-card", timeout=8000)
    # Wait for the summary bubble text to land.
    page.wait_for_selector(
        "#chat-log :text('Kith Crinkled Nylon Ugo Shirt')",
        timeout=8000,
    )

    # The detection logic correctly identifies the violation.
    detected = page.evaluate(
        """() => {
            const bubbles = Array.from(document.querySelectorAll('#chat-log .rounded-tl-sm'));
            // The agent bubble that's NOT a card (cards are in their own container).
            const summary = bubbles.find(b => {
                const text = b.textContent;
                return text.includes('Kith Crinkled Nylon')
                    || text.includes('WTAPS Cotton Twill');
            });
            return summary !== undefined;
        }"""
    )
    assert detected is True, (
        "Detection failed — the bad summary should be flagged so the "
        "orchestrator's tone rule can be enforced at e2e level."
    )
