"""Cross-cutting agent query-type matrix (Phase 1, plan section "Testing
Requirements (cross-cutting)" item 3 + standing "one card per product
family" rule).

Covers, parametrized across at least one representative product per merchant
in ``config.catalogue.MERCHANTS`` AND the live merchant ``kith.com``
(``LIVE_MERCHANTS``, mocked HTTP via ``tests/fixtures/kith_products.py``):

  1. No-variant product -> single card, direct ``add_to_cart`` (no
     ``variant_required``).
  2. Variant product, NO specific option value mentioned -> single card per
     family; ``add_to_cart`` returns ``variant_required`` with ALL of that
     family's dimensions (not just color).
  3. A query naming a SPECIFIC variant value -- one example per dimension
     type present in the fixtures (Size, Color, Width, Material, Capacity)
     -- resolves directly to a real ``variant_id`` via
     ``get_product_variants``, then ``add_to_cart`` succeeds in one call.
  4. Cross-product comparison (>1 family) -> one card per family, never
     multiple cards for dimension-siblings (color, material, capacity, etc.)
     of the same family.
  5. Explicit within-product variant comparison (incl. non-color dimensions:
     Material, Capacity, Width) -> ``get_product_variants`` for that family
     surfaces the requested dimension's real values.
  6. The merged family card's name never carries a stripped variant-vocabulary
     suffix (color/material/capacity/etc.) -- i.e. discovery never implies a
     single colorway/material/size is "the" product.

Sorts before ``test_user_journeys.py`` ("discovery_query_variants" < "user")
-> uses ``asyncio.get_event_loop().run_until_complete()``.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from adapters.shopify_mcp import ShopifyMCPAdapter, StubShopifyTransport
from adapters.stripe import StripeAdapter
from agents.orchestrator import OrchestratorAgent
from cli.confirmation import AutoConfirmProvider
from config.catalogue import LIVE_MERCHANTS, MERCHANTS
from config.variant_vocabulary import VARIANT_VOCABULARY
from gateway.merchant_gateway import MerchantGateway
from gateway.payment_gateway import PaymentGateway
from guardrails.confidence import ConfidenceChecker
from guardrails.spending import SpendingLimiter
from models.product import ProductResult
from models.user import UserProfile
from storage.state import SessionState
from tests.fake_anthropic import FakeAnthropicClient
from tests.fixtures.kith_products import (
    KITH_990V6_GREY,
    KITH_990V6_NAVY,
    KITH_CAMP_CAP,
    KITH_CREWNECK_BLACK,
    KITH_CREWNECK_WHITE,
    KITH_MOCKNECK_COTTON,
    KITH_MOCKNECK_WOOL,
    KITH_POCKET_TEE,
    KITH_TREATS_MUG_12OZ,
    KITH_TREATS_MUG_16OZ,
    make_kith_adapter,
)
from tools import discovery_tools
from tools.context import ToolContext


def _orch() -> OrchestratorAgent:
    return OrchestratorAgent(
        client=FakeAnthropicClient([]),
        confirmation=AutoConfirmProvider(),
        mandate_id="m_test",
    )


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def ctx(tmp_db, ap2, tmp_path):
    """ToolContext with all demo merchants registered, plus ``kith.com`` via
    a mocked ``LiveShopifyTransport`` (no real network)."""
    import httpx
    from ucp.discovery import UCPProfileDiscovery
    from ucp.signing import RequestSigner, generate_keypair

    stub_path = tmp_path / "profiles.json"
    stub_path.write_text(json.dumps({"profiles": {}}))
    offline_http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    discovery = UCPProfileDiscovery(tmp_db, http_client=offline_http, stub_path=stub_path)

    private_pem, _, _ = generate_keypair("k1")
    signer = RequestSigner(private_pem, key_id="k1")

    direct_adapters = {}
    for domain, seed in MERCHANTS.items():
        direct_adapters[domain] = ShopifyMCPAdapter(
            domain, StubShopifyTransport(seed_products=seed)
        )
    direct_adapters["kith.com"] = make_kith_adapter()

    gateway = MerchantGateway(discovery=discovery, signer=signer, direct_adapters=direct_adapters)

    stripe = StripeAdapter(api_key=None)
    user = UserProfile(user_id="user_1", name="Alex", payment_method_id="pm_test_card")
    session = SessionState(user_id=user.user_id)

    return ToolContext(
        db=tmp_db,
        ap2=ap2,
        merchant_gateway=gateway,
        payment_gateway=PaymentGateway(ap2, stripe),
        spending_limiter=SpendingLimiter(tmp_db),
        confidence_checker=ConfidenceChecker(threshold=0.8),
        user=user,
        session=session,
    )


async def _get_product(ctx, *, product_id: str, merchant_domain: str) -> ProductResult:
    product = await discovery_tools.get_product_details(
        ctx, product_id=product_id, merchant_domain=merchant_domain
    )
    assert product is not None
    return product


# ── 1. No-variant product -> single card, direct add ────────────────────


DEMO_NO_VARIANT = {
    "athletic-co.myshopify.com": "ath_005",
    "audio-hub.myshopify.com": "aud_003",
    "coffee-bar.myshopify.com": "cof_001",
}


class TestNoVariantQuery:
    @pytest.mark.parametrize("domain,product_id", sorted(DEMO_NO_VARIANT.items()))
    def test_demo_no_variant_single_card_direct_add(self, ctx, domain, product_id):
        product = _run(_get_product(ctx, product_id=product_id, merchant_domain=domain))
        merged = _run(_orch()._group_discovered_products(ctx, [product.model_dump(mode="json")]))
        assert len(merged) == 1
        assert merged[0]["variants"] == []

        result = _run(
            _orch()._add_to_cart(ctx, product_id=product_id, merchant_domain=domain, quantity=1)
        )
        assert result["added"] is True
        assert "error" not in result

    def test_kith_no_variant_single_card_direct_add(self, ctx):
        product_id = str(KITH_CAMP_CAP["id"])
        domain = "kith.com"
        product = _run(_get_product(ctx, product_id=product_id, merchant_domain=domain))
        merged = _run(_orch()._group_discovered_products(ctx, [product.model_dump(mode="json")]))
        assert len(merged) == 1
        assert merged[0]["variants"] == []

        result = _run(
            _orch()._add_to_cart(ctx, product_id=product_id, merchant_domain=domain, quantity=1)
        )
        assert result["added"] is True
        assert "error" not in result


# ── 2. Variant product, no value mentioned -> variant_required, all dims ─


DEMO_VARIANT_PRODUCTS = {
    "athletic-co.myshopify.com": [
        ("ath_001", ["Size"]),
        ("ath_003", ["Size", "Color"]),
        ("ath_007", ["Size", "Width"]),
    ],
    "audio-hub.myshopify.com": [("aud_001", ["Color"])],
    "coffee-bar.myshopify.com": [("cof_002", ["Size"]), ("cof_003", ["Size"])],
}


class TestVariantProductNoValueMentioned:
    @pytest.mark.parametrize(
        "domain,product_id,expected_dims",
        [
            (domain, pid, dims)
            for domain, items in DEMO_VARIANT_PRODUCTS.items()
            for pid, dims in items
        ],
    )
    def test_demo_variant_required_covers_all_dimensions(
        self, ctx, domain, product_id, expected_dims
    ):
        result = _run(
            _orch()._add_to_cart(ctx, product_id=product_id, merchant_domain=domain, quantity=1)
        )
        assert result["added"] is False
        assert result["error"] == "variant_required"
        assert set(result["option_names"]) == set(expected_dims)
        assert result["variants"]
        for v in result["variants"]:
            assert set(v["options"].keys()) == set(expected_dims)

    def test_kith_family_variant_required_covers_size_and_color(self, ctx):
        """990v6 Grey/Navy: discovery groups them into ONE family whose
        synthesized option_names span Size (each member's own dimension)
        AND Color (the dimension the listings were split on)."""
        domain = "kith.com"
        grey = _run(
            _get_product(ctx, product_id=str(KITH_990V6_GREY["id"]), merchant_domain=domain)
        )
        navy = _run(
            _get_product(ctx, product_id=str(KITH_990V6_NAVY["id"]), merchant_domain=domain)
        )

        orch = _orch()
        merged = _run(
            orch._group_discovered_products(
                ctx, [grey.model_dump(mode="json"), navy.model_dump(mode="json")]
            )
        )
        assert len(merged) == 1, "990v6 Grey and Navy must collapse into one family card"
        primary_id = merged[0]["product_id"]
        assert set(merged[0]["option_names"]) == {"Size", "Color"}

        result = _run(
            orch._add_to_cart(ctx, product_id=primary_id, merchant_domain=domain, quantity=1)
        )
        assert result["added"] is False
        assert result["error"] == "variant_required"
        assert set(result["option_names"]) == {"Size", "Color"}
        assert {v["options"]["Color"] for v in result["variants"]} == {"Grey", "Navy"}


# ── 3. Named variant value resolves directly (one example per dimension) ─


class TestNamedVariantValueResolvesDirectly:
    def test_size_only_demo(self, ctx):
        """ath_001: 'size 9' resolves directly (Size dimension)."""
        domain = "athletic-co.myshopify.com"
        variants = _run(
            discovery_tools.get_product_variants(ctx, product_id="ath_001", merchant_domain=domain)
        )["variants"]
        target = next(v for v in variants if v["options"]["Size"] == "9")

        result = _run(
            _orch()._add_to_cart(
                ctx,
                product_id="ath_001",
                merchant_domain=domain,
                quantity=1,
                variant_id=target["variant_id"],
            )
        )
        assert result["added"] is True

    def test_color_dimension_demo(self, ctx):
        """aud_001: 'the black one' resolves directly (Color dimension)."""
        domain = "audio-hub.myshopify.com"
        variants = _run(
            discovery_tools.get_product_variants(ctx, product_id="aud_001", merchant_domain=domain)
        )["variants"]
        target = next(v for v in variants if v["options"]["Color"] == "Black")

        result = _run(
            _orch()._add_to_cart(
                ctx,
                product_id="aud_001",
                merchant_domain=domain,
                quantity=1,
                variant_id=target["variant_id"],
            )
        )
        assert result["added"] is True

    def test_size_and_color_combo_demo(self, ctx):
        """ath_003: 'size M, black' resolves a single 2D variant directly."""
        domain = "athletic-co.myshopify.com"
        variants = _run(
            discovery_tools.get_product_variants(ctx, product_id="ath_003", merchant_domain=domain)
        )["variants"]
        target = next(
            v for v in variants if v["options"]["Size"] == "M" and v["options"]["Color"] == "Black"
        )

        result = _run(
            _orch()._add_to_cart(
                ctx,
                product_id="ath_003",
                merchant_domain=domain,
                quantity=1,
                variant_id=target["variant_id"],
            )
        )
        assert result["added"] is True

    def test_width_dimension_demo(self, ctx):
        """ath_007: 'size 9, wide' resolves directly (Width dimension —
        non-color)."""
        domain = "athletic-co.myshopify.com"
        variants = _run(
            discovery_tools.get_product_variants(ctx, product_id="ath_007", merchant_domain=domain)
        )["variants"]
        target = next(
            v for v in variants if v["options"]["Size"] == "9" and v["options"]["Width"] == "Wide"
        )

        result = _run(
            _orch()._add_to_cart(
                ctx,
                product_id="ath_007",
                merchant_domain=domain,
                quantity=1,
                variant_id=target["variant_id"],
            )
        )
        assert result["added"] is True

    def test_capacity_dimension_demo(self, ctx):
        """cof_002: 'the 20oz' resolves directly (Capacity-analog Size
        dimension) and uses the price-override (32.00)."""
        from decimal import Decimal

        domain = "coffee-bar.myshopify.com"
        variants = _run(
            discovery_tools.get_product_variants(ctx, product_id="cof_002", merchant_domain=domain)
        )["variants"]
        target = next(v for v in variants if v["options"]["Size"] == "20oz")

        result = _run(
            _orch()._add_to_cart(
                ctx,
                product_id="cof_002",
                merchant_domain=domain,
                quantity=1,
                variant_id=target["variant_id"],
            )
        )
        assert result["added"] is True
        line = ctx.session.click_basket[domain][0]
        assert Decimal(str(line["price"])) == Decimal("32.00")

    def test_kith_named_color_and_size_resolves_to_sibling_listing(self, ctx):
        """990v6 family: 'the navy one in size 9' resolves to a variant that
        physically lives on the SIBLING listing (400002), via the family
        cache -- in one add_to_cart call."""
        domain = "kith.com"
        grey = _run(
            _get_product(ctx, product_id=str(KITH_990V6_GREY["id"]), merchant_domain=domain)
        )
        navy = _run(
            _get_product(ctx, product_id=str(KITH_990V6_NAVY["id"]), merchant_domain=domain)
        )

        orch = _orch()
        merged = _run(
            orch._group_discovered_products(
                ctx, [grey.model_dump(mode="json"), navy.model_dump(mode="json")]
            )
        )
        primary_id = merged[0]["product_id"]

        family_variants = _run(
            orch._get_product_variants(ctx, product_id=primary_id, merchant_domain=domain)
        )["variants"]
        target = next(
            v
            for v in family_variants
            if v["options"]["Color"] == "Navy" and v["options"]["Size"] == "9"
        )
        assert target["variant_id"].startswith(f"{KITH_990V6_NAVY['id']}:")

        result = _run(
            orch._add_to_cart(
                ctx,
                product_id=primary_id,
                merchant_domain=domain,
                quantity=1,
                variant_id=target["variant_id"],
            )
        )
        assert result["added"] is True

    def test_kith_named_material_resolves_to_sibling_listing(self, ctx):
        """Mock Neck Sweater family (Material: Wool/Cotton): 'the wool one
        in size M' resolves directly via the family cache."""
        domain = "kith.com"
        wool = _run(
            _get_product(ctx, product_id=str(KITH_MOCKNECK_WOOL["id"]), merchant_domain=domain)
        )
        cotton = _run(
            _get_product(ctx, product_id=str(KITH_MOCKNECK_COTTON["id"]), merchant_domain=domain)
        )

        orch = _orch()
        merged = _run(
            orch._group_discovered_products(
                ctx, [wool.model_dump(mode="json"), cotton.model_dump(mode="json")]
            )
        )
        assert set(merged[0]["option_names"]) == {"Size", "Material"}
        primary_id = merged[0]["product_id"]

        family_variants = _run(
            orch._get_product_variants(ctx, product_id=primary_id, merchant_domain=domain)
        )["variants"]
        target = next(
            v
            for v in family_variants
            if v["options"]["Material"] == "Wool" and v["options"]["Size"] == "M"
        )

        result = _run(
            orch._add_to_cart(
                ctx,
                product_id=primary_id,
                merchant_domain=domain,
                quantity=1,
                variant_id=target["variant_id"],
            )
        )
        assert result["added"] is True

    def test_kith_named_capacity_resolves_to_sibling_listing(self, ctx):
        """Treats Mug family (Capacity: 12oz/16oz, both single-SKU listings):
        'the 16oz mug' resolves directly via the family cache."""
        from decimal import Decimal

        domain = "kith.com"
        mug12 = _run(
            _get_product(ctx, product_id=str(KITH_TREATS_MUG_12OZ["id"]), merchant_domain=domain)
        )
        mug16 = _run(
            _get_product(ctx, product_id=str(KITH_TREATS_MUG_16OZ["id"]), merchant_domain=domain)
        )

        orch = _orch()
        merged = _run(
            orch._group_discovered_products(
                ctx, [mug12.model_dump(mode="json"), mug16.model_dump(mode="json")]
            )
        )
        assert merged[0]["option_names"] == ["Capacity"]
        primary_id = merged[0]["product_id"]

        family_variants = _run(
            orch._get_product_variants(ctx, product_id=primary_id, merchant_domain=domain)
        )["variants"]
        target = next(v for v in family_variants if v["options"]["Capacity"] == "16oz")
        assert target["variant_id"].startswith(f"{KITH_TREATS_MUG_16OZ['id']}:")

        result = _run(
            orch._add_to_cart(
                ctx,
                product_id=primary_id,
                merchant_domain=domain,
                quantity=1,
                variant_id=target["variant_id"],
            )
        )
        assert result["added"] is True
        line = ctx.session.click_basket[domain][0]
        assert Decimal(str(line["price"])) == Decimal("22.00")


# ── 4. Cross-product comparison -> one card per family ───────────────────


class TestCrossProductComparisonOneCardPerFamily:
    def test_demo_running_shoe_comparison_one_card_each(self, ctx):
        """Demo catalogue titles never collide with the variant vocabulary,
        so comparing several Athletic Co products yields one card per
        product (no accidental merging)."""
        ids = ["ath_001", "ath_003", "ath_006", "ath_007"]
        domain = "athletic-co.myshopify.com"
        products = [_run(_get_product(ctx, product_id=pid, merchant_domain=domain)) for pid in ids]

        merged = _run(
            _orch()._group_discovered_products(ctx, [p.model_dump(mode="json") for p in products])
        )
        assert len(merged) == len(ids)
        assert {m["product_id"] for m in merged} == set(ids)

    def test_kith_jacket_comparison_one_card_per_family(self, ctx):
        """Comparing 7 Kith listings (incl. two color-split families and a
        material-split family) collapses to 4 cards: 990v6, Crewneck,
        Mock Neck Sweater, Pocket Tee -- never one card per colorway."""
        domain = "kith.com"
        ids = [
            str(KITH_990V6_GREY["id"]),
            str(KITH_990V6_NAVY["id"]),
            str(KITH_CREWNECK_BLACK["id"]),
            str(KITH_CREWNECK_WHITE["id"]),
            str(KITH_MOCKNECK_WOOL["id"]),
            str(KITH_MOCKNECK_COTTON["id"]),
            str(KITH_POCKET_TEE["id"]),
        ]
        products = [_run(_get_product(ctx, product_id=pid, merchant_domain=domain)) for pid in ids]

        merged = _run(
            _orch()._group_discovered_products(ctx, [p.model_dump(mode="json") for p in products])
        )
        assert len(merged) == 4
        names = {m["name"] for m in merged}
        assert names == {
            "Kith x New Balance 990v6",
            "Kith Logo Crewneck",
            "Kith Mock Neck Sweater",
            "Kith Pocket Tee",
        }


# ── 5. Explicit within-product variant comparison (incl. non-color) ──────


class TestExplicitVariantComparison:
    def test_demo_color_dimension_comparison(self, ctx):
        """ath_003: 'compare the black and navy shorts' -> get_product_variants
        surfaces real Color values."""
        result = _run(
            discovery_tools.get_product_variants(
                ctx, product_id="ath_003", merchant_domain="athletic-co.myshopify.com"
            )
        )
        colors = {v["options"]["Color"] for v in result["variants"]}
        assert colors == {"Black", "Navy"}

    def test_demo_capacity_dimension_comparison(self, ctx):
        """cof_002: 'compare the 16oz and 20oz tumbler' -> get_product_variants
        surfaces real Capacity-analog Size values + price divergence."""
        result = _run(
            discovery_tools.get_product_variants(
                ctx, product_id="cof_002", merchant_domain="coffee-bar.myshopify.com"
            )
        )
        sizes = {v["options"]["Size"] for v in result["variants"]}
        assert sizes == {"16oz", "20oz"}

    def test_demo_width_dimension_comparison(self, ctx):
        """ath_007: 'compare the standard and wide width' -> get_product_variants
        surfaces real Width values (non-color dimension)."""
        result = _run(
            discovery_tools.get_product_variants(
                ctx, product_id="ath_007", merchant_domain="athletic-co.myshopify.com"
            )
        )
        widths = {v["options"]["Width"] for v in result["variants"]}
        assert widths == {"Standard", "Wide"}

    def test_kith_color_dimension_comparison(self, ctx):
        """'compare the black and white crewneck' -> get_product_variants for
        the merged family surfaces real Color values from BOTH sibling
        listings."""
        domain = "kith.com"
        black = _run(
            _get_product(ctx, product_id=str(KITH_CREWNECK_BLACK["id"]), merchant_domain=domain)
        )
        white = _run(
            _get_product(ctx, product_id=str(KITH_CREWNECK_WHITE["id"]), merchant_domain=domain)
        )

        orch = _orch()
        merged = _run(
            orch._group_discovered_products(
                ctx, [black.model_dump(mode="json"), white.model_dump(mode="json")]
            )
        )
        primary_id = merged[0]["product_id"]
        result = _run(
            orch._get_product_variants(ctx, product_id=primary_id, merchant_domain=domain)
        )
        colors = {v["options"]["Color"] for v in result["variants"]}
        assert colors == {"Black", "White"}

    def test_kith_material_dimension_comparison(self, ctx):
        """'compare the leather and canvas / wool and cotton versions' ->
        get_product_variants for the merged Mock Neck Sweater family
        surfaces real Material values (non-color dimension)."""
        domain = "kith.com"
        wool = _run(
            _get_product(ctx, product_id=str(KITH_MOCKNECK_WOOL["id"]), merchant_domain=domain)
        )
        cotton = _run(
            _get_product(ctx, product_id=str(KITH_MOCKNECK_COTTON["id"]), merchant_domain=domain)
        )

        orch = _orch()
        merged = _run(
            orch._group_discovered_products(
                ctx, [wool.model_dump(mode="json"), cotton.model_dump(mode="json")]
            )
        )
        primary_id = merged[0]["product_id"]
        result = _run(
            orch._get_product_variants(ctx, product_id=primary_id, merchant_domain=domain)
        )
        materials = {v["options"]["Material"] for v in result["variants"]}
        assert materials == {"Wool", "Cotton"}

    def test_kith_capacity_dimension_comparison(self, ctx):
        """'compare the 12oz and 16oz mug' -> get_product_variants for the
        merged Treats Mug family surfaces real Capacity values."""
        domain = "kith.com"
        mug12 = _run(
            _get_product(ctx, product_id=str(KITH_TREATS_MUG_12OZ["id"]), merchant_domain=domain)
        )
        mug16 = _run(
            _get_product(ctx, product_id=str(KITH_TREATS_MUG_16OZ["id"]), merchant_domain=domain)
        )

        orch = _orch()
        merged = _run(
            orch._group_discovered_products(
                ctx, [mug12.model_dump(mode="json"), mug16.model_dump(mode="json")]
            )
        )
        primary_id = merged[0]["product_id"]
        result = _run(
            orch._get_product_variants(ctx, product_id=primary_id, merchant_domain=domain)
        )
        capacities = {v["options"]["Capacity"] for v in result["variants"]}
        assert capacities == {"12oz", "16oz"}


# ── 6. Merged family card name never carries a stripped variant suffix ──


KITH_FAMILY_PAIRS = [
    (KITH_990V6_GREY, KITH_990V6_NAVY, "Kith x New Balance 990v6"),
    (KITH_CREWNECK_BLACK, KITH_CREWNECK_WHITE, "Kith Logo Crewneck"),
    (KITH_MOCKNECK_WOOL, KITH_MOCKNECK_COTTON, "Kith Mock Neck Sweater"),
    (KITH_TREATS_MUG_12OZ, KITH_TREATS_MUG_16OZ, "Kith Treats Mug"),
]


class TestNoVariantVocabularyLeakInFamilyCardName:
    @pytest.mark.parametrize("member_a,member_b,expected_name", KITH_FAMILY_PAIRS)
    def test_family_card_name_has_no_dangling_variant_suffix(
        self, ctx, member_a, member_b, expected_name
    ):
        domain = "kith.com"
        a = _run(_get_product(ctx, product_id=str(member_a["id"]), merchant_domain=domain))
        b = _run(_get_product(ctx, product_id=str(member_b["id"]), merchant_domain=domain))

        merged = _run(
            _orch()._group_discovered_products(
                ctx, [a.model_dump(mode="json"), b.model_dump(mode="json")]
            )
        )
        assert len(merged) == 1
        name = merged[0]["name"]
        assert name == expected_name

        # The merged name must not end with ANY recognized variant value
        # (color, material, capacity, etc.) — would imply "the product" is
        # one specific colorway/material/size.
        for value in VARIANT_VOCABULARY:
            assert not name.endswith(value), (
                f"family card name {name!r} leaks variant value {value!r}"
            )


# ── Gateway registration check (per CLAUDE.md rule 3) ────────────────────


@pytest.mark.parametrize("domain", sorted(set(MERCHANTS) | set(LIVE_MERCHANTS)))
def test_gateway_registration_covers_query_variant_matrix(domain):
    if domain in MERCHANTS:
        assert domain in MERCHANTS
    else:
        assert domain == "kith.com", (
            f"New live merchant {domain!r} needs a mocked discovery-query-variant "
            f"matrix fixture in tests/test_discovery_query_variants.py."
        )
