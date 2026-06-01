"""last_discovered_products cache — populated by call_discovery_agent,
exposed via the get_last_discovered_products tool, used to avoid re-searches.
"""

from __future__ import annotations

import asyncio

from agents.orchestrator import OrchestratorAgent
from tests.fake_anthropic import (
    FakeAnthropicClient,
    text_response,
    tool_use_response,
)


def test_last_discovered_populated_after_discovery__single_query(tool_ctx):
    """Single-product discovery → cache populated."""
    client = FakeAnthropicClient(
        [
            tool_use_response(
                (
                    "call_discovery_agent",
                    {
                        "brief": "find running shoes",
                        "merchant_domains": ["demo-shop.myshopify.com"],
                    },
                )
            ),
            # DiscoveryAgent's run — single text response with products JSON
            text_response(
                '{"products": [{"product_id": "shop_001", '
                '"name": "Demo Running Shoes", "price": "129.99",'
                '"merchant": "Demo", '
                '"merchant_domain": "demo-shop.myshopify.com",'
                '"in_stock": true, "source_protocol": "shopify_mcp"}],'
                '"notes": "one match"}'
            ),
            # Orchestrator final reply
            text_response("Found one shoe."),
        ]
    )
    orchestrator = OrchestratorAgent(client, mandate_id=None)
    asyncio.get_event_loop().run_until_complete(orchestrator.run(tool_ctx, "find shoes"))
    cache = tool_ctx.session.last_discovered_products
    assert isinstance(cache, list)
    assert len(cache) == 1
    assert cache[0]["product_id"] == "shop_001"


def test_last_discovered_populated_after_discovery__multi_item_query(tool_ctx):
    """Multi-item discovery → cache contains every returned product."""
    products_json = (
        '{"products": ['
        '{"product_id": "a", "name": "Mug", "price": "14",'
        '"merchant": "X", "merchant_domain": "x.com",'
        '"in_stock": true, "source_protocol": "shopify_mcp"},'
        '{"product_id": "b", "name": "Tumbler", "price": "28",'
        '"merchant": "X", "merchant_domain": "x.com",'
        '"in_stock": true, "source_protocol": "shopify_mcp"},'
        '{"product_id": "c", "name": "Beans", "price": "18",'
        '"merchant": "X", "merchant_domain": "x.com",'
        '"in_stock": true, "source_protocol": "shopify_mcp"}'
        '], "notes": "three matches"}'
    )
    client = FakeAnthropicClient(
        [
            tool_use_response(
                (
                    "call_discovery_agent",
                    {
                        "brief": "mug, tumbler, beans",
                        "merchant_domains": ["demo-shop.myshopify.com"],
                    },
                )
            ),
            text_response(products_json),
            text_response("Found three items."),
        ]
    )
    orchestrator = OrchestratorAgent(client, mandate_id=None)
    asyncio.get_event_loop().run_until_complete(orchestrator.run(tool_ctx, "find all three"))
    cache = tool_ctx.session.last_discovered_products
    assert len(cache) == 3
    assert {p["product_id"] for p in cache} == {"a", "b", "c"}


def test_get_last_discovered_products_tool_returns_cache(tool_ctx):
    """Direct test of the orchestrator's new helper — returns cache without
    re-running discovery."""
    tool_ctx.session.last_discovered_products = [
        {"product_id": "x", "name": "Test Item", "price": "10"},
    ]
    client = FakeAnthropicClient([])  # no scripted responses needed
    orchestrator = OrchestratorAgent(client, mandate_id=None)
    result = asyncio.get_event_loop().run_until_complete(
        orchestrator._get_last_discovered(tool_ctx)
    )
    assert result["count"] == 1
    assert result["source"] == "session_cache"
    assert result["products"][0]["product_id"] == "x"


def test_get_last_discovered_empty_when_no_search_yet(tool_ctx):
    """Calling get_last_discovered with no prior search returns empty list."""
    client = FakeAnthropicClient([])
    orchestrator = OrchestratorAgent(client, mandate_id=None)
    result = asyncio.get_event_loop().run_until_complete(
        orchestrator._get_last_discovered(tool_ctx)
    )
    assert result["count"] == 0
    assert result["products"] == []


def test_get_last_discovered_advertised_to_model(tool_ctx):
    """The new tool is in the orchestrator's tool list, so the model sees it."""
    client = FakeAnthropicClient([text_response("hi")])
    orchestrator = OrchestratorAgent(client, mandate_id=None)
    asyncio.get_event_loop().run_until_complete(orchestrator.run(tool_ctx, "hi"))
    advertised = {t["name"] for t in client.calls[0].tools}
    assert "get_last_discovered_products" in advertised
