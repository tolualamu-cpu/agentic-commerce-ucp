"""Unit tests for ``config/variant_vocabulary.py`` (Phase 1 bugfix addendum,
2026-06-10).

Covers the "Clear Yellow" gap (Bug 3b secondary gap): multi-word color
values must be present in ``VARIANT_VOCABULARY`` and must take precedence
over a shorter single-word entry that is a suffix of them (e.g. "Clear
Yellow" over "Yellow") when ``agents.product_grouping`` sorts the vocabulary
longest-match-first.

Sorts before ``test_user_journeys.py`` -- no asyncio used, so this is a
non-issue, but keeping the convention documented per CLAUDE.md.
"""

from __future__ import annotations

import pytest

from agents.product_grouping import _strip_dimension_suffixes
from config.variant_vocabulary import VARIANT_VOCABULARY

MULTI_WORD_COLORS = [
    "Clear Yellow",
    "Light Grey",
    "Dark Grey",
    "Light Blue",
    "Dark Green",
]


@pytest.mark.parametrize("value", MULTI_WORD_COLORS)
def test_multi_word_color_present_and_correct(value):
    assert value in VARIANT_VOCABULARY
    dimension, canonical = VARIANT_VOCABULARY[value]
    assert dimension == "Color"
    assert canonical == value


@pytest.mark.parametrize(
    "title, expected_value",
    [
        ("Melissa x Diesel Quantum Flip Flop - Clear Yellow", "Clear Yellow"),
        ("Kith Crewneck - Light Grey", "Light Grey"),
        ("Kith Crewneck - Dark Grey", "Dark Grey"),
        ("Kith Track Jacket - Light Blue", "Light Blue"),
        ("Kith Track Jacket - Dark Green", "Dark Green"),
    ],
)
def test_longest_match_wins_over_shorter_suffix(title, expected_value):
    """ "Clear Yellow" must win over "Yellow", "Light Grey"/"Dark Grey" must
    win over "Grey", etc. -- the longest matching vocabulary entry is
    stripped, not a shorter substring match."""
    stripped_title, dimensions = _strip_dimension_suffixes(title)
    assert dimensions.get("Color") == expected_value
    assert stripped_title != title


def test_plain_yellow_still_matches():
    """Regression: plain "Yellow" (no "Clear" prefix) still resolves to
    ("Color", "Yellow") -- adding "Clear Yellow" must not break this."""
    stripped_title, dimensions = _strip_dimension_suffixes(
        "Melissa x Ganni Flip Flop Slim - Yellow"
    )
    assert dimensions.get("Color") == "Yellow"
    assert stripped_title == "Melissa x Ganni Flip Flop Slim"
