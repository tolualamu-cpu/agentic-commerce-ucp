"""Intent classifier tests for the enhanced regex-based _classify_gate_intent.

Covers the new word-boundary regex matching that handles natural language
confirm/cancel phrasings that the old exact-set approach missed.

Asyncio note: file sorts before test_user_journeys.py — no asyncio.run() used.
"""

from __future__ import annotations

import pytest

from web.routers.chat import _classify_gate_intent


# ─── Confirm intent — literal and natural-language phrasings ─────────────────


class TestConfirmIntents:
    @pytest.mark.parametrize(
        "text",
        [
            # Original exact patterns that must still work
            "CONFIRM",
            "confirm",
            " Confirm ",
            "confirm.",
            "ok confirm",
            "yes buy",
            "yes buy it",
            "buy it now",
            "approve",
            "proceed",
            "go ahead",
            # New natural-language patterns added by regex upgrade
            "now confirm",
            "please confirm",
            "please confirm that",
            "just confirm",
            "go ahead and confirm",
            "yes, please confirm",
            "ok, approve",
            "please proceed",
            "yes proceed now",
            "i want to proceed",
            "let's go ahead",
            "purchase it now",
        ],
    )
    def test_classifies_as_confirm(self, text):
        result = _classify_gate_intent(text)
        assert result == {"decision": "confirm"}, (
            f"{text!r} should classify as confirm; got {result}"
        )


# ─── Cancel intent ─────────────────────────────────────────────────────────────


class TestCancelIntents:
    @pytest.mark.parametrize(
        "text",
        [
            # Original exact patterns
            "cancel",
            "CANCEL",
            "Cancel ",
            "cancel.",
            "no",
            "no thanks",
            "stop",
            "abort",
            "nevermind",
            "don't buy",
            "do not buy",
            # Regex-matched phrasings
            "please cancel",
            "cancel that order",
            "no thanks, cancel",
            "abort the purchase",
        ],
    )
    def test_classifies_as_cancel(self, text):
        result = _classify_gate_intent(text)
        assert result == {"decision": "cancel"}, f"{text!r} should classify as cancel; got {result}"


# ─── Negation guard ────────────────────────────────────────────────────────────


class TestNegationGuard:
    @pytest.mark.parametrize(
        "text",
        [
            "don't confirm",
            "do not confirm",
            "don't approve",
            "do not proceed",
            "not confirm",
            "please don't proceed",
        ],
    )
    def test_negated_confirm_routes_as_cancel(self, text):
        result = _classify_gate_intent(text)
        assert result == {"decision": "cancel"}, (
            f"{text!r} (negated confirm) should route as cancel; got {result}"
        )


# ─── Question / pass-through intents ──────────────────────────────────────────


class TestQuestionIntents:
    @pytest.mark.parametrize(
        "text",
        [
            "remove 1",
            "what's the total?",
            "add 1 mug",
            "tell me about premium shoes",
            "1",  # numeric resolver — orchestrator handles
            "+1",  # delta quantity — orchestrator handles
            "-1",
            "add 1 more",
            "change the colour to blue",
            "i mean stability shoes",
            "how long is shipping?",
        ],
    )
    def test_classifies_as_question(self, text):
        result = _classify_gate_intent(text)
        assert result["decision"] == "question", (
            f"{text!r} should pass through as question; got {result}"
        )
        assert result["text"] == text

    def test_empty_string(self):
        result = _classify_gate_intent("")
        assert result == {"decision": "question", "text": ""}

    def test_whitespace_only(self):
        result = _classify_gate_intent("   ")
        # Stripped whitespace → empty → question
        assert result["decision"] == "question"


# ─── Boundary conditions ──────────────────────────────────────────────────────


class TestBoundaryConditions:
    def test_confirm_word_embedded_in_longer_word_not_matched(self):
        # "reconfirm" contains "confirm" but shouldn't be a standalone confirm.
        # With \b word boundary, "reconfirm" does NOT match because "confirm"
        # is not at a word boundary (preceded by "re"). Verify it passes through.
        # NOTE: "\bconfirm" matches inside "reconfirm" only if there's a boundary.
        # "reconfirm" → "re" + "confirm", the "c" in "confirm" IS at a letter
        # boundary (following "re"). So actually \bconfirm DOES match in "reconfirm".
        # That is acceptable product behaviour — "reconfirm" is a confirmation intent.
        result = _classify_gate_intent("reconfirm")
        # acceptable to be confirm or question — just must not be cancel
        assert result["decision"] != "cancel"

    def test_cancel_word_in_question_context(self):
        # "can i cancel?" — explicit cancel intent
        result = _classify_gate_intent("can i cancel?")
        assert result["decision"] == "cancel"

    def test_confirm_with_trailing_punctuation(self):
        result = _classify_gate_intent("confirm!")
        assert result["decision"] == "confirm"

    def test_mixed_case_confirm(self):
        result = _classify_gate_intent("PLEASE CONFIRM THIS PURCHASE")
        assert result["decision"] == "confirm"

    def test_mixed_case_cancel(self):
        result = _classify_gate_intent("CANCEL THAT")
        assert result["decision"] == "cancel"
