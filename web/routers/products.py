"""Product browsing routes — home, search, product detail.

All three handlers honour the dual-format seam:
  - Default: render Jinja HTML
  - Accept: application/json → return JSON
This means a future React frontend can use the JSON shapes without
backend changes.

NB: NO ``from __future__ import annotations`` in this file — FastAPI's
type-introspection needs concrete class references to recognise Request
and Depends parameters.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from tools.discovery_tools import get_product_details, search_products
from web.session import WebSession, get_or_create_session

router = APIRouter()


def _wants_json(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    # Either explicit JSON request or HTMX hint asking for raw data
    if "application/json" in accept:
        # If they also accept HTML and it comes first, prefer HTML
        if "text/html" in accept and accept.index("text/html") < accept.index("application/json"):
            return False
        return True
    return False


def _render(request: Request, name: str, ctx: dict, status: int = 200) -> Response:
    templates = request.app.state.templates
    if _wants_json(request):
        # Strip non-serialisable items; turn Pydantic models into dicts
        safe = _jsonify(ctx)
        return JSONResponse(safe, status_code=status)
    return templates.TemplateResponse(request, name, context=ctx, status_code=status)


def _jsonify(obj):
    """Recursively coerce objects to JSON-safe primitives."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return {k: _jsonify(v) for k, v in obj.items() if k != "request"}
    if isinstance(obj, list):
        return [_jsonify(v) for v in obj]
    if isinstance(obj, Decimal):
        return str(obj)
    return obj


# ─── Home ─────────────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, sess: WebSession = Depends(get_or_create_session)):
    """Featured products across all merchants."""
    domains = list(sess.ctx.merchant_gateway.direct_adapters.keys())
    # Use a broad query that returns interesting items from each merchant
    featured = await search_products(
        sess.ctx,
        query="",
        merchant_domains=domains,
        limit_per_merchant=4,
        mandate_id=sess.mandate_id,
    )
    spent_day, _ = sess.ctx.ap2._compute_spend(sess.mandate_id, datetime.now(timezone.utc))
    mandate = sess.ctx.ap2.get_mandate(sess.mandate_id)
    # Build merchant metadata for the brand row (logo, display name).
    from config.catalogue import LIVE_MERCHANTS

    merchant_meta = {}
    for d in domains:
        live = LIVE_MERCHANTS.get(d)
        if live:
            merchant_meta[d] = {
                "display_name": live.get("display_name", d),
                "logo_url": live.get("logo_url"),
            }
    return _render(
        request,
        "home.html",
        {
            "products": featured,
            "merchants": domains,
            "merchant_meta": merchant_meta,
            "mandate": mandate,
            "spent_today": str(spent_day),
        },
    )


# ─── Search ───────────────────────────────────────────────────────────────


@router.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: str = "",
    merchant: Optional[str] = None,
    sess: WebSession = Depends(get_or_create_session),
):
    """Full-text-ish search across registered merchants.

    Reuses ``tools.discovery_tools.search_products`` so the discovery cache
    gets populated — the agent then knows about these results in chat.
    """
    all_domains = list(sess.ctx.merchant_gateway.direct_adapters.keys())
    domains = [merchant] if merchant in all_domains else all_domains
    results = await search_products(
        sess.ctx,
        query=q,
        merchant_domains=domains,
        limit_per_merchant=8,
        mandate_id=sess.mandate_id,
    )
    # Cache for future agent-side reference
    sess.ctx.session.last_discovered_products = [p.model_dump(mode="json") for p in results]
    # When browsing a single merchant, surface the display name for the
    # page heading (e.g. "Athletic Co" instead of "Browse all merchants").
    merchant_name = None
    if len(domains) == 1 and results:
        merchant_name = results[0].merchant
    elif len(domains) == 1 and merchant:
        # No results but single merchant — clean up the domain slug as fallback
        merchant_name = merchant.split(".")[0].replace("-", " ").title()
    return _render(
        request,
        "search.html",
        {
            "products": results,
            "query": q,
            "merchants_searched": domains,
            "merchant_name": merchant_name,
        },
    )


# ─── Product detail ──────────────────────────────────────────────────────


@router.get("/product/{merchant_domain}/{product_id}", response_class=HTMLResponse)
async def product_detail(
    request: Request,
    merchant_domain: str,
    product_id: str,
    sess: WebSession = Depends(get_or_create_session),
):
    product = await get_product_details(
        sess.ctx,
        product_id=product_id,
        merchant_domain=merchant_domain,
        mandate_id=sess.mandate_id,
    )
    if product is None:
        return _render(
            request,
            "search.html",
            {
                "products": [],
                "query": product_id,
                "merchants_searched": [merchant_domain],
            },
            status=404,
        )
    return _render(request, "product_detail.html", {"product": product})


# ─── Health check / version ───────────────────────────────────────────────


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}
