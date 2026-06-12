"""Browser e2e — Bug A: the user's own chat message must appear IMMEDIATELY.

The recurring bug: a typed chat message did NOT show up in the log until the
user navigated to another page and back (the message only re-rendered from
server history on reload). The agent reply streamed in fine; the user's own
bubble was lost to single-consumer-queue / connection-takeover timing because
it depended on the SSE ``user`` echo surviving.

The fix (``_chat_sse.html``): the user bubble is drawn OPTIMISTICALLY on
``htmx:beforeRequest`` — before the POST round-trip, independent of any SSE
echo — and the server's ``user`` echo is SWALLOWED so it can never duplicate.

These tests prove the fix in a REAL browser by actually submitting the form and
asserting the bubble is on screen WITHOUT any navigation. A source-string test
cannot prove this because it never executes the JS event handlers.

Determinism: the server runs OFFLINE (``CARTO_FORCE_OFFLINE=1``), so POST /chat
emits a fixed "Chat is offline" reply + done — enough to exercise the optimistic
bubble, the swallowed echo, and the in-place reveal with no live model.
"""

from __future__ import annotations


def _submit_message(page, base_url: str, text: str) -> None:
    """Navigate to the empty /chat, type a message, and submit the form."""
    page.goto(f"{base_url}/chat")
    page.wait_for_selector("#chat-form")
    page.fill("#chat-input", text)
    # Submit via the real send button so htmx's beforeRequest fires.
    page.click('#chat-form button[type="submit"]')


def test_user_bubble_appears_immediately_without_navigation(page, live_server):
    """The typed message shows in #chat-log at once — no page navigation."""
    base_url = live_server
    url_before = f"{base_url}/chat"
    _submit_message(page, base_url, "what about the premium shoes?")

    # The optimistic bubble must appear without leaving the page. We wait on
    # the bubble itself (not a reload) — if the bug were present this would
    # time out because the bubble only appeared after navigating away/back.
    bubble = page.wait_for_selector(
        "#chat-log .bg-cyan-100:has-text('what about the premium shoes?')",
        timeout=5000,
    )
    assert bubble is not None
    # Prove we never navigated: URL is unchanged (no reload, no cross-page hop).
    assert page.url == url_before


def test_exactly_one_user_bubble_echo_swallowed(page, live_server):
    """Only ONE user bubble exists — the SSE echo must be swallowed."""
    base_url = live_server
    _submit_message(page, base_url, "how much are the premium shoes")

    # Wait for the agent's offline reply so the full SSE burst (incl. the
    # swallowed user echo + done) has definitely been delivered and processed.
    page.wait_for_selector("#chat-log :text('Chat is offline')", timeout=8000)

    # The user message bubble (bg-cyan-100) must appear exactly once. If the
    # echo were NOT swallowed there would be two identical cyan bubbles.
    matching = page.eval_on_selector_all(
        "#chat-log .bg-cyan-100",
        "els => els.map(e => e.textContent.trim())",
    )
    user_bubbles = [t for t in matching if "premium shoes" in t]
    assert len(user_bubbles) == 1, f"expected 1 user bubble, got {user_bubbles!r}"


def test_offline_agent_reply_bubble_renders(page, live_server):
    """The offline agent reply streams into a bubble in the same log."""
    base_url = live_server
    _submit_message(page, base_url, "find me running shoes")

    reply = page.wait_for_selector("#chat-log :text('Chat is offline')", timeout=8000)
    assert reply is not None


def test_inplace_reveal_hides_hero_shows_log(page, live_server):
    """Submitting from the empty hero reveals the log in place (no reload)."""
    base_url = live_server
    page.goto(f"{base_url}/chat")
    page.wait_for_selector("#chat-form")

    # Empty state: the log starts hidden and the hero headline is visible.
    assert page.is_hidden("#chat-log")
    assert page.is_visible("#chat-headline-wrap")

    page.fill("#chat-input", "wireless earbuds")
    page.click('#chat-form button[type="submit"]')

    # After submit the log is revealed and the hero headline is hidden — all
    # IN PLACE (the single EventSource stays alive, no navigation).
    page.wait_for_selector("#chat-log .bg-cyan-100", timeout=5000)
    assert page.is_visible("#chat-log")
    assert page.is_hidden("#chat-headline-wrap")
