"""Unit tests for ``POST /cart/add`` variant handling and composite line
identity (Phase 1, task 1.3/1.6).

Covers, parametrized over every demo merchant in ``config.catalogue.MERCHANTS``
(per CLAUDE.md rule 3):
  - missing ``variant_id`` on a variant product -> 400
  - invalid ``variant_id`` -> 400
  - valid ``variant_id`` -> 200, line carries ``variant_id``/``selected_options``
    and the correct (possibly variant-overridden) price
  - two distinct ``variant_id``s of the same product -> two separate lines
  - the same ``variant_id`` added twice -> quantity bump on ONE line
  - no-variant products are completely unaffected (regression)

Plus a dedicated out-of-stock-variant case using ``ath_002`` (Trail Runner
Pro — every size variant is unavailable).

Sorts before ``test_user_journeys.py`` ("cart_variant" < "user") -> use
``asyncio.get_event_loop().run_until_complete()`` (none needed here — this
file only drives the FastAPI TestClient, no raw asyncio).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from agents.orchestrator import OrchestratorAgent
from cli.confirmation import AutoConfirmProvider
from config.catalogue import MERCHANTS
from tests.fake_anthropic import FakeAnthropicClient
from tools.discovery_tools import get_product_details
from web import session as session_mod
from web.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "demo.json"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = create_app()
    with TestClient(app) as c:
        yield c


JSON_HEADERS = {"Accept": "application/json"}


def _demo_variant_and_plain(domain: str) -> tuple[str, str]:
    variant_id = plain_id = None
    for p in MERCHANTS[domain]:
        if p.get("variants") and variant_id is None:
            variant_id = p["id"]
        elif not p.get("variants") and plain_id is None:
            plain_id = p["id"]
    return variant_id, plain_id


DEMO_VARIANT_PLAIN = {domain: _demo_variant_and_plain(domain) for domain in MERCHANTS}
DOMAINS = sorted(MERCHANTS)


def _product_variants(client, domain, product_id):
    client.get("/")
    raw = client.cookies.get("ac_session")
    sid = session_mod._serializer.loads(raw)
    sess = session_mod.get_session_by_id(sid)

    async def _run():
        return await get_product_details(
            sess.ctx, product_id=product_id, merchant_domain=domain, mandate_id=sess.mandate_id
        )

    return asyncio.get_event_loop().run_until_complete(_run())


# ── Missing / invalid variant_id ─────────────────────────────────────────


class TestVariantRequired:
    @pytest.mark.parametrize("domain", DOMAINS)
    def test_add_variant_product_without_variant_id_is_400(self, client, domain):
        variant_id, _plain = DEMO_VARIANT_PLAIN[domain]
        r = client.post(f"/cart/add/{domain}/{variant_id}", headers=JSON_HEADERS)
        assert r.status_code == 400
        body = r.json()
        assert "options" in body["flash"].lower()
        assert body["lines"] == []

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_add_variant_product_with_invalid_variant_id_is_400(self, client, domain):
        variant_id, _plain = DEMO_VARIANT_PLAIN[domain]
        r = client.post(
            f"/cart/add/{domain}/{variant_id}",
            data={"variant_id": "does-not-exist"},
            headers=JSON_HEADERS,
        )
        assert r.status_code == 400
        body = r.json()
        assert "no longer available" in body["flash"].lower()
        assert body["lines"] == []


# ── Valid variant add ────────────────────────────────────────────────────


class TestValidVariantAdd:
    @pytest.mark.parametrize("domain", DOMAINS)
    def test_add_with_valid_variant_id_creates_line(self, client, domain):
        variant_id, _plain = DEMO_VARIANT_PLAIN[domain]
        product = _product_variants(client, domain, variant_id)
        first_variant = product.variants[0]

        r = client.post(
            f"/cart/add/{domain}/{variant_id}",
            data={"variant_id": first_variant.variant_id},
            headers=JSON_HEADERS,
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["lines"]) == 1
        line = body["lines"][0]
        assert line["product_id"] == variant_id
        assert line["variant_id"] == first_variant.variant_id
        assert line["selected_options"] == first_variant.options
        expected_price = first_variant.price if first_variant.price is not None else product.price
        assert Decimal(str(line["price"])) == Decimal(str(expected_price))
        assert int(line["quantity"]) == 1

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_two_distinct_variant_ids_create_two_lines(self, client, domain):
        variant_id, _plain = DEMO_VARIANT_PLAIN[domain]
        product = _product_variants(client, domain, variant_id)
        assert len(product.variants) >= 2, f"{domain}:{variant_id} needs >=2 variants for this test"
        v1, v2 = product.variants[0], product.variants[1]

        r1 = client.post(
            f"/cart/add/{domain}/{variant_id}",
            data={"variant_id": v1.variant_id},
            headers=JSON_HEADERS,
        )
        assert r1.status_code == 200

        r2 = client.post(
            f"/cart/add/{domain}/{variant_id}",
            data={"variant_id": v2.variant_id},
            headers=JSON_HEADERS,
        )
        assert r2.status_code == 200
        body = r2.json()
        assert len(body["lines"]) == 2
        line_variant_ids = {l["variant_id"] for l in body["lines"]}
        assert line_variant_ids == {v1.variant_id, v2.variant_id}
        # Both lines belong to the same underlying product.
        assert {l["product_id"] for l in body["lines"]} == {variant_id}

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_same_variant_id_twice_bumps_quantity(self, client, domain):
        variant_id, _plain = DEMO_VARIANT_PLAIN[domain]
        product = _product_variants(client, domain, variant_id)
        target = product.variants[0]

        for _ in range(2):
            r = client.post(
                f"/cart/add/{domain}/{variant_id}",
                data={"variant_id": target.variant_id},
                headers=JSON_HEADERS,
            )
            assert r.status_code == 200

        body = r.json()
        assert len(body["lines"]) == 1
        line = body["lines"][0]
        assert line["variant_id"] == target.variant_id
        assert int(line["quantity"]) == 2
        expected_price = target.price if target.price is not None else product.price
        assert Decimal(str(line["line_total"])) == Decimal(str(expected_price)) * 2


# ── No-variant products: regression-safe ─────────────────────────────────


class TestNoVariantProductsUnaffected:
    @pytest.mark.parametrize("domain", DOMAINS)
    def test_add_no_variant_product_succeeds_without_variant_id(self, client, domain):
        _variant, plain_id = DEMO_VARIANT_PLAIN[domain]
        r = client.post(f"/cart/add/{domain}/{plain_id}", headers=JSON_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert len(body["lines"]) == 1
        line = body["lines"][0]
        assert line["product_id"] == plain_id
        assert line["variant_id"] is None
        assert line["selected_options"] == {}

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_add_no_variant_product_twice_bumps_quantity(self, client, domain):
        _variant, plain_id = DEMO_VARIANT_PLAIN[domain]
        for _ in range(2):
            r = client.post(f"/cart/add/{domain}/{plain_id}", headers=JSON_HEADERS)
            assert r.status_code == 200
        body = r.json()
        assert len(body["lines"]) == 1
        assert int(body["lines"][0]["quantity"]) == 2

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_no_variant_product_ignores_stray_variant_id(self, client, domain):
        """Per 1.6: no-variant products force ``variant_id = None`` even if
        a client somehow sends one — never a 400, never a phantom variant
        line."""
        _variant, plain_id = DEMO_VARIANT_PLAIN[domain]
        r = client.post(
            f"/cart/add/{domain}/{plain_id}",
            data={"variant_id": "bogus"},
            headers=JSON_HEADERS,
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["lines"]) == 1
        assert body["lines"][0]["variant_id"] is None


# ── Out-of-stock variant (ath_002 — Trail Runner Pro) ────────────────────


class TestOutOfStockVariant:
    def test_out_of_stock_variant_returns_400(self, client):
        domain = "athletic-co.myshopify.com"
        product = _product_variants(client, domain, "ath_002")
        assert product.variants, "ath_002 should carry size variants"
        target = product.variants[0]
        assert target.in_stock is False

        r = client.post(
            f"/cart/add/{domain}/ath_002",
            data={"variant_id": target.variant_id},
            headers=JSON_HEADERS,
        )
        assert r.status_code == 400
        body = r.json()
        assert "out of stock" in body["flash"].lower()
        assert body["lines"] == []


# ── Remove / quantity endpoints respect (product_id, variant_id) ────────


class TestRemoveAndQuantityCompositeKey:
    def test_remove_one_variant_leaves_sibling_line_intact(self, client):
        domain = "athletic-co.myshopify.com"
        variant_id, _plain = DEMO_VARIANT_PLAIN[domain]
        product = _product_variants(client, domain, variant_id)
        v1, v2 = product.variants[0], product.variants[1]

        for v in (v1, v2):
            r = client.post(
                f"/cart/add/{domain}/{variant_id}",
                data={"variant_id": v.variant_id},
                headers=JSON_HEADERS,
            )
            assert r.status_code == 200

        r = client.post(
            f"/cart/remove/{domain}/{variant_id}",
            data={"variant_id": v1.variant_id},
            headers=JSON_HEADERS,
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["lines"]) == 1
        assert body["lines"][0]["variant_id"] == v2.variant_id

    def test_quantity_change_targets_correct_variant_line(self, client):
        domain = "athletic-co.myshopify.com"
        variant_id, _plain = DEMO_VARIANT_PLAIN[domain]
        product = _product_variants(client, domain, variant_id)
        v1, v2 = product.variants[0], product.variants[1]

        for v in (v1, v2):
            r = client.post(
                f"/cart/add/{domain}/{variant_id}",
                data={"variant_id": v.variant_id},
                headers=JSON_HEADERS,
            )
            assert r.status_code == 200

        r = client.post(
            f"/cart/quantity/{domain}/{variant_id}",
            data={"variant_id": v2.variant_id, "quantity": 5},
            headers=JSON_HEADERS,
        )
        assert r.status_code == 200
        body = r.json()
        by_variant = {l["variant_id"]: l for l in body["lines"]}
        assert int(by_variant[v2.variant_id]["quantity"]) == 5
        assert int(by_variant[v1.variant_id]["quantity"]) == 1


# ── Family-cache resolution: demo merchant family-of-1 (Phase 1 bugfix
#    addendum, 2026-06-10, Bug 3a) ────────────────────────────────────────


def _orch() -> OrchestratorAgent:
    return OrchestratorAgent(
        client=FakeAnthropicClient([]),
        confirmation=AutoConfirmProvider(),
        mandate_id="m_test",
    )


class TestFamilyCacheResolutionDemoMerchants:
    """``add_to_cart``'s family-cache lookup (``ctx.session.product_families``)
    must be a no-op for demo merchant products, which always group into
    family-of-1 (``primary == product``). Adding via ``/cart/add`` after
    discovery/grouping has populated ``product_families`` must behave
    identically to the ungrouped case covered above."""

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_family_of_one_variant_product_add_unaffected(self, client, domain):
        variant_id, _plain = DEMO_VARIANT_PLAIN[domain]
        client.get("/")
        raw = client.cookies.get("ac_session")
        sid = session_mod._serializer.loads(raw)
        sess = session_mod.get_session_by_id(sid)

        product = _product_variants(client, domain, variant_id)

        async def _group():
            return await _orch()._group_discovered_products(
                sess.ctx, [product.model_dump(mode="json")]
            )

        merged = asyncio.get_event_loop().run_until_complete(_group())

        # Family-of-1 products are never cached in product_families (only
        # families with >1 member are) -- the cart route's family lookup is
        # a no-op and falls back to product.variants directly.
        assert sess.ctx.session.product_families.get(variant_id) is None

        first_variant = product.variants[0]
        r = client.post(
            f"/cart/add/{domain}/{variant_id}",
            data={"variant_id": first_variant.variant_id},
            headers=JSON_HEADERS,
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["lines"]) == 1
        line = body["lines"][0]
        assert line["product_id"] == variant_id
        assert line["variant_id"] == first_variant.variant_id
        assert line["selected_options"] == first_variant.options
        expected_price = first_variant.price if first_variant.price is not None else product.price
        assert Decimal(str(line["price"])) == Decimal(str(expected_price))

        del merged

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_family_of_one_no_variant_product_add_unaffected(self, client, domain):
        _variant, plain_id = DEMO_VARIANT_PLAIN[domain]
        client.get("/")
        raw = client.cookies.get("ac_session")
        sid = session_mod._serializer.loads(raw)
        sess = session_mod.get_session_by_id(sid)

        product = _product_variants(client, domain, plain_id)

        async def _group():
            return await _orch()._group_discovered_products(
                sess.ctx, [product.model_dump(mode="json")]
            )

        merged = asyncio.get_event_loop().run_until_complete(_group())

        # Family-of-1 -- not cached in product_families.
        assert sess.ctx.session.product_families.get(plain_id) is None

        r = client.post(f"/cart/add/{domain}/{plain_id}", headers=JSON_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert len(body["lines"]) == 1
        line = body["lines"][0]
        assert line["product_id"] == plain_id
        assert line["variant_id"] is None
        assert line["selected_options"] == {}

        del merged
