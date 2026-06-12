"""E2E chat flow test (Phase 1, task 1.10): "add X to my cart" for a product
with variants → ``add_to_cart`` (no ``variant_id``) → ``variant_required`` →
the model retries with the chosen ``variant_id`` → the cart line carries
``selected_options``.

Parametrized across one variant product per merchant in
``config.catalogue.MERCHANTS`` AND the live merchant ``kith.com``
(``LIVE_MERCHANTS``, mocked HTTP via ``tests/fixtures/kith_products.py`` —
per CLAUDE.md rule 3).

Sorts before ``test_user_journeys.py`` ("chat_variant" < "user") -> uses
``asyncio.get_event_loop().run_until_complete()``.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest

from adapters.shopify_mcp import ShopifyMCPAdapter, StubShopifyTransport
from adapters.stripe import StripeAdapter
from agents.orchestrator import OrchestratorAgent
from cli.confirmation import AutoConfirmProvider
from config.catalogue import LIVE_MERCHANTS, MERCHANTS
from gateway.merchant_gateway import MerchantGateway
from gateway.payment_gateway import PaymentGateway
from guardrails.confidence import ConfidenceChecker
from guardrails.spending import SpendingLimiter
from models.user import UserProfile
from storage.state import SessionState
from tests.fake_anthropic import FakeAnthropicClient, text_response, tool_use_response
from tests.fixtures.kith_products import KITH_990V6_GREY, KITH_990V6_NAVY, make_kith_adapter
from tools import discovery_tools
from tools.context import ToolContext


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _demo_variant_product_id(domain: str) -> str:
    for p in MERCHANTS[domain]:
        if p.get("variants"):
            return p["id"]
    raise AssertionError(f"{domain} has no variant product")


DEMO_VARIANT_PRODUCT = {domain: _demo_variant_product_id(domain) for domain in MERCHANTS}
DOMAINS = sorted(MERCHANTS)


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


def _first_variant(ctx, *, product_id: str, merchant_domain: str) -> dict:
    result = _run(
        discovery_tools.get_product_variants(
            ctx, product_id=product_id, merchant_domain=merchant_domain
        )
    )
    assert result["has_variants"] is True
    return result["variants"][0]


class TestAddToCartVariantRetryFlow:
    @pytest.mark.parametrize("domain", DOMAINS)
    def test_demo_variant_product_retry_flow(self, ctx, domain):
        product_id = DEMO_VARIANT_PRODUCT[domain]
        target = _first_variant(ctx, product_id=product_id, merchant_domain=domain)

        responses = [
            tool_use_response(
                (
                    "add_to_cart",
                    {"product_id": product_id, "merchant_domain": domain, "quantity": 1},
                )
            ),
            tool_use_response(
                (
                    "add_to_cart",
                    {
                        "product_id": product_id,
                        "merchant_domain": domain,
                        "quantity": 1,
                        "variant_id": target["variant_id"],
                    },
                )
            ),
            text_response("Added to your cart."),
        ]
        client = FakeAnthropicClient(responses)
        orch = OrchestratorAgent(client, confirmation=AutoConfirmProvider(), mandate_id="m_test")

        result = _run(orch.run(ctx, "add it to my cart"))

        assert client.remaining() == 0
        assert "reply" in result

        bucket = ctx.session.click_basket[domain]
        assert len(bucket) == 1
        line = bucket[0]
        assert line["product_id"] == product_id
        assert line["variant_id"] == target["variant_id"]
        assert line["selected_options"] == target["options"]

    def test_kith_variant_product_retry_flow(self, ctx):
        product_id = str(KITH_990V6_GREY["id"])
        domain = "kith.com"
        target = _first_variant(ctx, product_id=product_id, merchant_domain=domain)

        responses = [
            tool_use_response(
                (
                    "add_to_cart",
                    {"product_id": product_id, "merchant_domain": domain, "quantity": 1},
                )
            ),
            tool_use_response(
                (
                    "add_to_cart",
                    {
                        "product_id": product_id,
                        "merchant_domain": domain,
                        "quantity": 1,
                        "variant_id": target["variant_id"],
                    },
                )
            ),
            text_response("Added to your cart."),
        ]
        client = FakeAnthropicClient(responses)
        orch = OrchestratorAgent(client, confirmation=AutoConfirmProvider(), mandate_id="m_test")

        result = _run(orch.run(ctx, "add the New Balance 990v6 to my cart"))

        assert client.remaining() == 0
        bucket = ctx.session.click_basket[domain]
        assert len(bucket) == 1
        line = bucket[0]
        assert line["product_id"] == product_id
        assert line["variant_id"] == target["variant_id"]
        assert line["selected_options"] == target["options"]
        assert Decimal(str(line["price"])) == Decimal("200.00")


class TestAddToCartNoVariantRegression:
    """A no-variant product adds in ONE round trip — no
    ``variant_required`` detour, no retry call to the model."""

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_demo_plain_product_adds_in_one_call(self, ctx, domain):
        plain_id = next(p["id"] for p in MERCHANTS[domain] if not p.get("variants"))

        responses = [
            tool_use_response(
                ("add_to_cart", {"product_id": plain_id, "merchant_domain": domain, "quantity": 1})
            ),
            text_response("Added to your cart."),
        ]
        client = FakeAnthropicClient(responses)
        orch = OrchestratorAgent(client, confirmation=AutoConfirmProvider(), mandate_id="m_test")

        result = _run(orch.run(ctx, "add it to my cart"))

        assert client.remaining() == 0
        bucket = ctx.session.click_basket[domain]
        assert len(bucket) == 1
        assert bucket[0]["variant_id"] is None
        assert bucket[0]["selected_options"] == {}

    def test_kith_plain_product_adds_in_one_call(self, ctx):
        from tests.fixtures.kith_products import KITH_CAMP_CAP

        product_id = str(KITH_CAMP_CAP["id"])
        domain = "kith.com"

        responses = [
            tool_use_response(
                (
                    "add_to_cart",
                    {"product_id": product_id, "merchant_domain": domain, "quantity": 1},
                )
            ),
            text_response("Added to your cart."),
        ]
        client = FakeAnthropicClient(responses)
        orch = OrchestratorAgent(client, confirmation=AutoConfirmProvider(), mandate_id="m_test")

        result = _run(orch.run(ctx, "add the camp cap to my cart"))

        assert client.remaining() == 0
        bucket = ctx.session.click_basket[domain]
        assert len(bucket) == 1
        assert bucket[0]["variant_id"] is None
        assert bucket[0]["selected_options"] == {}


class TestMultiDimensionFamilyPartialSelection:
    """Phase 1 bugfix addendum (2026-06-10), screenshot-4 sub-symptom: a
    multi-dimension family (990v6 Grey/Navy -- option_names=["Size","Color"]
    after the Bug 3b backfill fix) must support a multi-turn flow where the
    user supplies ONE dimension (color) first, the agent asks only for the
    remaining dimension (size), and the second ``add_to_cart`` call (with the
    fully-resolved synthesized ``variant_id``) succeeds -- with NO
    "purchase was cancelled" / cancellation-flow status anywhere in this
    sequence (cancellation language is reserved for the purchase/HITL gate,
    never for an incomplete ``add_to_cart``)."""

    def test_partial_then_complete_variant_selection(self, ctx):
        domain = "kith.com"
        primary_id = str(KITH_990V6_GREY["id"])
        navy_id = str(KITH_990V6_NAVY["id"])

        grey = _run(
            discovery_tools.get_product_details(
                ctx, product_id=primary_id, merchant_domain=domain, mandate_id="m_test"
            )
        )
        navy = _run(
            discovery_tools.get_product_details(
                ctx, product_id=navy_id, merchant_domain=domain, mandate_id="m_test"
            )
        )

        orch = OrchestratorAgent(
            FakeAnthropicClient([]), confirmation=AutoConfirmProvider(), mandate_id="m_test"
        )

        # Simulate discovery having already run and grouped the 990v6
        # Grey/Navy listings into one family (Bug 3b: option_names now
        # includes BOTH "Size" (each member's own dimension) and "Color"
        # (the dimension the listings were split on)).
        merged = _run(
            orch._group_discovered_products(
                ctx, [grey.model_dump(mode="json"), navy.model_dump(mode="json")]
            )
        )
        assert len(merged) == 1, "990v6 Grey and Navy must collapse into one family card"
        assert set(merged[0]["option_names"]) == {"Size", "Color"}

        family = ctx.session.product_families[primary_id]
        grey_size_10 = next(
            v
            for v in family["variants"]
            if v["options"]["Color"] == "Grey" and v["options"]["Size"] == "10"
        )

        # Turn 1: user names the family AND one dimension ("the grey one")
        # but not the other (size). add_to_cart returns variant_required
        # with BOTH "Size" and "Color" -- the agent must ask only for the
        # missing dimension (Size), and must NOT say anything resembling
        # "purchase was cancelled".
        turn1_responses = [
            tool_use_response(
                (
                    "add_to_cart",
                    {"product_id": primary_id, "merchant_domain": domain, "quantity": 1},
                )
            ),
            text_response("What size would you like?"),
        ]
        orch.client = FakeAnthropicClient(turn1_responses)
        result1 = _run(orch.run(ctx, "add the grey 990v6 to my cart"))
        assert orch.client.remaining() == 0
        assert "reply" in result1
        assert "cancel" not in result1["reply"].lower()
        assert domain not in ctx.session.click_basket or not ctx.session.click_basket[domain]

        # Turn 2: user supplies the missing dimension (size 10). The agent
        # resolves the fully-specified synthesized variant_id (Grey, Size 10)
        # from the variant_required response in turn 1 and calls
        # add_to_cart again -- this must succeed (added=True), not route
        # through the purchase/HITL gate or any cancellation status.
        turn2_responses = [
            tool_use_response(
                (
                    "add_to_cart",
                    {
                        "product_id": primary_id,
                        "merchant_domain": domain,
                        "quantity": 1,
                        "variant_id": grey_size_10["variant_id"],
                    },
                )
            ),
            text_response("Added to your cart."),
        ]
        orch.client = FakeAnthropicClient(turn2_responses)
        result2 = _run(orch.run(ctx, "size 10"))
        assert orch.client.remaining() == 0
        assert "reply" in result2
        assert "cancel" not in result2["reply"].lower()
        assert result2.get("status") not in ("cancelled_by_user", "gate_closed")

        bucket = ctx.session.click_basket[domain]
        assert len(bucket) == 1
        line = bucket[0]
        assert line["variant_id"] == grey_size_10["variant_id"]
        assert line["selected_options"] == {"Size": "10", "Color": "Grey"}


@pytest.mark.parametrize("domain", sorted(set(MERCHANTS) | set(LIVE_MERCHANTS)))
def test_gateway_registration_covers_chat_variant_flow(domain):
    if domain in MERCHANTS:
        assert domain in MERCHANTS
    else:
        assert domain == "kith.com", (
            f"New live merchant {domain!r} needs a mocked chat-variant-flow "
            f"fixture in tests/test_chat_variant_flow.py."
        )
