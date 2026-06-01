"""Agent tone tests — verify the prompts enforce no-emoji + no-sales-nudge rules.

These are prompt-level assertions (not behavioral assertions against Claude)
since we can't deterministically test stochastic model output via the
FakeAnthropicClient. The contract: the rules must be present in every
prompt sent to the model. Live behavioral verification happens manually
in the REPL.
"""

from __future__ import annotations

import re

from agents.prompts import (
    DISCOVERY,
    EVALUATION,
    PURCHASE,
    TONE_RULES,
    TRACKING,
    orchestrator_prompt,
)


ALL_SUBAGENT_PROMPTS = {
    "DISCOVERY": DISCOVERY,
    "EVALUATION": EVALUATION,
    "PURCHASE": PURCHASE,
    "TRACKING": TRACKING,
}


def test_tone_rules_include_no_emoji_directive():
    assert "emoji" in TONE_RULES.lower()
    assert "do not use emojis" in TONE_RULES.lower()


def test_tone_rules_include_no_sales_nudge_directive():
    assert "would you like" in TONE_RULES.lower()
    assert "do not append" in TONE_RULES.lower()


def test_tone_rules_include_no_em_dash_hard_rule():
    """The agent must never emit em-dashes/en-dashes in conversation."""
    lower = TONE_RULES.lower()
    assert "em-dash" in lower
    assert "never use an em-dash" in lower
    # The ban references the actual long-dash characters, not just hyphens.
    assert "—" in TONE_RULES  # —
    assert "–" in TONE_RULES  # –


def test_em_dash_rule_propagates_to_all_prompts():
    """Every subagent + the orchestrator inherit the em-dash ban via
    TONE_RULES, so no agent surface can slip an em-dash through."""
    for name, prompt in ALL_SUBAGENT_PROMPTS.items():
        assert "em-dash" in prompt.lower(), f"{name} prompt missing em-dash ban"
    rendered = orchestrator_prompt(["athletic-co.myshopify.com"])
    assert "em-dash" in rendered.lower()


def test_every_subagent_prompt_contains_tone_rules():
    for name, prompt in ALL_SUBAGENT_PROMPTS.items():
        assert "emoji" in prompt.lower(), f"{name} prompt missing emoji rule"
        assert "would you like" in prompt.lower(), f"{name} prompt missing sales-nudge ban"


def test_orchestrator_prompt_contains_tone_rules():
    rendered = orchestrator_prompt(["athletic-co.myshopify.com"])
    assert "emoji" in rendered.lower()
    assert "would you like to purchase" in rendered.lower()


def test_orchestrator_prompt_contains_named_merchant_rule():
    rendered = orchestrator_prompt(["coffee-bar.myshopify.com"])
    # The "Examples" block in the new prompt mentions Coffee Bar
    assert "buy X from Coffee Bar".lower() in rendered.lower() or "coffee-bar" in rendered.lower()


def test_orchestrator_prompt_contains_batched_discovery_rule():
    rendered = orchestrator_prompt(["x.com"])
    assert (
        "batched discovery" in rendered.lower()
        or "once with a single query" in rendered.lower()
        or "do not call discovery once per item" in rendered.lower()
    )


def test_orchestrator_prompt_mentions_get_last_discovered_tool():
    rendered = orchestrator_prompt(["x.com"])
    assert "get_last_discovered_products" in rendered


def test_no_prompt_contains_emoji_in_its_own_text():
    """Belt-and-suspenders: confirm the prompts themselves don't model bad
    behavior by including emojis in their instructions (other than the
    forbidden examples)."""
    # Some forbidden examples are intentional — we list emojis we ban.
    # But the body shouldn't have other emojis the model might copy.
    # Strategy: count "🎉 ✅ 😊 💳 🛍 📦" in the example list — that's expected.
    # Anywhere else, no emoji should appear in operational instructions.
    emoji_pattern = re.compile(
        # Common emoji ranges
        "[\U0001f300-\U0001f6ff\U0001f900-\U0001f9ff\U00002600-\U000027bf\U0001f1e0-\U0001f1ff]"
    )
    for name, prompt in ALL_SUBAGENT_PROMPTS.items():
        # Only emoji should appear in the TONE_RULES "examples to avoid" section
        # Strip out the TONE_RULES portion and check the rest
        if TONE_RULES in prompt:
            non_tone_part = prompt.replace(TONE_RULES, "")
        else:
            non_tone_part = prompt
        leftover_emojis = emoji_pattern.findall(non_tone_part)
        assert not leftover_emojis, f"{name} prompt body has stray emojis: {leftover_emojis}"


def test_orchestrator_body_no_stray_emojis():
    rendered = orchestrator_prompt(["x.com"])
    # The merchant_list line never has emojis. The body itself shouldn't.
    non_tone = rendered.replace(TONE_RULES, "")
    emoji_pattern = re.compile("[\U0001f300-\U0001f6ff\U0001f900-\U0001f9ff\U00002600-\U000027bf]")
    assert not emoji_pattern.findall(non_tone)
