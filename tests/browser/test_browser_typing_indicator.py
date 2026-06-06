"""Browser e2e — the agent "typing" indicator (the loading wave).

Proves, in a REAL browser executing the actual JS, that the three-dot wave:

  * APPEARS the instant the user submits and STAYS while we await the reply,
  * is REMOVED the moment the real response lands (text OR product cards) and
    does not persist alongside it,
  * NEVER appears without a submit (plain page load, injected cart_update),
  * is a SINGLETON (a rapid double submit cannot stack two waves),
  * does not disturb chat order/visibility — cards still render ABOVE the
    summary once the wave is gone.

Determinism: the server runs OFFLINE. For the "appears + persists" cases we
intercept the POST /chat round-trip so no auto-reply is ever queued, leaving the
wave up indefinitely to assert against; we then inject an exact SSE burst via
the env-gated /__test__/sse/emit hook to drive the removal. For the "removed on
real reply" case we let the offline flow complete and assert the wave is gone
once the reply bubble is on screen (a state that, once reached, is stable — no
race).
"""

from __future__ import annotations

_ATH_RUNNING_SHOE = {
    "merchant_domain": "athletic-co.myshopify.com",
    "product_id": "ath_001",
    "name": "Demo Running Shoes",
    "price": "129.99",
    "currency": "USD",
    "rating": 4.5,
    "review_count": 240,
    "description": "Lightweight road running shoes. Cushioned midsole.",
    "in_stock": True,
    "images": [
        "https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=800&q=80",
    ],
}


def _intercept_post_chat(page) -> None:
    """Swallow the POST /chat round-trip (fulfil 202, never hit the server).

    The optimistic user bubble + typing wave are drawn on htmx:beforeRequest,
    BEFORE the POST. Fulfilling the POST without reaching the server means the
    offline handler never queues its text/done reply, so the wave stays up for
    as long as we want to assert against it. GET /chat and /chat/stream are not
    matched by the ``**/chat`` glob's exact tail, but we guard on method too.
    """

    def handler(route):
        if route.request.method == "POST":
            route.fulfill(status=202, content_type="application/json", body="{}")
        else:
            route.continue_()

    page.route("**/chat", handler)


def _open_empty_chat(page, base_url: str) -> None:
    page.goto(f"{base_url}/chat")
    page.wait_for_selector("#chat-form")


def test_typing_wave_appears_on_submit_and_persists_while_waiting(page, live_server):
    """The wave shows immediately on submit and stays while awaiting a reply."""
    base_url = live_server
    _open_empty_chat(page, base_url)
    _intercept_post_chat(page)

    page.fill("#chat-input", "find running shoes")
    page.click('#chat-form button[type="submit"]')

    # The singleton wave appears with exactly three animated dots.
    page.wait_for_selector("#chat-typing", timeout=5000)
    dots = page.eval_on_selector_all("#chat-typing .ac-typing-dot", "els => els.length")
    assert dots == 3

    # It is genuinely animated (a running CSS animation, not a static element).
    anim = page.eval_on_selector(
        "#chat-typing .ac-typing-dot",
        "el => getComputedStyle(el).animationName",
    )
    assert anim == "ac-typing-wave"

    # No reply will arrive (POST intercepted) — the wave persists.
    page.wait_for_timeout(400)
    assert page.query_selector("#chat-typing") is not None
    # And the user's own bubble is present (chat flow intact).
    assert page.query_selector("#chat-log .bg-cyan-100") is not None


def test_typing_wave_removed_when_text_reply_arrives(page, live_server):
    """Once the agent's offline reply lands, the wave is gone (not alongside)."""
    base_url = live_server
    _open_empty_chat(page, base_url)

    # Let the REAL offline flow run (no interception): submit → wave → reply.
    page.fill("#chat-input", "how much are the premium shoes")
    page.click('#chat-form button[type="submit"]')

    # When the reply bubble is on screen the run is done — a stable state.
    page.wait_for_selector("#chat-log :text('Chat is offline')", timeout=8000)
    # The wave must be gone, never coexisting with the response.
    assert page.query_selector("#chat-typing") is None


