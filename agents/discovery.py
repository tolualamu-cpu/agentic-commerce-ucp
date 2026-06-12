"""DiscoveryAgent — finds candidate products via the gateway."""

from __future__ import annotations

from agents.base import BaseAgent, make_tool_spec
from agents.prompts import DISCOVERY
from tools import discovery_tools, evaluation_tools


class DiscoveryAgent(BaseAgent):
    model = "claude-haiku-4-5"
    system_prompt = DISCOVERY
    # 2048 was insufficient for real-merchant catalogues like Kith where a
    # single product's description can be 100+ tokens (Stone Island collab
    # specs, WTAPS material details, etc.). With 4-6 results the JSON output
    # exceeded 2048 and was silently truncated mid-string, producing
    # un-parseable JSON. The orchestrator then saw `{"parse_error":
    # "non_json"}` and never populated last_discovered_products — so the
    # products SSE event never fired and no cards appeared in the chat UI.
    # Bumped to 8192 to comfortably fit ~10 products with verbose descriptions.
    max_tokens = 8192

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
