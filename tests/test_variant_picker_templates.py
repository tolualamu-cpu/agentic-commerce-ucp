"""Automated coverage for the variant-picker UI (Phase 1, task 1.9):

  - ``_variant_picker_modal.html`` (global modal, included in ``base.html``)
  - ``_variant_picker_controls.html`` (inline picker on ``product_detail.html``)
  - the variant-aware branches of ``_product_card.html``,
    ``_chat_product_card.html``, and ``product_detail.html``

Covers every demo merchant in ``config.catalogue.MERCHANTS`` (one variant
product + one non-variant product each) and the live merchant
``kith.com`` (``LIVE_MERCHANTS``) via a mocked transport, per CLAUDE.md
rule 3 (iterate the authoritative merchant dicts).

Sorts AFTER ``test_user_journeys.py`` alphabetically ("variant" > "user")
-> ``asyncio.run()`` is safe per CLAUDE.md's asyncio test-ordering rule.
"""

from __future__ import annotations

import asyncio
import json
import re
from html.parser import HTMLParser
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from adapters.shopify_mcp import LiveShopifyTransport, ShopifyMCPAdapter
from config.catalogue import LIVE_MERCHANTS, MERCHANTS
from tools.discovery_tools import get_product_details
from web import session as session_mod
from web.app import create_app


# ── Shared helpers ───────────────────────────────────────────────────────


def _parses_cleanly(html: str) -> None:
    """Fail if ``html`` cannot be parsed (mirrors
    test_chat_product_card_no_inline_js.py's innerHTML-safety check)."""
    parser = HTMLParser()
    parser.feed(html)
    parser.close()


def _data_product_blob(html: str, product_id: str) -> dict:
    """Extract and JSON-decode the ``data-product='...'`` attribute for the
    trigger element belonging to ``product_id``.

    Jinja's ``tojson`` filter escapes ``& < > '`` as ``\\uXXXX`` sequences
    (not HTML entities), so the raw attribute text between single quotes is
    valid JSON as-is.
    """
    matches = re.findall(r"data-product='([^']*)'", html)
    assert matches, "expected at least one data-product='...' attribute"
    for raw in matches:
        blob = json.loads(raw)
        if blob.get("product_id") == product_id:
            return blob
    raise AssertionError(
        f"no data-product blob found for product_id={product_id!r} "
        f"(found ids: {[json.loads(m).get('product_id') for m in matches]})"
    )


def _session_ctx(client: TestClient):
    """Return (ctx, mandate_id) for the TestClient's session."""
    client.get("/")
    raw = client.cookies.get("ac_session")
    sid = session_mod._serializer.loads(raw)
    sess = session_mod.get_session_by_id(sid)
    return sess.ctx, sess.mandate_id


def _fetch_product_dict(client: TestClient, *, merchant_domain: str, product_id: str) -> dict:
    """Fetch a ProductResult via the same tool the orchestrator/UI use, and
    serialize to the JSON-safe dict shape the chat fragment endpoint expects
    (mirrors ``ProductResult.model_dump(mode="json")`` as sent by the SSE
    pipeline)."""
    ctx, mandate_id = _session_ctx(client)

    async def _run():
        return await get_product_details(
            ctx, product_id=product_id, merchant_domain=merchant_domain, mandate_id=mandate_id
        )

    product = asyncio.run(_run())
    assert product is not None, f"product {product_id} not found for {merchant_domain}"
    return product.model_dump(mode="json")


# ── Demo merchant fixtures ───────────────────────────────────────────────


def _demo_variant_and_plain(domain: str) -> tuple[str, str]:
    """Pick one product WITH variants and one WITHOUT, for a demo merchant."""
    variant_id = plain_id = None
    for p in MERCHANTS[domain]:
        if p.get("variants") and variant_id is None:
            variant_id = p["id"]
        elif not p.get("variants") and plain_id is None:
            plain_id = p["id"]
    assert variant_id is not None, f"{domain} has no variant product in seed data"
    assert plain_id is not None, f"{domain} has no non-variant product in seed data"
    return variant_id, plain_id


DEMO_VARIANT_PLAIN = {domain: _demo_variant_and_plain(domain) for domain in MERCHANTS}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "demo.json"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = create_app()
    with TestClient(app) as c:
        yield c


# ── base.html: global modal is always present ──────────────────────────


