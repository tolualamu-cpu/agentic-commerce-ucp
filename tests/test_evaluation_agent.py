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
    products = [_product("Shoe A", "50"), _product("Shoe B", "200")]
    client = FakeAnthropicClient(
        [
            tool_use_response(("rank_products", {"products": products})),
            text_response(
                '{"ranked": [{"product": {"product_id": "Shoe A", "name": "Shoe A",'
                '"price": "50", "merchant": "m", "merchant_domain": "m.com"},'
                '"score": 0.85, "rank": 1, "risk_flags": []}],'
                '"top_pick_rationale": "lowest price",'
                '"risk_flags": []}'
            ),
        ]
    )
    agent = EvaluationAgent(client)
    result = asyncio.get_event_loop().run_until_complete(agent.run(tool_ctx, "Rank these"))
    assert "rank_products" in client.dispatched_tool_names()
    assert result["top_pick_rationale"] == "lowest price"


def test_advertises_full_tool_set(tool_ctx):
    client = FakeAnthropicClient(
        [text_response('{"ranked": [], "top_pick_rationale": "", "risk_flags": []}')]
    )
    agent = EvaluationAgent(client)
    asyncio.get_event_loop().run_until_complete(agent.run(tool_ctx, "go"))
    advertised = {t["name"] for t in client.calls[0].tools}
    assert advertised == {"rank_products", "fetch_reviews", "compare_prices"}
