"""UCP RULE: ``ProductResult.merchant`` is the STOREFRONT, never the brand.

The regression this pins: previously the Shopify adapter set
``merchant=p.get("vendor", merchant_domain)``. For real merchants like Kith
whose catalogue carries multiple brands (Stone Island, Jordan, Nike, …),
``vendor`` is the BRAND, not the storefront. The UI rendered "Buy on Stone
Island" badges on cards that actually link to kith.com — confusing the user
about who they were buying from.

Fix: the adapter now takes a ``merchant_display_name`` arg sourced from the
catalogue config and uses it for ``merchant``. The Shopify vendor is preserved
on the new ``brand`` field for display only.
"""

from __future__ import annotations


from adapters.shopify_mcp import ShopifyMCPAdapter, StubShopifyTransport


def _make_adapter(domain: str, display_name: str, seed: list[dict]):
    return ShopifyMCPAdapter(
        merchant_domain=domain,
        transport=StubShopifyTransport(seed_products=seed),
        merchant_display_name=display_name,
    )


class TestMerchantIsStorefrontNotBrand:
    def test_kith_with_stone_island_vendor(self):
        """The Kith storefront sells Stone Island products. merchant must be
        "Kith" (storefront), brand must be "Stone Island"."""
        adapter = _make_adapter(
            "kith.com",
            "Kith",
            [
                {
                    "id": "abc",
                    "title": "World Cup Polo Sweatshirt",
                    "price": "520",
                    "vendor": "Stone Island",
                    "available": True,
                    "url": "https://kith.com/products/world-cup",
                }
            ],
        )
        import asyncio

        results = asyncio.get_event_loop().run_until_complete(
            adapter.search_products("polo", limit=10)
        )
        assert len(results) == 1
        p = results[0]
        # merchant = STOREFRONT (Kith), not the brand
        assert p.merchant == "Kith", f"merchant must be the storefront 'Kith', got {p.merchant!r}"
        # brand = vendor (Stone Island), preserved separately
        assert p.brand == "Stone Island", (
            f"brand must preserve the vendor 'Stone Island', got {p.brand!r}"
        )
        # merchant_domain = the canonical domain
        assert p.merchant_domain == "kith.com"
        # URL still points to the storefront
        assert p.url == "https://kith.com/products/world-cup"

    def test_kith_with_jordan_vendor(self):
        """Same storefront, different brand — both products carry merchant=Kith."""
        adapter = _make_adapter(
            "kith.com",
            "Kith",
            [
                {
                    "id": "j1",
                    "title": "Air Jordan 1 Retro",
                    "price": "150",
                    "vendor": "Jordan",
                    "available": True,
                    "url": "https://kith.com/products/aj1",
                }
            ],
        )
        import asyncio

        results = asyncio.get_event_loop().run_until_complete(
            adapter.search_products("shoes", limit=10)
        )
        p = results[0]
        assert p.merchant == "Kith"
        assert p.brand == "Jordan"

    def test_demo_merchant_storefront_name_passed_through(self):
        """Demo merchants (Athletic Co, Audio Hub, Coffee Bar) used to rely
        on ``vendor`` happening to equal the storefront name. Now we
        explicitly pass display_name and the result is the same."""
        adapter = _make_adapter(
            "athletic-co.myshopify.com",
            "Athletic Co",
            [
                {
                    "id": "ath_001",
                    "title": "Running Shoes",
                    "price": "130",
                    "vendor": "Athletic Co",
                    "available": True,
                }
            ],
        )
        import asyncio

        results = asyncio.get_event_loop().run_until_complete(
            adapter.search_products("shoes", limit=10)
        )
        p = results[0]
        assert p.merchant == "Athletic Co"
        # brand mirrors vendor for demo merchants (happens to be the same).
        assert p.brand == "Athletic Co"
        assert p.merchant_domain == "athletic-co.myshopify.com"


class TestSessionWiringPassesDisplayName:
    """The session/main wiring layer must pass display_name from the catalogue
    config. If a future change drops this, the merchant_domain fallback would
    re-introduce "Buy on kith.com" badges (ugly) or "Buy on Stone Island"
    badges (wrong) depending on whether vendor is also missing."""

    def test_session_wires_demo_display_names_via_lookup(self):
        from config.catalogue import DEMO_MERCHANT_DISPLAY_NAMES, MERCHANTS

        for domain in MERCHANTS:
            assert domain in DEMO_MERCHANT_DISPLAY_NAMES, (
                f"Demo merchant {domain} missing from DEMO_MERCHANT_DISPLAY_NAMES — "
                f"the session wiring would fall back to the domain name."
            )

    def test_live_merchants_have_display_name(self):
        from config.catalogue import LIVE_MERCHANTS

        for domain, meta in LIVE_MERCHANTS.items():
            assert "display_name" in meta and meta["display_name"], (
                f"Live merchant {domain} missing display_name in LIVE_MERCHANTS — "
                f"this is required so the 'Buy on {{merchant}}' badge reads "
                f"the storefront name, not the brand or domain."
            )
