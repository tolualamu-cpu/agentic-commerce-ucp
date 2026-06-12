"""Product schemas — UCP-vocabulary canonical types."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

SourceProtocol = Literal["shopify_mcp", "shopify_storefront", "ucp_rest", "ucp_mcp", "acp", "stub"]


class ProductVariant(BaseModel):
    """A single purchasable SKU within a product (e.g. a specific size/color)."""

    variant_id: str
    sku: str | None = None
    options: dict[str, str] = Field(default_factory=dict)
    price: Decimal | None = None
    in_stock: bool = True
    image: str | None = None


class ProductResult(BaseModel):
    product_id: str
    name: str
    description: str | None = None
    price: Decimal
    currency: str = "USD"

    # UCP RULE — NEVER VIOLATE:
    #   ``merchant``       = the STOREFRONT / SELLER (e.g. "Kith", "Athletic Co")
    #                        — derived from merchant_domain via LIVE_MERCHANTS
    #                        or the demo-catalogue. This is the entity the user
    #                        is buying FROM. "Buy on {{ merchant }}" must
    #                        always read the storefront, never the brand.
    #   ``merchant_domain`` = canonical domain ("kith.com", "athletic-co.myshopify.com")
    #   ``brand``           = the MANUFACTURER / DESIGNER (e.g. "Stone Island",
    #                        "Jordan", "Nike") — sourced from Shopify "vendor"
    #                        or the catalogue brand. Display-only; never used
    #                        for routing or "Buy on" attribution.
    # Conflating these two produces "Buy on Stone Island" links that actually
    # point at kith.com — the regression the user flagged.
    merchant: str
    merchant_domain: str
    brand: str | None = None

    rating: float | None = None
    review_count: int | None = None

    shipping_estimate: str | None = None
    shipping_cost: Decimal | None = None

    in_stock: bool = True
    url: str | None = None
    images: list[str] = Field(default_factory=list)
    attributes: dict[str, str] = Field(default_factory=dict)

    # Variant/SKU structure (e.g. size, color, roast, capacity). Empty list
    # means single-SKU — no variant picker should ever be shown.
    variants: list[ProductVariant] = Field(default_factory=list)
    option_names: list[str] = Field(default_factory=list)

    source_protocol: SourceProtocol = "stub"
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)


class CartItem(BaseModel):
    item_id: str | None = None
    product_id: str
    variant_id: str | None = None
    name: str
    price: Decimal
    quantity: int = 1
    merchant_domain: str | None = None
    selected_options: dict[str, str] = Field(default_factory=dict)
    attributes: dict[str, str] = Field(default_factory=dict)

    @property
    def line_total(self) -> Decimal:
        return self.price * self.quantity


class RankedProduct(BaseModel):
    product: ProductResult
    score: float = Field(ge=0.0, le=1.0)
    rank: int
    rationale: str | None = None
    risk_flags: list[str] = Field(default_factory=list)
