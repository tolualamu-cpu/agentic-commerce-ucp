"""Cart router — click-to-add / remove / change quantity.

These endpoints maintain a **draft basket** on ``WebSession.click_basket``
that the user assembles via click. The orchestrator's gate flow is still
the only path that actually executes a purchase — clicking "Review
purchase" submits a chat message that drives the orchestrator into
``call_purchase_agent`` with this same basket.

Every click also appends a synthesised conversation note (via
``web.intents.append_click_note``) so the agent is aware of UI actions
on its next turn.

NB: NO ``from __future__ import annotations``.
"""

import asyncio
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from tools.discovery_tools import get_product_details
from web.intents import append_click_note
from web.session import WebSession, get_or_create_session

router = APIRouter()


# ─── Helpers ─────────────────────────────────────────────────────────────


def _items_for(sess: WebSession, merchant_domain: str) -> list:
    return sess.click_basket.setdefault(merchant_domain, [])


def _find(items: list, product_id: str) -> Optional[dict]:
    for it in items:
        if it["product_id"] == product_id:
            return it
    return None


def _recompute_line_total(item: dict) -> None:
    price = Decimal(str(item["price"]))
    qty = int(item["quantity"])
    item["line_total"] = str(price * qty)


def _cart_summary(sess: WebSession) -> dict:
    """Flat summary suitable for rendering the drawer or returning as JSON."""
    lines = []
    total = Decimal("0")
    for domain, items in sess.click_basket.items():
        for it in items:
            lines.append({**it, "merchant_domain": domain})
            total += Decimal(str(it["line_total"]))
    return {
        "lines": lines,
        "subtotal": str(total),
        "currency": "USD",
        "item_count": sum(int(i["quantity"]) for i in lines),
    }


def _push_cart_update(sess: WebSession) -> None:
    """Emit a ``cart_update`` SSE event with the current item count so
    the header badge updates in real time wherever the user is.
    Best-effort — never raises into the request flow.
    """
    summary = _cart_summary(sess)
    try:
        sess.sse_queue.put_nowait(
            {
                "type": "cart_update",
                "data": {"count": summary["item_count"]},
            }
        )
    except asyncio.QueueFull:  # pragma: no cover — unbounded queue
        pass
    except Exception:  # noqa: BLE001 — best-effort badge sync
        pass


def _render_drawer(
    request: Request, sess: WebSession, status: int = 200, flash: Optional[str] = None
) -> Response:
    """Render the cart drawer partial. JSON when Accept: application/json."""
    summary = _cart_summary(sess)
    if "application/json" in (request.headers.get("accept") or "").lower():
        return JSONResponse({**summary, "flash": flash}, status_code=status)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "_cart_drawer.html",
        context={"cart": summary, "flash": flash},
        status_code=status,
    )


# ─── POST /cart/add/{merchant}/{product_id} ──────────────────────────────


@router.post("/cart/add/{merchant_domain}/{product_id}", response_class=HTMLResponse)
async def add_to_cart(
    request: Request,
    merchant_domain: str,
    product_id: str,
    quantity: int = Form(1),
    sess: WebSession = Depends(get_or_create_session),
):
    """Append a product to the draft basket.

    Idempotent at the product level — adding the same product again bumps
    the quantity rather than duplicating the line.
    """
    if quantity < 1:
        return _render_drawer(request, sess, status=400, flash="Quantity must be at least 1.")

    product = await get_product_details(
        sess.ctx,
        product_id=product_id,
        merchant_domain=merchant_domain,
        mandate_id=sess.mandate_id,
    )
    if product is None:
        return _render_drawer(
            request, sess, status=404, flash="That product is no longer available."
        )

    items = _items_for(sess, merchant_domain)
    existing = _find(items, product_id)
    if existing:
        existing["quantity"] = int(existing["quantity"]) + quantity
        _recompute_line_total(existing)
        note = f"increased {product.name} quantity to {existing['quantity']}"
    else:
        item = {
            "product_id": product.product_id,
            "name": product.name,
            "price": str(product.price),
            "currency": product.currency,
            "quantity": quantity,
            "line_total": str(Decimal(str(product.price)) * quantity),
            "image_url": product.images[0] if product.images else "",
        }
        items.append(item)
        note = f"added {product.name} × {quantity}"

    append_click_note(sess.ctx, note)
    action = "updated" if existing else "added"
    try:
        sess.sse_queue.put_nowait(
            {
                "type": "click",
                "data": {
                    "action": action,
                    "note": note,
                    "product_id": product.product_id,
                    "name": product.name,
                    "image_url": product.images[0] if product.images else "",
                    "merchant_domain": merchant_domain,
                    "quantity": quantity,
                },
            }
        )
    except asyncio.QueueFull:  # pragma: no cover — unbounded queue
        pass
    _push_cart_update(sess)

    return _render_drawer(request, sess, flash=f"Added: {product.name}")


