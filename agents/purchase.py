"""PurchaseAgent — executes the checkout chain. Never sees payment_method_id."""

from __future__ import annotations

from agents.base import BaseAgent, make_tool_spec
from agents.prompts import PURCHASE
from tools import purchase_tools, shared_tools


class PurchaseAgent(BaseAgent):
    model = "claude-haiku-4-5"
    system_prompt = PURCHASE
    max_tokens = 3072

    tool_specs = [
        make_tool_spec(
            name="validate_mandate",
            description="Pre-flight mandate authorisation. Returns AuthResult with reason on failure.",
            handler=purchase_tools.validate_mandate,
            overrides={"amount": {"type": "string", "description": "Decimal amount as string"}},
            required=["mandate_id", "amount"],
        ),
        make_tool_spec(
            name="create_checkout_session",
            description="Open a new checkout session at the merchant. Returns CheckoutSession or null.",
            handler=purchase_tools.create_checkout_session,
            required=["merchant_domain", "mandate_id"],
        ),
        make_tool_spec(
            name="update_checkout_session",
            description="Add line items and buyer info to an open session. Returns CheckoutSession.",
            handler=purchase_tools.update_checkout_session,
            overrides={
                "items": {
                    "type": "array",
                    "description": "List of CartItem objects (product_id, name, price, quantity)",
                },
                "buyer": {
                    "type": "object",
                    "description": "BuyerInfo dict (name, email, shipping_address)",
                },
                "discounts": {"type": "array", "items": {"type": "string"}},
            },
            required=["session_id", "merchant_domain", "items", "mandate_id"],
        ),
        make_tool_spec(
            name="get_payment_token",
            description="Resolve mandate -> opaque payment token. "
            "Returns {authorized, token, payment_intent_id, amount, currency} "
            "or {authorized:false, reason}. NEVER returns payment_method_id.",
            handler=purchase_tools.get_payment_token,
            overrides={"amount": {"type": "string"}},
            required=["mandate_id", "amount"],
        ),
        make_tool_spec(
            name="complete_order",
            description="Finalise the checkout with payment token. Returns PurchaseOrder.",
            handler=purchase_tools.complete_order,
            required=[
                "session_id",
                "merchant_domain",
                "payment_handler_id",
                "payment_token",
                "mandate_id",
            ],
        ),
        make_tool_spec(
            name="save_order",
            description="Persist a confirmed order to the local DB.",
            handler=purchase_tools.save_order,
            overrides={"order": {"type": "object", "description": "PurchaseOrder dict"}},
            required=["order"],
        ),
        make_tool_spec(
            name="record_mandate_spend",
            description="Record spend against the mandate. Feeds future cap checks.",
            handler=purchase_tools.record_mandate_spend,
            overrides={"amount": {"type": "string"}},
            required=["mandate_id", "amount", "order_id", "vendor"],
        ),
        make_tool_spec(
            name="audit_log",
            description="Write an audit entry.",
            handler=shared_tools.audit_log,
            required=["agent", "tool", "action"],
        ),
    ]
