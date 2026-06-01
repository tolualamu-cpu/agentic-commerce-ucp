"""Purchase tools — the full UCP checkout chain wrapped behind agent-facing functions.

Critical safety property: agents pass ``mandate_id`` strings. They never see, log,
or transmit ``payment_method_id``. The boundary crossing happens inside
``get_payment_token`` (which delegates to PaymentGateway, the only authorised reader
of mandate.payment_method_id).
"""

from __future__ import annotations

from decimal import Decimal

from models.mandate import AuthResult
from models.order import BuyerInfo, CheckoutSession, PurchaseOrder
from models.product import CartItem
from storage.db import OrderQ
from tools.context import ToolContext
from tools.shared_tools import audit_log


async def validate_mandate(
    ctx: ToolContext,
    *,
    mandate_id: str,
    amount: Decimal,
    vendor: str | None = None,
    category: str | None = None,
) -> AuthResult:
    """Pre-flight mandate authorisation check. Re-run at purchase time."""
    return ctx.ap2.verify_and_authorize(
        mandate_id=mandate_id,
        amount=Decimal(amount),
        vendor=vendor,
        category=category,
    )


async def create_checkout_session(
    ctx: ToolContext,
    *,
    merchant_domain: str,
    mandate_id: str,
    agent: str = "PurchaseAgent",
) -> CheckoutSession | None:
    """Open a new checkout session at the merchant. Returns None if unsupported."""
    await audit_log(
        ctx,
        agent=agent,
        tool="create_checkout_session",
        action=f"merchant={merchant_domain}",
        mandate_id=mandate_id,
        args={"merchant": merchant_domain},
    )

    client = await ctx.merchant_gateway.resolve_client(merchant_domain)
    if client is None:
        import sys

        print(
            f"[purchase_tools] create_checkout_session: NO CLIENT for " f"{merchant_domain!r}",
            file=sys.stdout,
            flush=True,
        )
        return None
    try:
        session = await client.create_checkout_session()
    except Exception as exc:
        import sys
        import traceback

        print(
            f"[purchase_tools] create_checkout_session FAILED: " f"{type(exc).__name__}: {exc}",
            file=sys.stdout,
            flush=True,
        )
        print(traceback.format_exc(), file=sys.stdout, flush=True)
        raise
    ctx.session.set_open_session(merchant_domain, session.session_id)
    import sys

    print(
        f"[purchase_tools] create_checkout_session ok: "
        f"merchant={merchant_domain} session_id={session.session_id}",
        file=sys.stdout,
        flush=True,
    )
    return session


async def update_checkout_session(
    ctx: ToolContext,
    *,
    session_id: str,
    merchant_domain: str,
    items: list[CartItem],
    buyer: BuyerInfo | None = None,
    discounts: list[str] | None = None,
    mandate_id: str,
    agent: str = "PurchaseAgent",
) -> CheckoutSession | None:
    # Coerce dict buyer (the way LLM tool args arrive) → BuyerInfo
    if isinstance(buyer, dict):
        try:
            buyer = BuyerInfo.model_validate(buyer)
        except Exception as exc:
            import sys

            print(
                f"[purchase_tools] buyer coercion failed: {buyer!r} → "
                f"{type(exc).__name__}: {exc}",
                file=sys.stdout,
                flush=True,
            )
            buyer = None  # fall through to default-address injection

    # Defensive injection: if the agent omitted buyer info but the user has
    # a default shipping address on file, build BuyerInfo automatically.
    # This closes the "order placed with no address" bug — the agent doesn't
    # have to be perfect, the tool layer enforces correctness.
    if buyer is None:
        default_address = ctx.user.default_shipping()
        if default_address is not None:
            buyer = BuyerInfo(
                name=ctx.user.name,
                email=str(ctx.user.email) if ctx.user.email else None,
                shipping_address={
                    "line1": default_address.line1,
                    "line2": default_address.line2 or "",
                    "city": default_address.city,
                    "region": default_address.region,
                    "postal_code": default_address.postal_code,
                    "country": default_address.country,
                },
            )

    await audit_log(
        ctx,
        agent=agent,
        tool="update_checkout_session",
        action=f"session={session_id} items={len(items)} " f"buyer_injected={buyer is not None}",
        mandate_id=mandate_id,
        args={
            "session_id": session_id,
            "merchant": merchant_domain,
            "item_count": len(items),
            "discounts": discounts or [],
            "has_buyer": buyer is not None,
        },
    )

    # Coerce dict-shaped items (the way LLM tool args arrive) into
    # ``CartItem`` Pydantic models so adapters can rely on attribute access.
    # Existing CartItem instances pass through unchanged.
    coerced_items: list[CartItem] = []
    for raw in items:
        if isinstance(raw, CartItem):
            coerced_items.append(raw)
            continue
        if isinstance(raw, dict):
            try:
                payload = dict(raw)
                payload.setdefault("merchant_domain", merchant_domain)
                # Some models emit "id" instead of "product_id"
                if "product_id" not in payload and "id" in payload:
                    payload["product_id"] = payload.pop("id")
                coerced_items.append(CartItem.model_validate(payload))
            except Exception as exc:
                import sys

                print(
                    f"[purchase_tools] item coercion failed: {raw!r} → "
                    f"{type(exc).__name__}: {exc}",
                    file=sys.stdout,
                    flush=True,
                )
                raise
        else:
            raise TypeError(f"unsupported item type: {type(raw).__name__}")

    client = await ctx.merchant_gateway.resolve_client(merchant_domain)
    if client is None:
        import sys

        print(
            f"[purchase_tools] update_checkout_session: NO CLIENT for " f"{merchant_domain!r}",
            file=sys.stdout,
            flush=True,
        )
        return None
    try:
        result = await client.update_checkout_session(
            session_id=session_id,
            items=coerced_items,
            buyer=buyer,
            discounts=discounts,
        )
        import sys

        print(
            f"[purchase_tools] update_checkout_session ok: session={session_id} "
            f"items={len(items)} → {type(result).__name__}",
            file=sys.stdout,
            flush=True,
        )
        return result
    except Exception as exc:
        import sys
        import traceback

        print(
            f"[purchase_tools] update_checkout_session FAILED: " f"{type(exc).__name__}: {exc}",
            file=sys.stdout,
            flush=True,
        )
        print(traceback.format_exc(), file=sys.stdout, flush=True)
        raise


