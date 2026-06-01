"""DiscoveryAgent — finds candidate products via the gateway."""

from __future__ import annotations

from agents.base import BaseAgent, make_tool_spec
from agents.prompts import DISCOVERY
from tools import discovery_tools, evaluation_tools


class DiscoveryAgent(BaseAgent):
    model = "claude-haiku-4-5"
    system_prompt = DISCOVERY
    max_tokens = 2048

    tool_specs = [
        make_tool_spec(
            name="search_products",
            description="Search products across one or more merchant domains. "
            "Returns ProductResult[]. Vendor-gated merchants are silently filtered.",
            handler=discovery_tools.search_products,
            overrides={
                "filters": {
                    "type": "object",
                    "description": "Optional filters like size, color, price_max",
                },
                "merchant_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Domains to fan-out to",
                },
            },
            required=["query", "merchant_domains"],
        ),
        make_tool_spec(
            name="get_product_details",
            description="Fetch full details for a single product by ID at a merchant.",
            handler=discovery_tools.get_product_details,
            required=["product_id", "merchant_domain"],
        ),
        make_tool_spec(
            name="check_vendor_allowlist",
            description="Return true if the user permits transactions with this merchant.",
            handler=evaluation_tools.check_vendor_allowlist,
            required=["merchant_domain"],
        ),
    ]
