"""Unit tests for agents.product_grouping.group_into_families.

Covers the standing "one card per product family" rule (plan section 1.11):
  - Family-of-1 pass-through (the default for demo merchants and most
    real-merchant products).
  - Multi-member grouping by color, by non-color dimensions (material,
    roast, capacity), and by multiple dimensions simultaneously.
  - Synthesized option_names/variants correctness, including variant_id
    round-trip to "{member_product_id}:{member_variant_id}".
  - Negative case: titles that merely CONTAIN a vocabulary word as a
    PREFIX (not a stripped suffix) are never incorrectly merged.

This file sorts before test_user_journeys.py — no asyncio is used here at
all, so the CLAUDE.md asyncio-ordering rule does not apply.
"""

from __future__ import annotations

from decimal import Decimal

from agents.product_grouping import group_into_families
from models.product import ProductResult, ProductVariant


def _product(
    product_id: str,
    name: str,
    *,
    price: str = "100.00",
    merchant_domain: str = "kith.com",
    merchant: str = "Kith",
    brand: str | None = "Kith",
    variants: list[ProductVariant] | None = None,
    option_names: list[str] | None = None,
    images: list[str] | None = None,
    in_stock: bool = True,
) -> ProductResult:
    return ProductResult(
        product_id=product_id,
        name=name,
        description=f"{name} description",
        price=Decimal(price),
        merchant=merchant,
        merchant_domain=merchant_domain,
        brand=brand,
        in_stock=in_stock,
        images=images or [],
        variants=variants or [],
        option_names=option_names or [],
        source_protocol="shopify_mcp",
    )


# ─── Family-of-1 pass-through ───────────────────────────────────────────


class TestFamilyOfOnePassThrough:
    def test_single_no_variant_product_passes_through(self):
        product = _product(
            "ath_005", "Athletic Wireless Earbuds", merchant_domain="athletic-co.myshopify.com"
        )
        families = group_into_families([product])

        assert len(families) == 1
        family = families[0]
        assert family.primary.product_id == "ath_005"
        assert family.members == [product]
        assert family.option_names == []
        assert family.variants == []

    def test_single_variant_product_passes_through_unchanged(self):
        variants = [
            ProductVariant(variant_id="ath_001-8", sku="ATH-001-8", options={"Size": "8"}),
            ProductVariant(variant_id="ath_001-9", sku="ATH-001-9", options={"Size": "9"}),
        ]
        product = _product(
            "ath_001",
            "Demo Running Shoes",
            merchant_domain="athletic-co.myshopify.com",
            variants=variants,
            option_names=["Size"],
        )
        families = group_into_families([product])

        assert len(families) == 1
        family = families[0]
        assert family.primary is product
        assert family.members == [product]
        assert family.option_names == ["Size"]
        assert family.variants == variants

    def test_standalone_listing_with_vocabulary_suffix_passes_through(self):
        """A standalone listing whose title happens to end in a recognized
        variant word, but with no siblings, is NOT merged with anything —
        it passes through unchanged (nothing to merge)."""
        product = _product("kith_solo_001", "Kith Mug - Black", merchant_domain="kith.com")
        families = group_into_families([product])

        assert len(families) == 1
        family = families[0]
        assert family.primary.product_id == "kith_solo_001"
        assert family.members == [product]
        # No siblings → option_names/variants are the product's own (empty).
        assert family.option_names == []
        assert family.variants == []

    def test_multiple_unrelated_products_each_family_of_one(self):
        products = [
            _product("ath_001", "Demo Running Shoes", merchant_domain="athletic-co.myshopify.com"),
            _product(
                "aud_001", "Demo Wireless Headphones", merchant_domain="audio-hub.myshopify.com"
            ),
            _product("cof_001", "Ceramic Coffee Mug", merchant_domain="coffee-bar.myshopify.com"),
        ]
        families = group_into_families(products)

        assert len(families) == 3
        for family, product in zip(families, products):
            assert family.primary is product
            assert family.members == [product]