async def get_payment_token(
    ctx: ToolContext,
    *,
    mandate_id: str,
    amount: Decimal,
    currency: str = "USD",
    vendor: str | None = None,
    category: str | None = None,
    merchant_domain: str | None = None,
    agent: str = "PurchaseAgent",
) -> dict:
    """Resolve mandate → opaque Stripe token.

    Returns a dict (not the PaymentToken object) so payment_method_id can't leak
    through accidental serialisation. The token + payment_intent_id are agent-safe.
    """
    await audit_log(
        ctx,
        agent=agent,
        tool="get_payment_token",
        action=f"amount={amount} vendor={vendor}",
        mandate_id=mandate_id,
        args={
            "amount": str(amount),
            "vendor": vendor,
            "category": category,
            "merchant": merchant_domain,
        },
    )

    result = ctx.payment_gateway.get_payment_token(
        mandate_id=mandate_id,
        amount=Decimal(amount),
        currency=currency,
        vendor=vendor,
        category=category,
        merchant_domain=merchant_domain,
    )
    if not result.authorized or result.token is None:
        return {"authorized": False, "reason": result.reason or "unauthorized"}
    return {
        "authorized": True,
        "token": result.token.token,
        "payment_intent_id": result.token.payment_intent_id,
        "amount": str(result.token.amount),
        "currency": result.token.currency,
    }


async def complete_order(
    ctx: ToolContext,
    *,
    session_id: str,
    merchant_domain: str,
    payment_handler_id: str,
    payment_token: str,
    mandate_id: str,
    agent: str = "PurchaseAgent",
) -> PurchaseOrder | None:
    """Final step: merchant captures payment via Stripe (Trust Triangle)."""
    await audit_log(
        ctx,
        agent=agent,
        tool="complete_order",
        action=f"session={session_id} handler={payment_handler_id}",
        mandate_id=mandate_id,
        args={
            "session_id": session_id,
            "merchant": merchant_domain,
            "handler": payment_handler_id,
        },
    )

    client = await ctx.merchant_gateway.resolve_client(merchant_domain)
    if client is None:
        return None
    order = await client.complete_checkout(
        session_id=session_id,
        payment_handler_id=payment_handler_id,
        payment_token=payment_token,
    )
    # Stamp the mandate_id on the order (merchant may not echo it)
    if not order.mandate_id:
        order = order.model_copy(update={"mandate_id": mandate_id})
    ctx.session.clear_open_session(merchant_domain)
    return order


async def save_order(ctx: ToolContext, *, order) -> dict:
    """Persist a confirmed order. Idempotent by order_id.

    Accepts either a ``PurchaseOrder`` Pydantic instance OR a plain
    dict (the shape an LLM emits as a tool argument). When a dict
    arrives we coerce it via ``PurchaseOrder.model_validate`` so the
    adapter contract holds and the row lands in ``db.orders``. Same
    pattern as the ``update_checkout_session`` dict-coercion in Phase 7g.

    Returns ``{"saved": True, "order_id": "..."}`` on success, or
    ``{"saved": False, "reason": "invalid_order_payload", "error":
    "..."}`` if the dict can't be coerced. Older callers that ignored
    the return value still work; callers that look at ``saved`` can
    distinguish persistence failures from silent ones.
    """
    coerced = order
    if isinstance(order, dict):
        try:
            coerced = PurchaseOrder.model_validate(order)
        except Exception as exc:
            import sys

            print(
                f"[purchase_tools] save_order: invalid order dict "
                f"({type(exc).__name__}: {exc}); raw={order!r}",
                file=sys.stdout,
                flush=True,
            )
            return {
                "saved": False,
                "reason": "invalid_order_payload",
                "error": str(exc),
            }

    if not isinstance(coerced, PurchaseOrder):
        import sys

        print(
            f"[purchase_tools] save_order: unsupported order type " f"{type(coerced).__name__}",
            file=sys.stdout,
            flush=True,
        )
        return {
            "saved": False,
            "reason": "invalid_order_payload",
            "error": f"unsupported type: {type(coerced).__name__}",
        }

    ctx.db.orders.upsert(
        coerced.model_dump(mode="json"),
        OrderQ.order_id == coerced.order_id,
    )
    return {"saved": True, "order_id": coerced.order_id}


async def record_mandate_spend(
    ctx: ToolContext,
    *,
    mandate_id: str,
    amount: Decimal,
    order_id: str,
    vendor: str,
    category: str | None = None,
) -> None:
    """Update mandate spend after a confirmed purchase. Feeds future cap checks."""
    ctx.payment_gateway.record_completed_purchase(
        mandate_id=mandate_id,
        amount=Decimal(amount),
        order_id=order_id,
        vendor=vendor,
        category=category,
    )
