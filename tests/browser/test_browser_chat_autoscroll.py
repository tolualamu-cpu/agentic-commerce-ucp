"""Browser e2e — the user's freshly-submitted message must be VISIBLE.

The recurring bug: after the user submits a message, their own bubble lands
above the viewport. They have to scroll UP to see what they typed because
the autoscroll fires BEFORE the typing indicator extends the page height
(rAF coalescing dropped the second force-scroll call), and the smooth
animation drifts to the wrong final position when the page grows mid-flight.

The fix (``_chat_sse.html``):
  * scheduleScroll({force:true}) bypasses the rAF coalescing guard so
    successive force-scrolls each re-measure scrollHeight.
  * Force-scrolls use behavior:"auto" so DOM growth doesn't desync a smooth
    animation.
  * After the optimistic ``append("user")`` call, the bubble's own
    ``scrollIntoView({block:"end"})`` is called as a belt-and-braces
    element-relative scroll (immune to page-height changes).

These tests prove the user's bubble is on-screen after submit — a
source-string test cannot prove DOM scroll position.
"""

from __future__ import annotations


def _submit_message(page, base_url: str, text: str) -> None:
    page.goto(f"{base_url}/chat")
    page.wait_for_selector("#chat-form")
    page.fill("#chat-input", text)
    page.click('#chat-form button[type="submit"]')


def test_user_bubble_is_in_viewport_after_submit(page, live_server):
    """The freshly-appended user bubble's bottom edge must be on-screen."""
    base_url = live_server
    _submit_message(page, base_url, "show me t-shirts under $100")

    bubble = page.wait_for_selector(
        "#chat-log .bg-cyan-100:has-text('show me t-shirts under $100')",
        timeout=5000,
    )
    assert bubble is not None

    # The bubble's bottom edge must be at or above the viewport bottom.
    # i.e. the user can see the line they just typed without scrolling up.
    in_view = page.evaluate(
        """() => {
            const el = document.querySelector("#chat-log .bg-cyan-100");
            if (!el) return null;
            const rect = el.getBoundingClientRect();
            // Allow a small tolerance — the input bar is sticky-bottom so
            // perfectly-flush bottom alignment may be slightly clipped.
            return rect.bottom <= window.innerHeight + 5
                && rect.bottom >= 0;
        }"""
    )
    assert in_view is True, (
        "User bubble's bottom edge is above the viewport — user has to scroll "
        "up to see their own message."
    )


def test_user_bubble_at_visible_top_after_submit(page, live_server):
    """The user's bubble must land at the TOP of the visible chat-log
    (just below the sticky header) — not at the bottom of the page.

    NOTE: this REPLACED an earlier test that asserted the page was
    scrolled to the bottom after submit. That was wrong: scrolling to
    bottom puts the user's bubble behind the sticky input AND, once
    the agent reply streams in, pushes the user bubble above the
    viewport — exactly the bug the user kept reporting. The pin-based
    autoscroll keeps the bubble at the visible top instead.
    """
    base_url = live_server
    _submit_message(page, base_url, "i want some headphones")

    page.wait_for_selector(
        "#chat-log .bg-cyan-100:has-text('i want some headphones')",
        timeout=5000,
    )
    page.wait_for_timeout(200)  # let scroll-into-view settle

    state = page.evaluate(
        """() => {
            const bubbles = document.querySelectorAll('#chat-log .bg-cyan-100');
            if (!bubbles.length) return null;
            const el = bubbles[bubbles.length - 1];
            const rect = el.getBoundingClientRect();
            return {top: rect.top, bottom: rect.bottom, viewportH: window.innerHeight};
        }"""
    )
    assert state is not None
    # Bubble's top edge near the top of the viewport (below the sticky
    # header which is ~64px tall), and fully visible.
    assert 0 <= state["top"] <= 200, f"User bubble's top should be near viewport top, got {state}"
    assert state["bottom"] <= state["viewportH"], f"User bubble extends below viewport: {state}"


def test_user_bubble_remains_visible_after_typing_indicator_appears(page, live_server):
    """When the typing indicator lands (adding height), the user bubble
    must STILL be visible — the scroll must catch up."""
    base_url = live_server
    _submit_message(page, base_url, "find me a coffee mug")

    # Wait for typing indicator to appear (the offline reply takes a moment).
    page.wait_for_selector("#chat-typing", timeout=3000)

    # The user bubble must still be in viewport even after typing indicator
    # extends the page height.
    in_view = page.evaluate(
        """() => {
            const bubbles = document.querySelectorAll("#chat-log .bg-cyan-100");
            const el = bubbles[bubbles.length - 1];
            if (!el) return null;
            const rect = el.getBoundingClientRect();
            return rect.bottom <= window.innerHeight + 5 && rect.bottom >= 0;
        }"""
    )
    assert in_view is True, (
        "User bubble fell out of viewport when typing indicator landed — "
        "the rAF guard dropped the second scheduleScroll call."
    )


def test_user_bubble_visible_after_agent_reply_finishes(page, live_server):
    """The CRITICAL test — the bug the user kept reporting. After the agent's
    reply lands AND `done` fires AND scroll has settled, the user's bubble
    must STILL be visible. Previously, `done` cleared the pin BEFORE the
    queued append-agent-bubble+scheduleScroll rAFs ran, and those rAFs
    then scrolled to bottom (with no pin to honour), pushing the user's
    bubble above the viewport."""
    base_url = live_server
    _submit_message(page, base_url, "show me running shoes please")

    # Wait for the offline reply to land.
    page.wait_for_selector("#chat-log :text('Chat is offline')", timeout=8000)
    page.wait_for_timeout(800)  # let all scrolls settle

    state = page.evaluate(
        """() => {
            const bubbles = document.querySelectorAll('#chat-log .bg-cyan-100');
            if (!bubbles.length) return {error: 'no user bubble'};
            const el = bubbles[bubbles.length - 1];
            const rect = el.getBoundingClientRect();
            return {
                top: rect.top, bottom: rect.bottom,
                viewportH: window.innerHeight,
                visible: rect.top >= 0 && rect.bottom <= window.innerHeight,
                aboveViewport: rect.bottom < 0,
            };
        }"""
    )
    assert state.get("visible") is True, (
        f"User bubble fell out of viewport after agent reply finished: "
        f"{state} — done cleared the pin too early, queued scrolls then "
        f"scrolled to bottom, pushing the user's message above the viewport."
    )
    assert not state.get("aboveViewport"), (
        f"User bubble landed ABOVE viewport (the reported bug): {state}"
    )
