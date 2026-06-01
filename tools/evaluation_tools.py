"""Evaluation tools — ranking, reviews, vendor checks.

Weighted scoring (ARCHITECTURE.md §"Agent Design"):
    preference 30% · price 25% · merchant trust 20% · shipping 15% · reviews 10%

The weights live here, not in the agent, so they can be tuned without prompt edits.
"""

from __future__ import annotations

from decimal import Decimal

from models.product import ProductResult, RankedProduct
from models.user import UserProfile
from tools.context import ToolContext


# Scoring weights — sum to 1.0
WEIGHTS = {
    "preference": 0.30,
    "price": 0.25,
    "trust": 0.20,
    "shipping": 0.15,
    "reviews": 0.10,
}


def _score_preference(p: ProductResult, user: UserProfile) -> float:
    """1.0 if any preferred category matches the product, scaled by attribute hits."""
    if not user.preferred_categories:
        return 0.6  # neutral
    text = " ".join([p.name, p.description or "", " ".join(p.attributes.values())]).lower()
    hits = sum(1 for c in user.preferred_categories if c.lower() in text)
    return min(1.0, 0.5 + 0.25 * hits)


def _score_price(p: ProductResult, all_prices: list[Decimal]) -> float:
    """Lower price ranks higher; linear interpolation over the candidate set."""
    if not all_prices or len(all_prices) < 2:
        return 0.8
    lo, hi = min(all_prices), max(all_prices)
    if hi == lo:
        return 0.8
    return float(1 - (p.price - lo) / (hi - lo))


def _score_trust(p: ProductResult, user: UserProfile) -> float:
    """Allowlisted = 1.0, no list = 0.7 neutral, otherwise 0.4."""
    if user.vendor_allowlist:
        return (
            1.0 if p.merchant_domain.lower() in {v.lower() for v in user.vendor_allowlist} else 0.4
        )
    return 0.7


def _score_shipping(p: ProductResult) -> float:
    """No estimate = 0.5; explicit "free" or low cost ranks higher."""
    if p.shipping_cost is None:
        return 0.5 if not p.shipping_estimate else 0.7
    if p.shipping_cost == 0:
        return 1.0
    if p.shipping_cost < Decimal("10"):
        return 0.8
    if p.shipping_cost < Decimal("25"):
        return 0.6
    return 0.4


def _score_reviews(p: ProductResult) -> float:
    """Combine rating and review_count; saturates at 200+ reviews."""
    if p.rating is None or p.review_count is None:
        return 0.5
    volume = min(1.0, p.review_count / 200)
    return (p.rating / 5.0) * 0.7 + volume * 0.3


async def rank_products(
    ctx: ToolContext,
    *,
    products: list[ProductResult],
    user: UserProfile | None = None,
) -> list[RankedProduct]:
    """Rank products by the weighted composite score. Sorted best → worst."""
    if not products:
        return []
    user = user or ctx.user
    prices = [p.price for p in products if p.in_stock]
    if not prices:
        prices = [p.price for p in products]

    ranked: list[RankedProduct] = []
    for p in products:
        score = (
            WEIGHTS["preference"] * _score_preference(p, user)
            + WEIGHTS["price"] * _score_price(p, prices)
            + WEIGHTS["trust"] * _score_trust(p, user)
            + WEIGHTS["shipping"] * _score_shipping(p)
            + WEIGHTS["reviews"] * _score_reviews(p)
        )
        risk_flags = []
        if not p.in_stock:
            risk_flags.append("OUT_OF_STOCK")
        if p.confidence_score < 0.8:
            risk_flags.append("LOW_CONFIDENCE")

        ranked.append(
            RankedProduct(
                product=p,
                score=round(score, 4),
                rank=0,
                risk_flags=risk_flags,
            )
        )

    ranked.sort(key=lambda r: r.score, reverse=True)
    for i, r in enumerate(ranked, start=1):
        r.rank = i
    return ranked


async def fetch_reviews(
    ctx: ToolContext,
    *,
    product_id: str,
    merchant_domain: str,
) -> dict:
    """Return a review summary for a product.

    MVP behaviour: derive from the ProductResult fields (rating + review_count).
    Phase 2+ can route to a real reviews API or a UCP reviews capability.
    """
    client = await ctx.merchant_gateway.resolve_client(merchant_domain)
    if client is None:
        return {
            "product_id": product_id,
            "rating": None,
            "review_count": 0,
            "summary": "merchant_not_available",
        }
    product = await client.get_product(product_id)
    if product is None:
        return {
            "product_id": product_id,
            "rating": None,
            "review_count": 0,
            "summary": "not_found",
        }
    return {
        "product_id": product_id,
        "merchant_domain": merchant_domain,
        "rating": product.rating,
        "review_count": product.review_count or 0,
        "summary": f"{product.rating}/5 across {product.review_count or 0} reviews"
        if product.rating
        else "no reviews yet",
    }


async def check_vendor_allowlist(
    ctx: ToolContext,
    *,
    merchant_domain: str,
    mandate_id: str | None = None,
) -> bool:
    """True if the user (+ mandate, if any) permits transactions with this merchant."""
    return ctx.vendor_gate(mandate_id).check(merchant_domain).allowed


async def compare_prices(
    ctx: ToolContext,
    *,
    product_name: str,
    merchant_domains: list[str],
    mandate_id: str | None = None,
) -> dict[str, list[dict]]:
    """Searches each merchant for product_name and returns sorted prices."""
    results = await ctx.merchant_gateway.search(
        query=product_name,
        domains=merchant_domains,
        limit_per_merchant=3,
    )
    by_merchant: dict[str, list[dict]] = {}
    for p in results:
        by_merchant.setdefault(p.merchant_domain, []).append(
            {
                "product_id": p.product_id,
                "name": p.name,
                "price": str(p.price),
                "in_stock": p.in_stock,
            }
        )
    for items in by_merchant.values():
        items.sort(key=lambda i: Decimal(i["price"]))
    return by_merchant