# ─── Multi-member grouping: Color ───────────────────────────────────────


class TestColorFamilyGrouping:
    def _members(self) -> list[ProductResult]:
        black = _product(
            "khmg030009-001",
            "Kith Logo Crewneck - Black",
            variants=[
                ProductVariant(
                    variant_id="45678", sku="KHMG030009-001-S", options={"Size": "S"}, in_stock=True
                ),
                ProductVariant(
                    variant_id="45679", sku="KHMG030009-001-M", options={"Size": "M"}, in_stock=True
                ),
            ],
            option_names=["Size"],
            images=["https://cdn.example.com/black-front.jpg"],
        )
        white = _product(
            "khmg030009-101",
            "Kith Logo Crewneck - White",
            variants=[
                ProductVariant(
                    variant_id="45680", sku="KHMG030009-101-S", options={"Size": "S"}, in_stock=True
                ),
                ProductVariant(
                    variant_id="45681",
                    sku="KHMG030009-101-M",
                    options={"Size": "M"},
                    in_stock=False,
                ),
            ],
            option_names=["Size"],
            images=["https://cdn.example.com/white-front.jpg"],
        )
        return [black, white]

    def test_two_color_listings_collapse_into_one_family(self):
        members = self._members()
        families = group_into_families(members)

        assert len(families) == 1, "Black and White crewneck listings must merge into one family"
        family = families[0]
        assert len(family.members) == 2

    def test_primary_is_lowest_product_id(self):
        families = group_into_families(self._members())
        family = families[0]
        # "khmg030009-001" < "khmg030009-101"
        assert family.primary.product_id == "khmg030009-001"

    def test_primary_name_has_split_dimension_suffix_stripped(self):
        """The merged card represents the FAMILY, not member
        khmg030009-001's specific "- Black" variant — its name must be
        normalized so the card never implies a single color."""
        families = group_into_families(self._members())
        family = families[0]
        assert family.primary.name == "Kith Logo Crewneck"
        # The underlying member's own name is preserved in `members`.
        assert any(m.name == "Kith Logo Crewneck - Black" for m in family.members)

    def test_option_names_include_color_and_size(self):
        families = group_into_families(self._members())
        family = families[0]
        assert "Size" in family.option_names
        assert "Color" in family.option_names

    def test_variants_cross_product_with_color(self):
        families = group_into_families(self._members())
        family = families[0]

        # 2 sizes per color x 2 colors = 4 synthesized variants.
        assert len(family.variants) == 4

        for v in family.variants:
            assert "Color" in v.options
            assert v.options["Color"] in ("Black", "White")
            assert "Size" in v.options

    def test_variant_id_round_trips_to_member_and_member_variant(self):
        families = group_into_families(self._members())
        family = families[0]

        ids = {v.variant_id for v in family.variants}
        assert "khmg030009-001:45678" in ids  # Black, Size S
        assert "khmg030009-001:45679" in ids  # Black, Size M
        assert "khmg030009-101:45680" in ids  # White, Size S
        assert "khmg030009-101:45681" in ids  # White, Size M

    def test_white_size_m_variant_is_out_of_stock(self):
        families = group_into_families(self._members())
        family = families[0]
        white_m = next(v for v in family.variants if v.variant_id == "khmg030009-101:45681")
        assert white_m.options == {"Size": "M", "Color": "White"}
        assert white_m.in_stock is False


# ─── Multi-member grouping: non-color dimensions ────────────────────────


