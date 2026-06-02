"""Regression guard for the chat entrance animations.

Token streaming was rolled back (it inverted card/text ordering and read as a
rushed flash). In its place, live SSE-rendered chat bubbles fade+slide in once
and product cards cascade in with a per-card stagger. These are presentation-
only (CSS keyframes + class application), verified visually in-browser — this
test just pins the wiring so it can't silently regress:

  * base.html defines the ``ac-bubble-in`` / ``ac-card-in`` keyframes and a
    ``prefers-reduced-motion`` guard.
  * _chat_sse.html applies those classes to live bubbles and staggers cards.

Pure string checks — no asyncio, no event loop, safe regardless of sort order.
"""

from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "web" / "templates"


class TestEntranceAnimationCss:
    def test_base_defines_keyframes_and_classes(self):
        css = (TEMPLATE_DIR / "base.html").read_text()
        assert "@keyframes ac-bubble-in" in css
        assert "@keyframes ac-card-in" in css
        assert ".ac-bubble-in" in css
        assert ".ac-card-in" in css

    def test_base_respects_reduced_motion(self):
        css = (TEMPLATE_DIR / "base.html").read_text()
        assert "prefers-reduced-motion: reduce" in css
        # The reduced-motion block must disable the entrance animations.
        idx = css.index("prefers-reduced-motion: reduce")
        block = css[idx : idx + 200]
        assert "animation: none" in block
        assert "ac-bubble-in" in block and "ac-card-in" in block


class TestEntranceAnimationWiring:
    def test_bubbles_get_animation_class(self):
        js = (TEMPLATE_DIR / "_chat_sse.html").read_text()
        # The shared append() helper tags every live bubble.
        assert 'classList.add("ac-bubble-in")' in js
        # The click-confirmation bubble (built via innerHTML) also animates.
        assert "ac-bubble-in" in js

    def test_cards_are_staggered(self):
        js = (TEMPLATE_DIR / "_chat_sse.html").read_text()
        assert 'classList.add("ac-card-in")' in js
        # Per-card stagger via incremental animation-delay.
        assert "animationDelay" in js
        assert ".chat-product-card" in js

    def test_no_streaming_residue_in_template(self):
        """The drip-buffer / streaming reveal code must be fully gone."""
        js = (TEMPLATE_DIR / "_chat_sse.html").read_text()
        for token in ("_dripTimer", "flushReveal", "startDrip", "runFirstAgentWrap"):
            assert token not in js, f"streaming residue still present: {token}"
