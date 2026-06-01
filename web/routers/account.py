"""Account, orders, mandate, audit — the non-shopping pages.

All handlers honour the dual HTML/JSON format. Sensitive fields are never
emitted: ``payment_method_id`` lives only inside ``PaymentGateway``;
profile views go through ``UserProfile.agent_safe_view()``.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from tools.shared_tools import get_active_mandate_summary
from tools.tracking_tools import get_order_status, initiate_return
from web.session import WebSession, get_or_create_session

router = APIRouter()


def _wants_json(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    if "application/json" in accept:
        if "text/html" in accept and accept.index("text/html") < accept.index("application/json"):
            return False
        return True
    return False


def _render(request: Request, name: str, ctx: dict, status: int = 200) -> Response:
    templates = request.app.state.templates
    if _wants_json(request):
        # Strip the request key + coerce common non-JSON-safe types
        safe = {k: v for k, v in ctx.items() if k != "request"}
        return JSONResponse(safe, status_code=status)
    return templates.TemplateResponse(request, name, context=ctx, status_code=status)


# ─── Profile ─────────────────────────────────────────────────────────────


@router.get("/profile", response_class=HTMLResponse)
async def profile(request: Request, sess: WebSession = Depends(get_or_create_session)):
    """User info — payment_method_id is excluded by ``agent_safe_view``."""
    view = sess.ctx.user.agent_safe_view()
    return _render(request, "profile.html", {"user": view})


# ─── Mandate ─────────────────────────────────────────────────────────────


@router.get("/mandate", response_class=HTMLResponse)
async def mandate(request: Request, sess: WebSession = Depends(get_or_create_session)):
    summary = await get_active_mandate_summary(
        sess.ctx,
        mandate_id=sess.mandate_id,
    )
    return _render(request, "mandate.html", {"mandate": summary})


@router.post("/mandate/revoke")
async def revoke_mandate(request: Request, sess: WebSession = Depends(get_or_create_session)):
    """Revoke the active mandate. Subsequent purchases will be blocked
    by the AP2 layer (existing safety rail)."""
    sess.ctx.ap2.revoke_mandate(sess.mandate_id)
    if _wants_json(request):
        return JSONResponse({"status": "revoked", "mandate_id": sess.mandate_id})
    return RedirectResponse(url="/mandate", status_code=303)


# ─── Orders ──────────────────────────────────────────────────────────────


@router.get("/orders", response_class=HTMLResponse)
async def orders(request: Request, sess: WebSession = Depends(get_or_create_session)):
    from web.routers.chat import _enrich_products_with_images

    rows = sess.ctx.db.orders.all()
    # Most-recent first
    rows = sorted(rows, key=lambda r: r.get("created_at", ""), reverse=True)
    # Enrich the first item of each order so the list can show a thumbnail.
    # Only the first item is enriched to keep the list query fast.
    for row in rows:
        items = row.get("items", [])
        if items:
            first = items[0] if isinstance(items[0], dict) else items[0].dict()
            try:
                enriched = await _enrich_products_with_images(sess.ctx, [first])
                imgs = enriched[0].get("images", []) if enriched else []
                row["_first_item_image"] = imgs[0] if imgs else ""
            except Exception:  # noqa: BLE001 — thumbnail is best-effort
                row["_first_item_image"] = ""
    return _render(request, "orders.html", {"orders": rows})


@router.get("/orders/{order_id}", response_class=HTMLResponse)
async def order_detail(
    request: Request, order_id: str, sess: WebSession = Depends(get_or_create_session)
):
    from storage.db import OrderQ
    from web.routers.chat import _enrich_products_with_images

    row = sess.ctx.db.orders.get(OrderQ.order_id == order_id)
    if not row:
        return _render(
            request,
            "orders.html",
            {"orders": [], "flash": f"Order {order_id} not found"},
            status=404,
        )
    # Enrich order items with product images so the detail page can show
    # thumbnails. items are CartItem dicts or Pydantic models.
    if row.get("items"):
        items_as_dicts = [it if isinstance(it, dict) else it.dict() for it in row["items"]]
        try:
            row = dict(row)  # avoid mutating the db row directly
            row["items"] = await _enrich_products_with_images(sess.ctx, items_as_dicts)
        except Exception:  # noqa: BLE001 — images are best-effort
            row["items"] = items_as_dicts
    # Try to fetch live tracking info
    tracking = None
    try:
        info = await get_order_status(
            sess.ctx,
            order_id=order_id,
            merchant_domain=row["merchant_domain"],
            mandate_id=sess.mandate_id,
        )
        if info is not None:
            tracking = info.model_dump(mode="json") if hasattr(info, "model_dump") else info
    except Exception:  # noqa: BLE001 — tracking is best-effort on this page
        tracking = None
    return _render(request, "order_detail.html", {"order": row, "tracking": tracking})


@router.post("/orders/{order_id}/return")
async def order_return(
    request: Request,
    order_id: str,
    reason: str = Form("user_initiated"),
    sess: WebSession = Depends(get_or_create_session),
):
    from storage.db import OrderQ

    row = sess.ctx.db.orders.get(OrderQ.order_id == order_id)
    if not row:
        if _wants_json(request):
            return JSONResponse({"error": "order_not_found"}, status_code=404)
        return RedirectResponse(url="/orders", status_code=303)
    result = await initiate_return(
        sess.ctx,
        order_id=order_id,
        merchant_domain=row["merchant_domain"],
        items=row.get("items", []),
        reason=reason,
        mandate_id=sess.mandate_id,
    )
    if _wants_json(request):
        return JSONResponse(result)
    return RedirectResponse(url=f"/orders/{order_id}", status_code=303)


# ─── Audit ───────────────────────────────────────────────────────────────


@router.get("/audit", response_class=HTMLResponse)
async def audit(
    request: Request,
    limit: int = 100,
    sess: WebSession = Depends(get_or_create_session),
):
    rows = sess.ctx.db.audit_log.all()
    rows = sorted(rows, key=lambda r: r.get("timestamp", ""), reverse=True)
    rows = rows[: max(1, min(limit, 500))]
    return _render(request, "audit.html", {"entries": rows})
