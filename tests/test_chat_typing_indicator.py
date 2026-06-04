"""Unit coverage for the agent "typing" indicator (the loading wave).

WHAT THIS GUARDS
A soft three-dot wave must appear in the chat log ONLY while the agent is
composing a reply, and must vanish the instant the real response lands. These
tests pin the wiring at the source level:

  * the CSS animation exists, is SLOW (a ~1.4s cycle, not a frantic blink), and
    degrades gracefully under prefers-reduced-motion;
  * the client (``_chat_sse.html``) creates the wave on submit (and on a
    mid-run PARTIAL page load), and removes it on every event that represents
    real agent output or the end of the run — and on NOTHING else.

These are static assertions: they prove the handlers are wired the right way.
The runtime PROOF that the wave actually shows during loading, disappears when
the reply arrives, and never appears otherwise lives in the Playwright suite
(``tests/browser/test_browser_typing_indicator.py``), which executes the JS in a
real browser. Both levels are required — a source assertion can't prove the DOM
behaves, and a browser test alone wouldn't localise a wiring regression.

This file sorts before ``test_user_journeys.py`` and does no asyncio work, so it
cannot contaminate the shared event loop (CLAUDE.md asyncio rule).
"""

from __future__ import annotations

import pathlib

_WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
_CHAT_SSE_SRC = (_WEB / "templates" / "_chat_sse.html").read_text(encoding="utf-8")
_BASE_SRC = (_WEB / "templates" / "base.html").read_text(encoding="utf-8")


# ── CSS: the animation exists, is slow, and is reduced-motion aware ──────────


class TestTypingCss:
    def test_keyframes_defined(self):
        assert "@keyframes ac-typing-wave" in _BASE_SRC

    def test_dot_class_defined(self):
        assert ".ac-typing-dot" in _BASE_SRC

    def test_motion_is_slow_not_frantic(self):
        # The cycle must be a calm ~1.4s. Assert the duration is wired and is
        # clearly slower than a snappy sub-second blink.
        assert "ac-typing-wave 1.4s" in _BASE_SRC
        # The lift is gentle (a few px), never a big jump.
        assert "translateY(-4px)" in _BASE_SRC

    def test_dots_are_staggered_into_a_wave(self):
        # Three dots, the 2nd and 3rd delayed so the wave travels across them.
        assert ".ac-typing-dot:nth-child(2)" in _BASE_SRC
        assert ".ac-typing-dot:nth-child(3)" in _BASE_SRC
        assert "animation-delay: 0.2s" in _BASE_SRC
        assert "animation-delay: 0.4s" in _BASE_SRC

    def test_respects_reduced_motion(self):
        # Under reduced-motion the dots stop animating but remain visible.
        idx = _BASE_SRC.find("prefers-reduced-motion")
        assert idx != -1
        reduced_block = _BASE_SRC[idx : idx + 300]
        assert ".ac-typing-dot" in reduced_block
        assert "animation: none" in reduced_block


# ── Client helpers exist and build a singleton three-dot wave ────────────────


class TestTypingHelpers:
    def test_show_and_hide_helpers_defined(self):
        assert "function showTyping()" in _CHAT_SSE_SRC
        assert "function hideTyping()" in _CHAT_SSE_SRC

    def test_indicator_is_a_singleton(self):
        # showTyping bails if the indicator already exists, so rapid submits
        # can never stack two waves.
        assert 'if (document.getElementById("chat-typing")) return' in _CHAT_SSE_SRC

    def test_indicator_uses_three_animated_dots(self):
        assert _CHAT_SSE_SRC.count('class="ac-typing-dot"') == 3

    def test_indicator_id_is_chat_typing(self):
        assert 'wrap.id = "chat-typing"' in _CHAT_SSE_SRC

    def test_hide_removes_the_element(self):
        # hideTyping must actually detach the node, not just hide it.
        idx = _CHAT_SSE_SRC.find("function hideTyping()")
        assert idx != -1
        body = _CHAT_SSE_SRC[idx : idx + 200]
        assert "remove()" in body


