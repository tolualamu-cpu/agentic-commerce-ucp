"""Tracking tools — order status, returns, refunds.

Per ARCHITECTURE.md: the TrackingAgent never initiates returns without user
confirmation. That HITL gate lives in the Orchestrator (Phase 3); these tools
execute the action when called.
"""

from __future__ import annotations

from models.order import TrackingInfo
from storage.db import OrderQ
from tools.context import ToolContext
from tools.shared_tools import audit_log


async def get_order_status(
    ctx: ToolContext,
    *,
    order_id: str,
    merchant_domain: str,
    mandate_id: str | None = None,
    agent: str = "TrackingAgent",
) -> TrackingInfo | None:
    """Poll the merchant for order status."""
    await audit_log(
        ctx,
        agent=agent,
        tool="get_order_status",
        action=f"order={order_id}",
        mandate_id=mandate_id,
        args={"order_id": order_id, "merchant": merchant_domain},
    )

    client = await ctx.merchant_gateway.resolve_client(merchant_domain)
    if client is None:
        return None
    return await client.get_order_status(order_id)


async def initiate_return(
    ctx: ToolContext,
    *,
    order_id: str,
    merchant_domain: str,
    items: list[dict],
    reason: str,
    mandate_id: str | None = None,
    agent: str = "TrackingAgent",
) -> dict:
    """Initiate a return. Caller (Orchestrator) must have confirmed with user first.

    Returns a confirmation dict. The actual UCP order-management flow lands in
    Phase 2+ when the capability is wired through UCPRestClient.
    """
    await audit_log(
        ctx,
        agent=agent,
        tool="initiate_return",
        action=f"order={order_id} reason={reason}",
        mandate_id=mandate_id,
        args={"order_id": order_id, "items": items, "reason": reason},
    )

    # Verify the order exists in our records
    row = ctx.db.orders.get(OrderQ.order_id == order_id)
    if not row:
        return {"accepted": False, "reason": "order_not_found"}

    return {
        "accepted": True,
        "order_id": order_id,
        "merchant_domain": merchant_domain,
        "items": items,
        "reason": reason,
        "status": "submitted",
    }


async def check_refund_status(
    ctx: ToolContext,
    *,
    payment_intent_id: str,
    mandate_id: str | None = None,
    agent: str = "TrackingAgent",
) -> dict:
    """Look up a refund by Stripe payment_intent_id.

    MVP behaviour: returns the local order's refund metadata. Phase 2+ wires
    this to the live Stripe API via StripeAdapter.
    """
    await audit_log(
        ctx,
        agent=agent,
        tool="check_refund_status",
        action=f"intent={payment_intent_id}",
        mandate_id=mandate_id,
        args={"payment_intent_id": payment_intent_id},
    )

    row = ctx.db.orders.get(OrderQ.payment_intent_id == payment_intent_id)
    if not row:
        return {"payment_intent_id": payment_intent_id, "status": "unknown"}
    return {
        "payment_intent_id": payment_intent_id,
        "order_id": row["order_id"],
        "status": row.get("status", "pending"),
    }
