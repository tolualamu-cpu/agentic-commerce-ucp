"""Product schemas — UCP-vocabulary canonical types."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

SourceProtocol = Literal["shopify_mcp", "ucp_rest", "ucp_mcp", "acp", "stub"]


class ProductResult(BaseModel):
    product_id: str
    name: str
    description: str | None = None
    price: Decimal
    currency: str = "USD"

    merchant: str
    merchant_domain: str

    rating: float | None = None
    review_count: int | None = None

    shipping_estimate: str | None = None
    shipping_cost: Decimal | None = None

    in_stock: bool = True
    url: str | None = None
    images: list[str] = Field(default_factory=list)
    attributes: dict[str, str] = Field(default_factory=dict)

    source_protocol: SourceProtocol = "stub"
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)


class CartItem(BaseModel):
    item_id: str | None = None
    product_id: str
    name: str
    price: Decimal
    quantity: int = 1
    merchant_domain: str | None = None
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