# ─── POST /cart/remove/{merchant}/{product_id} ───────────────────────────


@router.post("/cart/remove/{merchant_domain}/{product_id}")
async def remove_from_cart(
    request: Request,
    merchant_domain: str,
    product_id: str,
    sess: WebSession = Depends(get_or_create_session),
):
    items = _items_for(sess, merchant_domain)
    existing = _find(items, product_id)
    if existing is None:
        # Silent no-op — equivalent to clicking remove on a freshly-cleared
        # cart from a stale tab.
        return _render_drawer(request, sess)
    removed_name = existing["name"]
    removed_image = existing.get("image_url", "")
    items.remove(existing)
    append_click_note(sess.ctx, f"removed {removed_name}")
    try:
        sess.sse_queue.put_nowait(
            {
                "type": "click",
                "data": {
                    "action": "removed",
                    "note": f"removed {removed_name}",
                    "product_id": product_id,
                    "name": removed_name,
                    "image_url": removed_image,
                    "merchant_domain": merchant_domain,
                    "quantity": 0,
                },
            }
        )
    except asyncio.QueueFull:  # pragma: no cover
        pass
    _push_cart_update(sess)
    return _render_drawer(request, sess, flash=f"Removed: {removed_name}")


# ─── POST /cart/quantity/{merchant}/{product_id} ─────────────────────────


@router.post("/cart/quantity/{merchant_domain}/{product_id}")
async def change_quantity(
    request: Request,
    merchant_domain: str,
    product_id: str,
    quantity: int = Form(...),
    sess: WebSession = Depends(get_or_create_session),
):
    items = _items_for(sess, merchant_domain)
    existing = _find(items, product_id)
    if existing is None:
        return _render_drawer(request, sess, status=404, flash="Item is no longer in your cart.")
    item_name = existing["name"]
    item_image = existing.get("image_url", "")
    if quantity <= 0:
        items.remove(existing)
        append_click_note(sess.ctx, f"removed {item_name} (quantity → 0)")
        try:
            sess.sse_queue.put_nowait(
                {
                    "type": "click",
                    "data": {
                        "action": "removed",
                        "note": f"removed {item_name}",
                        "product_id": product_id,
                        "name": item_name,
                        "image_url": item_image,
                        "merchant_domain": merchant_domain,
                        "quantity": 0,
                    },
                }
            )
        except asyncio.QueueFull:  # pragma: no cover
            pass
        _push_cart_update(sess)
        return _render_drawer(request, sess, flash=f"Removed: {item_name}")
    existing["quantity"] = quantity
    _recompute_line_total(existing)
    append_click_note(sess.ctx, f"set {item_name} quantity to {quantity}")
    try:
        sess.sse_queue.put_nowait(
            {
                "type": "click",
                "data": {
                    "action": "updated",
                    "note": f"set {item_name} quantity to {quantity}",
                    "product_id": product_id,
                    "name": item_name,
                    "image_url": item_image,
                    "merchant_domain": merchant_domain,
                    "quantity": quantity,
                },
            }
        )
    except asyncio.QueueFull:  # pragma: no cover
        pass
    _push_cart_update(sess)
    return _render_drawer(request, sess, flash=f"Updated: {item_name} → {quantity}")


# ─── POST /cart/clear ────────────────────────────────────────────────────


@router.post("/cart/clear")
async def clear_cart(request: Request, sess: WebSession = Depends(get_or_create_session)):
    sess.click_basket = {}
    append_click_note(sess.ctx, "cleared the basket")
    _push_cart_update(sess)
    return _render_drawer(request, sess, flash="Cart cleared.")


# ─── GET /cart ───────────────────────────────────────────────────────────


@router.get("/cart")
async def view_cart(request: Request, sess: WebSession = Depends(get_or_create_session)):
    """Render the cart.

    Two modes, distinguished by the ``HX-Request`` header that HTMX
    sets on its own requests:
      - HTMX swap (e.g. drawer re-renders after qty change): return
        the bare ``_cart_drawer.html`` partial so the swap target
        gets replaced in-place without breaking the layout.
      - Regular browser navigation (clicking the 🛒 icon in the
        header): return the full ``cart.html`` page extending
        ``base.html`` so the header / nav stays visible.

    JSON behaviour (Accept: application/json) is unchanged — returns
    the cart summary dict in both modes.
    """
    if "application/json" in (request.headers.get("accept") or "").lower():
        return _render_drawer(request, sess)
    if request.headers.get("hx-request", "").lower() == "true":
        return _render_drawer(request, sess)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "cart.html",
        context={"cart": _cart_summary(sess), "flash": None},
    )
