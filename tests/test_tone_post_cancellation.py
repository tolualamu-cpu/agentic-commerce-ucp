"""Tone and prompt rule tests for post-cancellation behavior,
invitation bans, product card display rules, and image schema.

Sorts after test_user_journeys (t > u is false — 'tone' < 'user', so
this file sorts BEFORE test_user_journeys). Use get_event_loop().
"""

from __future__ import annotations

import pytest

from agents.prompts import (
    DISCOVERY,
    EVALUATION,
    PURCHASE,
    TONE_RULES,
    TRACKING,
    orchestrator_prompt,
)


ORCHESTRATOR = orchestrator_prompt(["athletic-co.myshopify.com"])


class TestPostCancellationToneRule:
    def test_tone_rules_mention_cancelled(self):
        """TONE_RULES must explicitly address post-cancellation behaviour."""
        assert (
            "cancel" in TONE_RULES.lower()
        ), "TONE_RULES must contain guidance for post-cancellation responses"

    def test_tone_rules_forbid_let_me_know(self):
        assert (
            "let me know" in TONE_RULES.lower()
        ), "TONE_RULES must list 'let me know' as a forbidden phrase"

    def test_tone_rules_forbid_feel_free(self):
        assert (
            "feel free" in TONE_RULES.lower()
        ), "TONE_RULES must list 'feel free' as a forbidden phrase"

    def test_tone_rules_forbid_whenever_ready(self):
        assert (
            "whenever you're ready" in TONE_RULES.lower()
        ), "TONE_RULES must list 'whenever you're ready' as forbidden"

    def test_tone_rules_forbid_if_youd_like(self):
        assert (
            "if you'd like" in TONE_RULES.lower() or "if you" in TONE_RULES.lower()
        ), "TONE_RULES must explicitly list open-invitation patterns as forbidden"

    def test_tone_rules_state_one_sentence_rule(self):
        assert (
            "one sentence" in TONE_RULES.lower()
        ), "TONE_RULES must state the post-cancellation one-sentence rule"


class TestProductCardDisplayRule:
    def test_orchestrator_has_product_card_display_section(self):
        assert (
            "PRODUCT CARD DISPLAY" in ORCHESTRATOR
        ), "ORCHESTRATOR_TEMPLATE must contain a PRODUCT CARD DISPLAY section"

    def test_product_card_rule_forbids_prose_listing(self):
        assert (
            "MUST NOT" in ORCHESTRATOR
        ), "Product card rule must use MUST NOT to prohibit prose listings"

    def test_product_card_rule_mentions_list_ban(self):
        lower = ORCHESTRATOR.lower()
        assert (
            "numbered" in lower or "list" in lower
        ), "Product card rule must ban numbered/bulleted product lists"

    def test_product_card_rule_mentions_description_in_card(self):
        lower = ORCHESTRATOR.lower()
        assert (
            "description" in lower and "card" in lower
        ), "Product card rule must state description belongs inside each card"

    def test_product_card_rule_requires_brief_summary(self):
        assert (
            "paragraph" in ORCHESTRATOR.lower() or "sentence" in ORCHESTRATOR.lower()
        ), "Product card rule must require a brief paragraph/sentence summary"

    def test_product_card_rule_covers_any_product_search(self):
        """Rule must be general — not specific to one product type."""
        lower = ORCHESTRATOR.lower()
        assert (
            "any product search" in lower or "product search" in lower
        ), "Product card rule must cover any product search, not just shoes"


class TestDiscoveryPromptImagesField:
    def test_discovery_prompt_mentions_images(self):
        assert (
            "images" in DISCOVERY.lower()
        ), "DISCOVERY prompt must explicitly list 'images' in the output schema"

    def test_discovery_prompt_says_do_not_omit_images(self):
        assert (
            "omit" in DISCOVERY.lower()
            or "do not" in DISCOVERY.lower()
            or "must" in DISCOVERY.lower()
        ), "DISCOVERY prompt must instruct agent to not omit images"


class TestToneRulesPresentInAllSubagentPrompts:
    @pytest.mark.parametrize(
        "prompt,name",
        [
            (DISCOVERY, "DISCOVERY"),
            (EVALUATION, "EVALUATION"),
            (PURCHASE, "PURCHASE"),
            (TRACKING, "TRACKING"),
        ],
    )
    def test_tone_rules_in_subagent_prompt(self, prompt, name):
        """Every subagent prompt must include the strengthened TONE_RULES."""
        assert "emoji" in prompt.lower(), f"{name} prompt must contain emoji ban from TONE_RULES"
        assert (
            "cancel" in prompt.lower() or "nudge" in prompt.lower()
        ), f"{name} prompt must include post-cancellation or no-nudge rule"

    def test_orchestrator_contains_tone_rules(self):
        assert "emoji" in ORCHESTRATOR.lower()
        assert "cancel" in ORCHESTRATOR.lower() or "nudge" in ORCHESTRATOR.lower()


class TestNudgingBanComprehensive:
    """Verify every specific forbidden phrase is present in the rules."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "would you like",
            "just say the word",
            "ready to buy",
            "let me know how you'd like to proceed",
        ],
    )
    def test_existing_nudge_examples_still_present(self, phrase):
        """Original forbidden nudge phrases must still be listed."""
        assert (
            phrase.lower() in TONE_RULES.lower()
        ), f"Original forbidden phrase '{phrase}' must remain in TONE_RULES"

    def test_post_cancellation_example_present(self):
        """A specific post-cancellation example must be listed as forbidden."""
        assert (
            "try again" in TONE_RULES.lower() or "retry" in TONE_RULES.lower()
        ), "TONE_RULES must give an example of a forbidden retry invitation"
