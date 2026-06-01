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
