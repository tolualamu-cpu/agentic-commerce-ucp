"""TrackingAgent: status polling + return gating."""

from __future__ import annotations

import asyncio

from agents.tracking import TrackingAgent
from tests.fake_anthropic import FakeAnthropicClient, text_response, tool_use_response


def test_polls_order_status(tool_ctx):
    client = FakeAnthropicClient(
        [
            tool_use_response(
                (
                    "get_order_status",
                    {"order_id": "ord_1", "merchant_domain": "demo-shop.myshopify.com"},
                )
            ),
            text_response('{"tracking": null, "summary": "still pending"}'),
        ]
    )
    agent = TrackingAgent(client)
    result = asyncio.get_event_loop().run_until_complete(agent.run(tool_ctx, "check order ord_1"))
    assert "get_order_status" in client.dispatched_tool_names()
    assert result["summary"] == "still pending"


def test_advertises_tracking_tools(tool_ctx):
    client = FakeAnthropicClient([text_response('{"tracking": null, "summary": ""}')])
    agent = TrackingAgent(client)
    asyncio.get_event_loop().run_until_complete(agent.run(tool_ctx, "x"))
    advertised = {t["name"] for t in client.calls[0].tools}
    assert advertised == {
        "get_order_status",
        "initiate_return",
        "check_refund_status",
        "audit_log",
    }
