"""Regression: _enrich_products_with_images MUST backfill `url` and `brand`
when the discovery agent drops them from its serialized JSON output.

Why this matters:
- ``_chat_product_card.html`` conditional ``{% if product.url and
  product.url.startswith('http') %}`` renders the "Buy on {merchant}" badge.
- If `url` is missing → badge disappears → the user can't click through to
  the real merchant's site.
- Similarly `brand` is a separate field (≠ merchant). Dropping it loses the
  manufacturer info on cards.
- The DISCOVERY prompt requires these fields, but Haiku is unreliable. The
  enrich helper is a safety net.

The bug this pins: Haiku returned products without `url` field, so cards
rendered without the "Buy on Kith" badge — the user complaint.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from web.routers.chat import _enrich_products_with_images


class _FakeAdapter:
    """Adapter stub that returns a canonical product with url + brand set."""

    def __init__(self, *, url: str | None, brand: str | None, images: list[str] | None):
        self._url = url
        self._brand = brand
        self._images = images or []

    async def get_product(self, product_id: str):
        return MagicMock(
            url=self._url,
            brand=self._brand,
            images=self._images,
        )


def _ctx_with_adapter(adapter):
    ctx = MagicMock()
    ctx.merchant_gateway.direct_adapters = {"kith.com": adapter}
    return ctx


class TestEnrichBackfill:
    def test_backfills_missing_url(self):
        """Discovery dropped the url field — enrich must add it back."""
        ctx = _ctx_with_adapter(
            _FakeAdapter(
                url="https://kith.com/products/stone-island-polo",
                brand="Stone Island",
                images=["https://images.unsplash.com/photo-1.jpg"],
            )
        )
        product_without_url = {
            "product_id": "ki001",
            "name": "Stone Island Polo",
            "merchant": "Kith",
            "merchant_domain": "kith.com",
            "images": ["https://images.unsplash.com/photo-1.jpg"],
            # url and brand intentionally missing — Haiku drop
        }
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(_enrich_products_with_images(ctx, [product_without_url]))
        assert result[0]["url"] == "https://kith.com/products/stone-island-polo", (
            "Enrich must backfill the url field so the 'Buy on Kith' badge "
            "renders on the product card."
        )

    def test_backfills_missing_brand(self):
        ctx = _ctx_with_adapter(
            _FakeAdapter(
                url="https://kith.com/products/x",
                brand="Stone Island",
                images=["https://x.jpg"],
            )
        )
        product = {
            "product_id": "ki001",
            "merchant": "Kith",
            "merchant_domain": "kith.com",
            "images": ["https://x.jpg"],
            "url": "https://kith.com/products/x",
            # brand missing
        }
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(_enrich_products_with_images(ctx, [product]))
        assert result[0]["brand"] == "Stone Island"

    def test_preserves_present_fields(self):
        """When url/brand/images are already set on the product dict, enrich
        leaves them alone (no unnecessary adapter calls, no overwrites)."""
        ctx = _ctx_with_adapter(
            _FakeAdapter(
                url="https://wrong.com/should-not-overwrite",
                brand="WrongBrand",
                images=["https://wrong.jpg"],
            )
        )
        product = {
            "product_id": "ki001",
            "merchant": "Kith",
            "merchant_domain": "kith.com",
            "images": ["https://correct.jpg"],
            "url": "https://kith.com/products/correct",
            "brand": "CorrectBrand",
        }
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(_enrich_products_with_images(ctx, [product]))
        assert result[0]["url"] == "https://kith.com/products/correct"
        assert result[0]["brand"] == "CorrectBrand"
        assert result[0]["images"] == ["https://correct.jpg"]

    def test_handles_unknown_merchant_gracefully(self):
        """If no adapter is registered for the merchant_domain, enrich is a
        no-op (does not raise)."""
        ctx = MagicMock()
        ctx.merchant_gateway.direct_adapters = {}  # no adapters
        product = {
            "product_id": "ki001",
            "merchant_domain": "unknown-merchant.com",
        }
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(_enrich_products_with_images(ctx, [product]))
        # Returned unchanged
        assert result == [product]

    def test_adapter_exception_does_not_propagate(self):
        """If adapter.get_product raises, enrich swallows the error and
        returns the product unchanged — never break the chat flow."""
        broken = MagicMock()
        broken.get_product = AsyncMock(side_effect=RuntimeError("simulated"))
        ctx = MagicMock()
        ctx.merchant_gateway.direct_adapters = {"kith.com": broken}
        product = {
            "product_id": "ki001",
            "merchant_domain": "kith.com",
        }
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(_enrich_products_with_images(ctx, [product]))
        assert result[0]["product_id"] == "ki001"

    def test_backfills_all_three_fields_simultaneously(self):
        """The Haiku worst-case: drops images, url, AND brand. Enrich fills
        all three from the adapter in one pass."""
        ctx = _ctx_with_adapter(
            _FakeAdapter(
                url="https://kith.com/products/x",
                brand="Stone Island",
                images=["https://images.unsplash.com/photo.jpg"],
            )
        )
        product = {
            "product_id": "ki001",
            "name": "Some shirt",
            "merchant": "Kith",
            "merchant_domain": "kith.com",
            # images, url, brand ALL missing
        }
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(_enrich_products_with_images(ctx, [product]))
        d = result[0]
        assert d["url"] == "https://kith.com/products/x"
        assert d["brand"] == "Stone Island"
        assert d["images"] == ["https://images.unsplash.com/photo.jpg"]


class TestDiscoveryPromptIncludesUrlAndBrand:
    """The DISCOVERY system prompt must require url AND brand fields in the
    serialized output. Without these, Haiku drops them and the safety net in
    _enrich is the only thing preventing missing Buy-on links."""

    def test_url_field_required_in_prompt(self):
        from agents.prompts import DISCOVERY

        # The CRITICAL block lists required fields. `url` must be there.
        assert "url" in DISCOVERY, (
            "DISCOVERY prompt must require `url` in product output — "
            "otherwise Haiku drops it and 'Buy on {merchant}' badges disappear."
        )

    def test_brand_field_required_in_prompt(self):
        from agents.prompts import DISCOVERY

        assert "brand" in DISCOVERY, (
            "DISCOVERY prompt must require `brand` — separate from merchant "
            "(brand is the manufacturer e.g. Stone Island, merchant is the "
            "storefront e.g. Kith)."
        )

    def test_prompt_explains_merchant_vs_brand(self):
        from agents.prompts import DISCOVERY

        # Must distinguish merchant (storefront) from brand (manufacturer).
        # Otherwise the agent will conflate them and ship "Buy on Stone Island"
        # links pointing to kith.com.
        assert "STOREFRONT" in DISCOVERY or "storefront" in DISCOVERY
        assert "manufacturer" in DISCOVERY or "vendor" in DISCOVERY
