"""DiscoveryAgent: tool selection + result parsing."""

from __future__ import annotations

import asyncio

from agents.discovery import DiscoveryAgent
from tests.fake_anthropic import (
    FakeAnthropicClient,
    text_response,
    tool_use_response,
)


def test_emits_search_call_with_expected_args(tool_ctx):
    client = FakeAnthropicClient(
        [
            tool_use_response(
                (
                    "search_products",
                    {
                        "query": "running shoes",
                        "merchant_domains": ["demo-shop.myshopify.com"],
                    },
                )
            ),
            text_response('{"products": [], "notes": "found nothing matching"}'),
        ]
    )
    agent = DiscoveryAgent(client)
    result = asyncio.get_event_loop().run_until_complete(agent.run(tool_ctx, "Find running shoes"))
    assert "search_products" in client.dispatched_tool_names()
    inputs = client.tool_inputs("search_products")
    assert inputs[0]["query"] == "running shoes"
    assert result["notes"] == "found nothing matching"


def test_search_passes_through_to_real_tool(tool_ctx):
    """Asserts the agent's tool dispatch actually calls the Phase 2 tool."""
    client = FakeAnthropicClient(
        [
            tool_use_response(
                (
                    "search_products",
                    {"query": "shoes", "merchant_domains": ["demo-shop.myshopify.com"]},
                )
            ),
            text_response('{"products": [], "notes": "ok"}'),
        ]
    )
    agent = DiscoveryAgent(client)
    asyncio.get_event_loop().run_until_complete(agent.run(tool_ctx, "find shoes"))
    # Phase 2 tool wrote an audit entry — proof the real handler ran
    audit_tools = {r["tool"] for r in tool_ctx.db.audit_log.all()}
    assert "search_products" in audit_tools


def test_tool_schema_advertises_search_to_the_model(tool_ctx):
    client = FakeAnthropicClient([text_response('{"products": [], "notes": ""}')])
    agent = DiscoveryAgent(client)
    asyncio.get_event_loop().run_until_complete(agent.run(tool_ctx, "x"))
    # The first call's `tools` param must include all three tools
    tools_advertised = {t["name"] for t in client.calls[0].tools}
    assert tools_advertised == {
        "search_products",
        "get_product_details",
        "check_vendor_allowlist",
    }