def test_typing_wave_removed_when_cards_arrive_and_order_intact(page, live_server, sse_emit):
    """Cards replace the wave and still render ABOVE the summary (order intact)."""
    base_url = live_server
    _open_empty_chat(page, base_url)
    _intercept_post_chat(page)

    # Submit (intercepted) so the wave shows but no auto-reply arrives.
    page.fill("#chat-input", "find running shoes")
    page.click('#chat-form button[type="submit"]')
    page.wait_for_selector("#chat-typing", timeout=5000)

    # Now drive the real-output burst ourselves: products, then summary, done.
    sse_emit(
        page,
        base_url,
        [
            {"type": "products", "data": {"products": [_ATH_RUNNING_SHOE]}},
            {"type": "text", "data": {"delta": "This one fits your run."}},
            {"type": "done", "data": {}},
        ],
    )

    page.wait_for_selector(".chat-product-card", timeout=8000)
    page.wait_for_selector("#chat-log :text('This one fits your run.')", timeout=8000)

    # Wave gone.
    assert page.query_selector("#chat-typing") is None

    # Order preserved: the card precedes the summary bubble (no regression to
    # the cards-before-text guarantee now that a wave sat between them).
    follows = page.evaluate(
        """() => {
            const card = document.querySelector('.chat-product-card');
            const bubbles = Array.from(document.querySelectorAll('#chat-log .rounded-tl-sm'));
            const summary = bubbles.find(b => b.textContent.includes('This one fits your run.'));
            if (!card || !summary) return null;
            return (card.compareDocumentPosition(summary) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
        }"""
    )
    assert follows is True


def test_no_wave_on_plain_page_load(page, live_server):
    """A freshly loaded /chat shows no wave (nothing is loading)."""
    base_url = live_server
    _open_empty_chat(page, base_url)
    page.wait_for_timeout(300)
    assert page.query_selector("#chat-typing") is None


def test_no_wave_from_injected_cart_update(page, live_server, sse_emit):
    """A cart_update frame (no submit) must never spawn the wave."""
    base_url = live_server
    _open_empty_chat(page, base_url)
    page.evaluate("window.__chatRevealActive && window.__chatRevealActive()")

    sse_emit(page, base_url, [{"type": "cart_update", "data": {"count": 2}}])
    # Badge updates, but no loading wave appears.
    page.wait_for_function(
        "() => document.getElementById('cart-badge') "
        "&& document.getElementById('cart-badge').textContent.trim() === '2'",
        timeout=5000,
    )
    assert page.query_selector("#chat-typing") is None


def test_no_wave_from_injected_click_confirmation(page, live_server, sse_emit):
    """A standalone click confirmation (no submit) must not spawn the wave."""
    base_url = live_server
    _open_empty_chat(page, base_url)
    page.evaluate("window.__chatRevealActive && window.__chatRevealActive()")

    sse_emit(
        page,
        base_url,
        [{"type": "click", "data": {"action": "added", "name": "Demo Running Shoes"}}],
    )
    page.wait_for_selector("#chat-log :text('Added to cart')", timeout=8000)
    assert page.query_selector("#chat-typing") is None


def test_wave_is_singleton_on_rapid_double_submit(page, live_server):
    """Two quick submits never stack two waves."""
    base_url = live_server
    _open_empty_chat(page, base_url)
    _intercept_post_chat(page)

    page.fill("#chat-input", "first query")
    page.click('#chat-form button[type="submit"]')
    page.wait_for_selector("#chat-typing", timeout=5000)

    page.fill("#chat-input", "second query")
    page.click('#chat-form button[type="submit"]')
    page.wait_for_timeout(200)

    count = page.eval_on_selector_all("#chat-typing", "els => els.length")
    assert count == 1
