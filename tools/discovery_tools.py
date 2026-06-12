"""Discovery tools — product search + product detail fetch.

Vendor gating is enforced here: blocked merchants are silently filtered (per
ARCHITECTURE.md "refuse silently — don't tell agent why").
"""

from __future__ import annotations

from models.product import ProductResult
from tools.context import ToolContext
from tools.shared_tools import audit_log


async def search_products(
    ctx: ToolContext,
    *,
    query: str,
    merchant_domains: list[str],
    filters: dict | None = None,
    limit_per_merchant: int = 10,
    mandate_id: str | None = None,
    agent: str = "DiscoveryAgent",
) -> list[ProductResult]:
    """Fan-out search across merchants. Filters out vendor-gated domains silently."""
    gate = ctx.vendor_gate(mandate_id)
    allowed = [d for d in merchant_domains if gate.check(d).allowed]

    await audit_log(
        ctx,
        agent=agent,
        tool="search_products",
        action=f"query='{query}' merchants={allowed}",
        mandate_id=mandate_id,
        args={"query": query, "domains": allowed, "filters": filters},
    )

    if not allowed:
        return []

    return await ctx.merchant_gateway.search(
        query=query,
        domains=allowed,
        filters=filters,
        limit_per_merchant=limit_per_merchant,
    )


async def get_product_details(
    ctx: ToolContext,
    *,
    product_id: str,
    merchant_domain: str,
    mandate_id: str | None = None,
    agent: str = "DiscoveryAgent",
) -> ProductResult | None:
    """Fetch a single product. Returns None if vendor-gated or not found."""
    gate = ctx.vendor_gate(mandate_id)
    if not gate.check(merchant_domain).allowed:
        return None

    await audit_log(
        ctx,
        agent=agent,
        tool="get_product_details",
        action=f"product={product_id} merchant={merchant_domain}",
        mandate_id=mandate_id,
        args={"product_id": product_id, "merchant": merchant_domain},
    )

    client = await ctx.merchant_gateway.resolve_client(merchant_domain)
    if client is None:
        return None
    return await client.get_product(product_id)


async def get_product_variants(
    ctx: ToolContext,
    *,
    product_id: str,
    merchant_domain: str,
    mandate_id: str | None = None,
    agent: str = "DiscoveryAgent",
) -> dict:
    """Return a product's real size/color/etc. options and SKUs.

    Use this to check whether a product has variants before answering
    questions like "what sizes does X come in", or to resolve a
    user-named variant value (e.g. "the black one in size M") to its
    real ``variant_id``. Never invent variant values.
    """
    product = await get_product_details(
        ctx,
        product_id=product_id,
        merchant_domain=merchant_domain,
        mandate_id=mandate_id,
        agent=agent,
    )
    if product is None:
        return {
            "has_variants": False,
            "option_names": [],
            "variants": [],
            "error": "product_not_found",
        }

    return {
        "has_variants": bool(product.variants),
        "option_names": product.option_names,
        "variants": [v.model_dump(mode="json") for v in product.variants],
    }