class TestBaseTemplateIncludesVariantPickerModal:
    def test_home_page_has_variant_picker_overlay(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert 'id="variant-picker-overlay"' in r.text
        assert "window.__openVariantPicker" in r.text

    def test_chat_page_has_variant_picker_overlay(self, client):
        r = client.get("/chat")
        assert r.status_code == 200
        assert 'id="variant-picker-overlay"' in r.text
        assert "window.__openVariantPicker" in r.text


# ── product_detail.html: inline picker controls ─────────────────────────


class TestProductDetailVariantControls:
    @pytest.mark.parametrize("domain", sorted(MERCHANTS))
    def test_variant_product_renders_inline_picker(self, client, domain):
        variant_id, _plain_id = DEMO_VARIANT_PLAIN[domain]
        r = client.get(f"/product/{domain}/{variant_id}")
        assert r.status_code == 200
        html = r.text
        assert 'id="pdp-variant-controls"' in html
        assert 'id="pdp-variant-id"' in html
        assert 'id="pdp-add-btn"' in html
        # Submit disabled + "Select options" until JS resolves a full match.
        assert "Select options" in html
        assert "data-option-names=" in html
        assert "data-variants=" in html
        _parses_cleanly(html)

    @pytest.mark.parametrize("domain", sorted(MERCHANTS))
    def test_plain_product_has_no_inline_picker(self, client, domain):
        _variant_id, plain_id = DEMO_VARIANT_PLAIN[domain]
        r = client.get(f"/product/{domain}/{plain_id}")
        assert r.status_code == 200
        html = r.text
        assert 'id="pdp-variant-controls"' not in html
        assert 'id="pdp-variant-id"' not in html
        assert 'id="pdp-add-btn"' in html
        assert "Add to cart" in html
        assert "Select options" not in html
        _parses_cleanly(html)

    @pytest.mark.parametrize("domain", sorted(MERCHANTS))
    def test_variant_controls_data_matches_product(self, client, domain):
        """The data-* JSON blobs feeding the inline picker JS must reflect
        the product's real option_names/variants — never fabricated."""
        variant_id, _plain_id = DEMO_VARIANT_PLAIN[domain]
        ctx, mandate_id = _session_ctx(client)

        async def _run():
            return await get_product_details(
                ctx, product_id=variant_id, merchant_domain=domain, mandate_id=mandate_id
            )

        product = asyncio.run(_run())
        r = client.get(f"/product/{domain}/{variant_id}")
        html = r.text

        m = re.search(r"data-option-names='([^']*)'", html)
        assert m, "missing data-option-names"
        option_names = json.loads(m.group(1))
        assert option_names == product.option_names

        m = re.search(r"data-variants='([^']*)'", html)
        assert m, "missing data-variants"
        variants = json.loads(m.group(1))
        assert len(variants) == len(product.variants)
        assert {v["variant_id"] for v in variants} == {v.variant_id for v in product.variants}


# ── _product_card.html (Explore / Search grid) ──────────────────────────


class TestProductCardVariantPicker:
    @pytest.mark.parametrize("domain", sorted(MERCHANTS))
    def test_variant_product_card_uses_picker_button(self, client, domain):
        variant_id, _plain_id = DEMO_VARIANT_PLAIN[domain]
        product_dict = _fetch_product_dict(client, merchant_domain=domain, product_id=variant_id)

        r = client.get(f"/search?merchant={domain}&q={product_dict['name']}")
        assert r.status_code == 200
        html = r.text
        assert "window.__openVariantPicker(this)" in html

        blob = _data_product_blob(html, variant_id)
        assert blob["merchant_domain"] == domain
        assert blob["option_names"] == product_dict["option_names"]
        assert {v["variant_id"] for v in blob["variants"]} == {
            v["variant_id"] for v in product_dict["variants"]
        }
        # No direct-add <form> for THIS product when it has variants.
        assert f"/cart/add/{domain}/{variant_id}" not in html
        _parses_cleanly(html)

    @pytest.mark.parametrize("domain", sorted(MERCHANTS))
    def test_plain_product_card_uses_direct_add_form(self, client, domain):
        _variant_id, plain_id = DEMO_VARIANT_PLAIN[domain]
        product_dict = _fetch_product_dict(client, merchant_domain=domain, product_id=plain_id)

        r = client.get(f"/search?merchant={domain}&q={product_dict['name']}")
        assert r.status_code == 200
        html = r.text
        assert f'action="/cart/add/{domain}/{plain_id}"' in html
        assert "data-product='{" not in html or "__openVariantPicker" not in html
        _parses_cleanly(html)


# ── _chat_product_card.html (chat fragment) ─────────────────────────────


class TestChatProductCardVariantPicker:
    @pytest.mark.parametrize("domain", sorted(MERCHANTS))
    def test_variant_product_chat_card_uses_picker_button(self, client, domain):
        variant_id, _plain_id = DEMO_VARIANT_PLAIN[domain]
        product_dict = _fetch_product_dict(client, merchant_domain=domain, product_id=variant_id)

        r = client.post("/chat/products-fragment", json={"products": [product_dict]})
        assert r.status_code == 200
        html = r.text
        assert "chat-product-card" in html
        assert "window.__openVariantPicker(this)" in html
        assert "cart-toggle-btn" not in html

        blob = _data_product_blob(html, variant_id)
        assert blob["merchant_domain"] == domain
        assert blob["option_names"] == product_dict["option_names"]
        assert len(blob["variants"]) == len(product_dict["variants"])
        _parses_cleanly(html)

    @pytest.mark.parametrize("domain", sorted(MERCHANTS))
    def test_plain_product_chat_card_uses_cart_toggle(self, client, domain):
        _variant_id, plain_id = DEMO_VARIANT_PLAIN[domain]
        product_dict = _fetch_product_dict(client, merchant_domain=domain, product_id=plain_id)

        r = client.post("/chat/products-fragment", json={"products": [product_dict]})
        assert r.status_code == 200
        html = r.text
        assert "chat-product-card" in html
        assert "cart-toggle-btn" in html
        assert "window.__openVariantPicker" not in html
        _parses_cleanly(html)

    def test_no_inline_handlers_introduced_by_variant_branch(
        self,
        client,
    ):
        """Regression guard for test_chat_product_card_no_inline_js.py:
        the new variant branch must not introduce onmouseenter/onmouseleave."""
        domain = sorted(MERCHANTS)[0]
        variant_id, _plain_id = DEMO_VARIANT_PLAIN[domain]
        product_dict = _fetch_product_dict(client, merchant_domain=domain, product_id=variant_id)

        r = client.post("/chat/products-fragment", json={"products": [product_dict]})
        assert "onmouseenter=" not in r.text
        assert "onmouseleave=" not in r.text


# ── Live merchant (Kith) — mocked HTTP, no real network ──────────────────


KITH_SAMPLE_PRODUCTS = {
    "products": [
        {
            "id": 200001,
            "title": "Kith Williams III Sneaker",
            "handle": "kith-williams-iii",
            "body_html": "<p>Leather sneaker with rubber sole.</p>",
            "vendor": "ASICS",
            "product_type": "Footwear",
            "tags": ["sneaker", "footwear"],
            "options": [{"name": "Size"}],
            "variants": [
                {"id": 9001, "title": "9", "price": "150.00", "available": True, "option1": "9"},
                {"id": 9002, "title": "10", "price": "150.00", "available": True, "option1": "10"},
                {"id": 9003, "title": "11", "price": "150.00", "available": False, "option1": "11"},
            ],
            "images": [
                {"id": 20, "src": "https://cdn.shopify.com/kith-sneaker-1.jpg"},
                {"id": 21, "src": "https://cdn.shopify.com/kith-sneaker-2.jpg"},
            ],
        },
        {
            "id": 200002,
            "title": "Kith Ceramic Mug",
            "handle": "kith-ceramic-mug",
            "body_html": "<p>Ceramic mug with Kith logo.</p>",
            "vendor": "Kith",
            "product_type": "Home Goods",
            "tags": ["mug", "home"],
            "variants": [
                {
                    "id": 9004,
                    "title": "Default Title",
                    "price": "30.00",
                    "available": True,
                    "option1": "Default Title",
                },
            ],
            "images": [
                {"id": 22, "src": "https://cdn.shopify.com/kith-mug-1.jpg"},
            ],
        },
    ]
}


def _mock_response(data: dict):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = data
    return resp


def _mock_kith_transport() -> LiveShopifyTransport:
    transport = LiveShopifyTransport("https://kith.com", max_pages=1, cache_ttl=9999)
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response(KITH_SAMPLE_PRODUCTS))
    transport._http = mock_client
    transport._owns_http = False
    return transport


