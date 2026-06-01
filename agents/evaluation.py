"""EvaluationAgent — ranks discovered products for the user."""

from __future__ import annotations

from agents.base import BaseAgent, make_tool_spec
from agents.prompts import EVALUATION
from tools import evaluation_tools


class EvaluationAgent(BaseAgent):
    model = "claude-haiku-4-5"
    system_prompt = EVALUATION
    max_tokens = 2048

    tool_specs = [
        make_tool_spec(
            name="rank_products",
            description="Apply the weighted composite score "
            "(preference 30%, price 25%, trust 20%, shipping 15%, reviews 10%) "
            "and return RankedProduct[] best-first.",
            handler=evaluation_tools.rank_products,
            overrides={
                "products": {
                    "type": "array",
                    "description": "List of ProductResult objects (as dicts)",
                },
                "user": {
                    "type": "object",
                    "description": "Optional user profile override",
                },
            },
            required=["products"],
        ),
        make_tool_spec(
            name="fetch_reviews",
            description="Return a small review summary {rating, review_count, summary} for a product.",
            handler=evaluation_tools.fetch_reviews,
            required=["product_id", "merchant_domain"],
        ),
        make_tool_spec(
            name="compare_prices",
            description="Search a product across merchants and return sorted prices per merchant.",
            handler=evaluation_tools.compare_prices,
            overrides={
                "merchant_domains": {"type": "array", "items": {"type": "string"}},
            },
            required=["product_name", "merchant_domains"],
        ),
    ]