class TestRoastFamilyGrouping:
    def _members(self) -> list[ProductResult]:
        light = _product(
            "cof_101",
            "Single-Origin Coffee - Light Roast",
            price="14.00",
            merchant_domain="coffee-bar.myshopify.com",
            merchant="Coffee Bar",
            brand="Coffee Bar",
            variants=[
                ProductVariant(
                    variant_id="cof_101-12oz", options={"Capacity": "12oz"}, price=Decimal("14.00")
                ),
                ProductVariant(
                    variant_id="cof_101-2lb", options={"Capacity": "2lb"}, price=Decimal("28.00")
                ),
            ],
            option_names=["Capacity"],
        )
        dark = _product(
            "cof_102",
            "Single-Origin Coffee - Dark Roast",
            price="14.00",
            merchant_domain="coffee-bar.myshopify.com",
            merchant="Coffee Bar",
            brand="Coffee Bar",
            variants=[
                ProductVariant(
                    variant_id="cof_102-12oz", options={"Capacity": "12oz"}, price=Decimal("14.00")
                ),
                ProductVariant(
                    variant_id="cof_102-2lb", options={"Capacity": "2lb"}, price=Decimal("28.00")
                ),
            ],
            option_names=["Capacity"],
        )
        return [light, dark]

    def test_roast_listings_collapse_into_one_family(self):
        families = group_into_families(self._members())
        assert len(families) == 1
        assert len(families[0].members) == 2

    def test_option_names_include_roast_and_capacity(self):
        families = group_into_families(self._members())
        family = families[0]
        assert "Roast" in family.option_names
        assert "Capacity" in family.option_names

    def test_variants_cross_product_with_roast(self):
        families = group_into_families(self._members())
        family = families[0]

        assert len(family.variants) == 4
        roast_values = {v.options["Roast"] for v in family.variants}
        assert roast_values == {"Light Roast", "Dark Roast"}

    def test_price_overrides_preserved_per_capacity(self):
        families = group_into_families(self._members())
        family = families[0]

        two_lb_dark = next(v for v in family.variants if v.variant_id == "cof_102:cof_102-2lb")
        assert two_lb_dark.options == {"Capacity": "2lb", "Roast": "Dark Roast"}
        assert two_lb_dark.price == Decimal("28.00")


class TestMaterialFamilyGrouping:
    def _members(self) -> list[ProductResult]:
        suede = _product(
            "kith_chukka_suede",
            "Kith Chukka Boot - Suede",
            price="248.00",
            variants=[
                ProductVariant(variant_id="suede-9", options={"Size": "9"}),
                ProductVariant(variant_id="suede-10", options={"Size": "10"}),
            ],
            option_names=["Size"],
        )
        leather = _product(
            "kith_chukka_leather",
            "Kith Chukka Boot - Leather",
            price="268.00",
            variants=[
                ProductVariant(variant_id="leather-9", options={"Size": "9"}),
                ProductVariant(variant_id="leather-10", options={"Size": "10"}),
            ],
            option_names=["Size"],
        )
        return [suede, leather]

    def test_material_listings_collapse_into_one_family(self):
        families = group_into_families(self._members())
        assert len(families) == 1
        assert len(families[0].members) == 2

    def test_option_names_include_material_and_size(self):
        families = group_into_families(self._members())
        family = families[0]
        assert "Material" in family.option_names
        assert "Size" in family.option_names

    def test_variants_carry_member_specific_price(self):
        families = group_into_families(self._members())
        family = families[0]

        suede_9 = next(v for v in family.variants if v.variant_id == "kith_chukka_suede:suede-9")
        leather_9 = next(
            v for v in family.variants if v.variant_id == "kith_chukka_leather:leather-9"
        )

        assert suede_9.options == {"Size": "9", "Material": "Suede"}
        assert suede_9.price == Decimal("248.00")
        assert leather_9.options == {"Size": "9", "Material": "Leather"}
        assert leather_9.price == Decimal("268.00")


# ─── Multi-dimension families (no own variants — synthetic single variant) ──