@pytest.fixture
def kith_client(tmp_path, monkeypatch):
    """TestClient with a mocked Kith transport carrying a variant product
    (Size, 3 variants, one out-of-stock) and a non-variant product."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "demo.json"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    mock_transport = _mock_kith_transport()
    _original_make = session_mod._make_session

    def _patched_make(session_id):
        sess = _original_make(session_id)
        sess.ctx.merchant_gateway.direct_adapters["kith.com"] = ShopifyMCPAdapter(
            "kith.com",
            mock_transport,
            source_protocol="shopify_storefront",
            merchant_display_name="Kith",
        )
        return sess

    monkeypatch.setattr(session_mod, "_make_session", _patched_make)
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("domain", sorted(LIVE_MERCHANTS))
def test_live_merchants_have_kith_style_fixture(domain):
    """Sanity: this module's mocked-fixture coverage tracks LIVE_MERCHANTS.
    If a new live merchant is added, this test fails until an equivalent
    mocked fixture + client are added to this file (per CLAUDE.md rule 3 /
    the Kith-template requirement)."""
    assert domain == "kith.com", (
        f"New live merchant {domain!r} detected — add a mocked variant-picker "
        f"fixture/client for it in tests/test_variant_picker_templates.py "
        f"(see _mock_kith_transport / kith_client)."
    )


class TestKithVariantPickerTemplates:
    def test_product_detail_variant_product_renders_inline_picker(self, kith_client):
        r = kith_client.get("/product/kith.com/200001")
        assert r.status_code == 200
        html = r.text
        assert 'id="pdp-variant-controls"' in html
        assert 'id="pdp-variant-id"' in html
        assert "Select options" in html
        m = re.search(r"data-option-names='([^']*)'", html)
        assert json.loads(m.group(1)) == ["Size"]
        m = re.search(r"data-variants='([^']*)'", html)
        variants = json.loads(m.group(1))
        assert len(variants) == 3
        assert {v["options"]["Size"] for v in variants} == {"9", "10", "11"}
        assert any(v["in_stock"] is False for v in variants)
        _parses_cleanly(html)

    def test_product_detail_plain_product_no_picker(self, kith_client):
        r = kith_client.get("/product/kith.com/200002")
        assert r.status_code == 200
        html = r.text
        assert 'id="pdp-variant-controls"' not in html
        assert "Add to cart" in html
        assert "Select options" not in html
        _parses_cleanly(html)

    def test_search_card_variant_product_uses_picker(self, kith_client):
        r = kith_client.get("/search?merchant=kith.com&q=Sneaker")
        assert r.status_code == 200
        html = r.text
        assert "window.__openVariantPicker(this)" in html
        blob = _data_product_blob(html, "200001")
        assert blob["merchant_domain"] == "kith.com"
        assert blob["option_names"] == ["Size"]
        assert len(blob["variants"]) == 3
        _parses_cleanly(html)

    def test_search_card_plain_product_direct_add(self, kith_client):
        r = kith_client.get("/search?merchant=kith.com&q=Mug")
        assert r.status_code == 200
        html = r.text
        assert 'action="/cart/add/kith.com/200002"' in html
        _parses_cleanly(html)

    def test_chat_card_variant_product_uses_picker(self, kith_client):
        product_dict = _fetch_product_dict(
            kith_client, merchant_domain="kith.com", product_id="200001"
        )
        assert product_dict["option_names"] == ["Size"]
        assert len(product_dict["variants"]) == 3

        r = kith_client.post("/chat/products-fragment", json={"products": [product_dict]})
        assert r.status_code == 200
        html = r.text
        assert "window.__openVariantPicker(this)" in html
        assert "cart-toggle-btn" not in html
        blob = _data_product_blob(html, "200001")
        assert blob["option_names"] == ["Size"]
        _parses_cleanly(html)

    def test_chat_card_plain_product_uses_cart_toggle(self, kith_client):
        product_dict = _fetch_product_dict(
            kith_client, merchant_domain="kith.com", product_id="200002"
        )
        assert product_dict["variants"] == []

        r = kith_client.post("/chat/products-fragment", json={"products": [product_dict]})
        assert r.status_code == 200
        html = r.text
        assert "cart-toggle-btn" in html
        assert "window.__openVariantPicker" not in html
        _parses_cleanly(html)
