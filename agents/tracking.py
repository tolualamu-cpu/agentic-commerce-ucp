"""TrackingAgent — post-purchase order status, returns, refund queries."""

from __future__ import annotations

from agents.base import BaseAgent, make_tool_spec
from agents.prompts import TRACKING
from tools import shared_tools, tracking_tools


class TrackingAgent(BaseAgent):
    model = "claude-haiku-4-5"
    system_prompt = TRACKING
    max_tokens = 1536

    tool_specs = [
        make_tool_spec(
            name="get_order_status",
            description="Poll the merchant for an order's status. Returns TrackingInfo.",
            handler=tracking_tools.get_order_status,
            required=["order_id", "merchant_domain"],
        ),
        make_tool_spec(
            name="initiate_return",
            description="Submit a return request. ONLY use when user explicitly requested it.",
            handler=tracking_tools.initiate_return,
            overrides={
                "items": {
                    "type": "array",
                    "description": "Items to return [{product_id, quantity}]",
                },
            },
            required=["order_id", "merchant_domain", "items", "reason"],
        ),
        make_tool_spec(
            name="check_refund_status",
            description="Look up a refund by payment_intent_id.",
            handler=tracking_tools.check_refund_status,
            required=["payment_intent_id"],
        ),
        make_tool_spec(
            name="audit_log",
            description="Write an audit entry.",
            handler=shared_tools.audit_log,
            required=["agent", "tool", "action"],
        ),
    ]