class TestMultiDimensionSyntheticVariants:
    def test_color_and_material_split_single_sku_listings(self):
        """Members with NO variants of their own (single-SKU listings) get
        ONE synthesized variant carrying every stripped dimension."""
        products = [
            _product("kith_cap_black_wool", "Kith Cap - Wool - Black", price="48.00"),
            _product("kith_cap_white_wool", "Kith Cap - Wool - White", price="48.00"),
            _product("kith_cap_black_cotton", "Kith Cap - Cotton - Black", price="42.00"),
        ]
        families = group_into_families(products)

        assert len(families) == 1
        family = families[0]
        assert len(family.members) == 3
        assert "Color" in family.option_names
        assert "Material" in family.option_names

        by_id = {v.variant_id: v for v in family.variants}
        assert by_id["kith_cap_black_wool:kith_cap_black_wool"].options == {
            "Material": "Wool",
            "Color": "Black",
        }
        assert by_id["kith_cap_white_wool:kith_cap_white_wool"].options == {
            "Material": "Wool",
            "Color": "White",
        }
        assert by_id["kith_cap_black_cotton:kith_cap_black_cotton"].options == {
            "Material": "Cotton",
            "Color": "Black",
        }
        # Each synthetic variant carries its member's own price.
        assert by_id["kith_cap_black_cotton:kith_cap_black_cotton"].price == Decimal("42.00")


# ─── Negative cases ──────────────────────────────────────────────────────


class TestNegativeCasesNoIncorrectMerging:
    def test_black_cap_and_black_hoodie_are_not_merged(self):
        """'Black' is a vocabulary value, but it appears as a PREFIX here,
        not a stripped trailing suffix — these are different products and
        must remain separate families."""
        products = [
            _product("kith_cap_black", "Black Cap"),
            _product("kith_hoodie_black", "Black Hoodie"),
        ]
        families = group_into_families(products)

        assert len(families) == 2
        ids = {f.primary.product_id for f in families}
        assert ids == {"kith_cap_black", "kith_hoodie_black"}

    def test_dark_roast_mug_and_dark_roast_coffee_are_not_merged(self):
        products = [
            _product(
                "cof_mug_dark_roast", "Dark Roast Mug", merchant_domain="coffee-bar.myshopify.com"
            ),
            _product(
                "cof_beans_dark_roast",
                "Dark Roast Coffee",
                merchant_domain="coffee-bar.myshopify.com",
            ),
        ]
        families = group_into_families(products)

        assert len(families) == 2

    def test_different_vendors_with_same_normalized_title_not_merged(self):
        """Same normalized title + suffix, but different brand/vendor →
        different families (a coincidental title collision across
        merchants/brands is not a real product family)."""
        products = [
            _product("a_jacket_black", "Trail Jacket - Black", brand="Acme"),
            _product("b_jacket_black", "Trail Jacket - Black", brand="Summit"),
        ]
        families = group_into_families(products)

        assert len(families) == 2

    def test_different_merchants_with_same_title_not_merged(self):
        products = [
            _product(
                "k_crew_black", "Logo Crewneck - Black", merchant_domain="kith.com", brand="Kith"
            ),
            _product(
                "a_crew_black",
                "Logo Crewneck - Black",
                merchant_domain="athletic-co.myshopify.com",
                brand="Kith",
            ),
        ]
        families = group_into_families(products)

        assert len(families) == 2

    def test_unrelated_products_with_different_titles_not_merged(self):
        products = [
            _product(
                "ath_003",
                "Performance Running Shorts - Black",
                merchant_domain="athletic-co.myshopify.com",
            ),
            _product(
                "ath_004",
                "Lightweight Training T-Shirt - Black",
                merchant_domain="athletic-co.myshopify.com",
            ),
        ]
        families = group_into_families(products)

        assert len(families) == 2


# ─── Order preservation ──────────────────────────────────────────────────


class TestOrderPreservation:
    def test_families_preserve_first_seen_order(self):
        products = [
            _product("z_solo", "Z Solo Product"),
            _product("a_black", "A Product - Black"),
            _product("a_white", "A Product - White"),
            _product("m_solo", "M Solo Product"),
        ]
        families = group_into_families(products)

        assert [f.primary.product_id for f in families] == [
            "z_solo",
            "a_black",  # "A Product" family — primary is lowest id of its members
            "m_solo",
        ]