# ── SHOWN only on submit / mid-run load — never otherwise ────────────────────


class TestTypingShownOnlyWhenLoading:
    def test_shown_on_submit(self):
        # The beforeRequest (submit) handler kicks off the wave.
        idx = _CHAT_SSE_SRC.find('addEventListener("htmx:beforeRequest"')
        assert idx != -1
        handler = _CHAT_SSE_SRC[idx : idx + 1300]
        assert "showTyping();" in handler

    def test_shown_on_partial_midrun_load(self):
        # If the page loads mid-run (user turn rendered, no reply yet), show it.
        assert "if (_pendingUserDedup) showTyping();" in _CHAT_SSE_SRC

    def test_not_shown_by_swallowed_user_echo(self):
        # The `user` echo is swallowed and must NOT create a wave (we're already
        # showing it from submit; re-adding would be redundant/duplicate logic).
        idx = _CHAT_SSE_SRC.find('if (t === "user") {')
        assert idx != -1
        # Bound the user branch at the next `} else if`.
        end = _CHAT_SSE_SRC.find("} else if", idx)
        user_branch = _CHAT_SSE_SRC[idx:end]
        assert "showTyping" not in user_branch

    def test_only_one_call_site_creates_it_in_dispatcher(self):
        # showTyping is CALLED exactly twice in the whole script: the submit
        # handler and the PARTIAL-load line. Nothing in the SSE dispatcher
        # (cart_update path, click, etc.) should ever create the wave. Count
        # invocations (``showTyping();``) — the definition is ``function
        # showTyping()`` (no semicolon) and is excluded.
        assert _CHAT_SSE_SRC.count("showTyping();") == 2


# ── HIDDEN on every real-output / run-end event ──────────────────────────────


class TestTypingHiddenWhenResponseArrives:
    def _branch(self, marker: str) -> str:
        idx = _CHAT_SSE_SRC.find(marker)
        assert idx != -1, f"branch not found: {marker}"
        end = _CHAT_SSE_SRC.find("} else if", idx + len(marker))
        # Last branch (no trailing else-if): fall back to end of dispatcher.
        if end == -1:
            end = _CHAT_SSE_SRC.find("});", idx)
        return _CHAT_SSE_SRC[idx:end]

    def test_hidden_on_text_delta(self):
        assert "hideTyping()" in self._branch('} else if (t === "text") {')

    def test_hidden_on_products(self):
        assert "hideTyping()" in self._branch('} else if (t === "products") {')

    def test_hidden_on_done(self):
        assert "hideTyping()" in self._branch('} else if (t === "done") {')

    def test_hidden_on_bubble_end(self):
        assert "hideTyping()" in self._branch('} else if (t === "bubble_end") {')

    def test_hidden_on_error(self):
        assert "hideTyping()" in self._branch('} else if (t === "error") {')

    def test_hidden_on_click_confirmation(self):
        assert "hideTyping()" in self._branch('} else if (t === "click") {')

    def test_text_hides_before_appending_bubble(self):
        # hideTyping must run synchronously at the top of the text branch, NOT
        # inside the _afterCards gate, so the wave never overlaps the reply.
        branch = self._branch('} else if (t === "text") {')
        assert branch.index("hideTyping()") < branch.index("_afterCards")


# ── Loading cue stays at the bottom during intermediate (tool) events ────────


class TestTypingStaysLastDuringToolUse:
    def test_tool_start_bumps_wave_to_bottom(self):
        idx = _CHAT_SSE_SRC.find('} else if (t === "tool_start") {')
        assert idx != -1
        end = _CHAT_SSE_SRC.find("} else if", idx + 10)
        branch = _CHAT_SSE_SRC[idx:end]
        # Tool work is still "loading" — keep the wave, but below the tool line.
        assert "bumpTypingToBottom()" in branch
        assert "hideTyping" not in branch
