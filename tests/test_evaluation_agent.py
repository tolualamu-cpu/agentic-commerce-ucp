"""EvaluationAgent: dispatches rank_products with provided products."""

from __future__ import annotations

import asyncio
from decimal import Decimal

from agents.evaluation import EvaluationAgent
from models.product import ProductResult
from tests.fake_anthropic import FakeAnthropicClient, text_response, tool_use_response


def _product(name: str, price: str) -> dict:
    return ProductResult(
        product_id=name,
        name=name,
        price=Decimal(price),
        merchant="m",
        merchant_domain="m.com",
        in_stock=True,
    ).model_dump(mode="json")


def test_invokes_rank_products_and_returns_ranked_list(tool_ctx):
    """rank_products is terminal: the agent returns the deterministic ranking
    after ONE create() call, skipping the redundant reformat turn (Lever 2)."""
    products = [_product("Shoe A", "50"), _product("Shoe B", "200")]
    # Only the tool_use turn is scripted. No second text turn is needed —
    # the terminal fast-path returns rank_products' deterministic output.
    client = FakeAnthropicClient(
        [
            tool_use_response(("rank_products", {"products": products})),
        ]
    )
    agent = EvaluationAgent(client)
    result = asyncio.get_event_loop().run_until_complete(agent.run(tool_ctx, "Rank these"))
    # Fast-path engaged: exactly one model round-trip (no reformat turn). The
    # tool_use assistant turn is appended AFTER this single create() snapshot,
    # so we prove the tool ran via its deterministic output, not call records.
    assert len(client.calls) == 1
    # Deterministic ranking: cheaper Shoe A wins on the price-weighted score.
    assert [r["product"]["product_id"] for r in result["ranked"]] == ["Shoe A", "Shoe B"]
    assert result["ranked"][0]["rank"] == 1
    # Rationale is deterministically templated (internal hint, never shown).
    assert "Shoe A" in result["top_pick_rationale"]
    assert result["risk_flags"] == []


def test_advertises_full_tool_set(tool_ctx):
    client = FakeAnthropicClient(
        [text_response('{"ranked": [], "top_pick_rationale": "", "risk_flags": []}')]
    )
    agent = EvaluationAgent(client)
    asyncio.get_event_loop().run_until_complete(agent.run(tool_ctx, "go"))
    advertised = {t["name"] for t in client.calls[0].tools}
    assert advertised == {"rank_products", "fetch_reviews", "compare_prices"}
