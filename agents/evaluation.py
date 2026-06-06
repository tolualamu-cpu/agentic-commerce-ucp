"""EvaluationAgent — ranks discovered products for the user."""

from __future__ import annotations

from typing import Any

from agents.base import BaseAgent, make_tool_spec
from agents.prompts import EVALUATION
from models.product import RankedProduct
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
            # rank_products output IS the complete ranking — the deterministic
            # scorer leaves no judgment for a second LLM turn. Short-circuit the
            # reformat round-trip; _wrap_terminal assembles the result schema.
            terminal=True,
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

    def _wrap_terminal(self, name: str, result: Any) -> dict:
        """Assemble the EvaluationAgent's final result from a terminal tool.

        When ``rank_products`` fires as the terminal tool, ``result`` is the
        serialised RankedProduct list. Rebuild the schema the orchestrator
        expects ({ranked, top_pick_rationale, risk_flags}) with a deterministic
        rationale — no second Haiku turn. The user-facing comparison prose is
        still written by the orchestrator's Sonnet summary, so explicit-compare
        UX is unchanged. Other tools fall back to the base behaviour.
        """
        if name == "rank_products" and isinstance(result, list):
            ranked = [RankedProduct.model_validate(r) for r in result]
            return {
                "ranked": [r.model_dump(mode="json") for r in ranked],
                "top_pick_rationale": evaluation_tools.template_rationale(ranked),
                "risk_flags": sorted({f for r in ranked for f in r.risk_flags}),
            }
        return super()._wrap_terminal(name, result)
