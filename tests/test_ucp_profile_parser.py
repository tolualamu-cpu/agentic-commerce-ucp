"""Unit tests for the versioned UCP profile parser registry.

Covers:
  - _detect_version helper
  - _extract_supported_versions helper
  - VFlatParser (stub/no-version format)
  - V2026_04_08Parser (current spec wire format, based on real Kith profile)
  - ProfileParserRegistry: registration, exact match, newest fallback,
    flat fallback, negotiate_version_url
  - UCPProfileDiscovery._fetch_real integration (mocked HTTP)

Event-loop note: this file sorts before test_user_journeys.py ('p' < 'u'),
so async tests use loop.run_until_complete() — not asyncio.run().
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock


from models.ucp_profile import UCPProfile
from ucp.parsers.v2026_04_08 import V2026_04_08Parser
from ucp.parsers.v_flat import VFlatParser
from ucp.profile_parser import (
    ProfileParserRegistry,
    _detect_version,
    _extract_supported_versions,
    get_default_registry,
)

# ── Fixture data ──────────────────────────────────────────────────────────────

# Exact shape of Kith's real /.well-known/ucp response (2026-04-08 format).
# Captured 2026-06 — used as the ground-truth for parser correctness tests.
KITH_WIRE_PROFILE = {
    "ucp": {
        "version": "2026-04-08",
        "supported_versions": {
            "2026-04-08": "https://kithnyc.myshopify.com/.well-known/ucp/2026-04-08",
            "2026-01-23": "https://kithnyc.myshopify.com/.well-known/ucp/2026-01-23",
        },
        "services": {
            "dev.ucp.shopping": [
                {
                    "version": "2026-04-08",
                    "spec": "https://ucp.dev/2026-04-08/specification/overview/",
                    "transport": "mcp",
                    "endpoint": "https://kithnyc.myshopify.com/api/ucp/mcp",
                    "schema": "https://ucp.dev/2026-04-08/services/shopping/mcp.openrpc.json",
                },
                {
                    "version": "2026-04-08",
                    "spec": "https://ucp.dev/2026-04-08/specification/overview/",
                    "transport": "embedded",
                    "schema": "https://ucp.dev/2026-04-08/services/shopping/embedded.openrpc.json",
                },
            ]
        },
        "capabilities": {
            "dev.ucp.shopping.checkout": [
                {
                    "version": "2026-04-08",
                    "spec": "https://ucp.dev/2026-04-08/specification/checkout",
                    "schema": "https://ucp.dev/2026-04-08/schemas/shopping/checkout.json",
                }
            ],
            "dev.ucp.shopping.cart": [
                {
                    "version": "2026-04-08",
                    "spec": "https://ucp.dev/2026-04-08/specification/cart",
                    "schema": "https://ucp.dev/2026-04-08/schemas/shopping/cart.json",
                }
            ],
            "dev.ucp.shopping.catalog.search": [
                {
                    "version": "2026-04-08",
                    "spec": "https://ucp.dev/2026-04-08/specification/catalog",
                    "schema": "https://ucp.dev/2026-04-08/schemas/shopping/catalog_search.json",
                }
            ],
            "dev.ucp.shopping.catalog.lookup": [
                {
                    "version": "2026-04-08",
                    "spec": "https://ucp.dev/2026-04-08/specification/catalog",
                    "schema": "https://ucp.dev/2026-04-08/schemas/shopping/catalog_lookup.json",
                }
            ],
            "dev.ucp.shopping.fulfillment": [
                {
                    "version": "2026-04-08",
                    "spec": "https://ucp.dev/2026-04-08/specification/fulfillment",
                    "schema": "https://ucp.dev/2026-04-08/schemas/shopping/fulfillment.json",
                    "extends": ["dev.ucp.shopping.checkout", "dev.ucp.shopping.cart"],
                }
            ],
            "dev.ucp.shopping.discount": [
                {
                    "version": "2026-04-08",
                    "spec": "https://ucp.dev/2026-04-08/specification/discount",
                    "schema": "https://ucp.dev/2026-04-08/schemas/shopping/discount.json",
                }
            ],
            "dev.ucp.shopping.order": [
                {
                    "version": "2026-04-08",
                    "spec": "https://ucp.dev/2026-04-08/specs/shopping/order",
                    "schema": "https://ucp.dev/2026-04-08/schemas/shopping/order.json",
                }
            ],
            "dev.shopify.catalog": [
                {
                    "version": "2026-04-08",
                    "spec": "https://shopify.dev/docs/agents/catalog/storefront-catalog",
                    "schema": "https://shopify.dev/ucp/schemas/2026-04-08/shopify_catalog.json",
                    "extends": [
                        "dev.ucp.shopping.catalog.search",
                        "dev.ucp.shopping.catalog.lookup",
                    ],
                }
            ],
        },
        "payment_handlers": {
            "com.google.pay": [
                {
                    "id": "gpay",
                    "version": "2026-01-11",
                    "spec": "https://pay.google.com/gp/p/ucp/2026-01-11/",
                    "schema": "https://pay.google.com/gp/p/ucp/2026-01-11/schemas/config.json",
                    "config": {},
                }
            ],
            "dev.shopify.card": [
                {
                    "id": "shopify.card",
                    "version": "2026-01-15",
                    "spec": "https://ucp.dev/specification/payment-handler-guide",
                    "schema": "https://shopify.dev/ucp/card-payment-handler/2026-01-15/config.json",
                    "config": {},
                }
            ],
            "dev.shopify.shop_pay": [
                {
                    "id": "shop_pay",
                    "version": "2026-04-08",
                    "spec": "https://shopify.dev/ucp/shop-pay-handler/2026-04-08/spec.md",
                    "schema": "https://shopify.dev/ucp/shop-pay-handler/2026-04-08/schema.json",
                    "config": {"shop_id": "942252"},
                }
            ],
        },
    }
}

FLAT_STUB_PROFILE = {
    "merchant_domain": "athletic-co.myshopify.com",
    "capabilities": [],
    "services": [],
    "payment_handlers": [
        {"id": "stripe", "name": "Stripe", "spec_url": "https://stripe.com/docs/api"}
    ],
    "signing_keys": [],
    "_note": "comment field — should be dropped",
}


# ── _detect_version ───────────────────────────────────────────────────────────


class TestDetectVersion:
    def test_2026_04_08_wrapped(self):
        assert _detect_version(KITH_WIRE_PROFILE) == "2026-04-08"

    def test_flat_stub_returns_none(self):
        assert _detect_version(FLAT_STUB_PROFILE) is None

    def test_empty_dict_returns_none(self):
        assert _detect_version({}) is None

    def test_ucp_key_without_version_returns_none(self):
        assert _detect_version({"ucp": {}}) is None

    def test_hypothetical_flat_versioned_format(self):
        # A hypothetical older flat format with a top-level "version" key
        assert _detect_version({"version": "2025-01-15", "capabilities": []}) == "2025-01-15"

    def test_ucp_version_takes_priority_over_root_version(self):
        data = {"ucp": {"version": "2026-04-08"}, "version": "old"}
        assert _detect_version(data) == "2026-04-08"


# ── _extract_supported_versions ───────────────────────────────────────────────


class TestExtractSupportedVersions:
    def test_kith_profile(self):
        result = _extract_supported_versions(KITH_WIRE_PROFILE)
        assert "2026-04-08" in result
        assert "2026-01-23" in result
        assert result["2026-04-08"] == "https://kithnyc.myshopify.com/.well-known/ucp/2026-04-08"

    def test_flat_profile_returns_empty(self):
        assert _extract_supported_versions(FLAT_STUB_PROFILE) == {}

    def test_ucp_without_supported_versions(self):
        assert _extract_supported_versions({"ucp": {"version": "2026-04-08"}}) == {}


# ── VFlatParser ───────────────────────────────────────────────────────────────


class TestVFlatParser:
    def setup_method(self):
        self.parser = VFlatParser()

    def test_versions_is_empty_tuple(self):
        assert self.parser.versions == ()

    def test_preserves_all_fields(self):
        result = self.parser.parse(FLAT_STUB_PROFILE, "athletic-co.myshopify.com")
        assert result["merchant_domain"] == "athletic-co.myshopify.com"
        assert result["capabilities"] == []
        assert len(result["payment_handlers"]) == 1

    def test_strips_comment_fields(self):
        result = self.parser.parse(FLAT_STUB_PROFILE, "athletic-co.myshopify.com")
        assert "_note" not in result

    def test_injects_merchant_domain_if_missing(self):
        data = {"capabilities": [], "services": [], "payment_handlers": [], "signing_keys": []}
        result = self.parser.parse(data, "new-merchant.com")
        assert result["merchant_domain"] == "new-merchant.com"

    def test_does_not_override_existing_merchant_domain(self):
        data = {"merchant_domain": "original.com", "capabilities": []}
        result = self.parser.parse(data, "injected.com")
        assert result["merchant_domain"] == "original.com"


# ── V2026_04_08Parser ─────────────────────────────────────────────────────────


class TestV2026_04_08Parser:
    def setup_method(self):
        self.parser = V2026_04_08Parser()

    def test_versions_contains_2026_04_08(self):
        assert "2026-04-08" in self.parser.versions

    def test_parse_kith_merchant_domain(self):
        result = self.parser.parse(KITH_WIRE_PROFILE, "kith.com")
        assert result["merchant_domain"] == "kith.com"

    def test_parse_kith_capabilities_count(self):
        result = self.parser.parse(KITH_WIRE_PROFILE, "kith.com")
        # 8 namespaces in the fixture
        assert len(result["capabilities"]) == 8

    def test_parse_kith_capabilities_namespaces(self):
        result = self.parser.parse(KITH_WIRE_PROFILE, "kith.com")
        namespaces = {c["namespace"] for c in result["capabilities"]}
        assert "dev.ucp.shopping.checkout" in namespaces
        assert "dev.ucp.shopping.cart" in namespaces
        assert "dev.ucp.shopping.catalog.search" in namespaces
        assert "dev.ucp.shopping.catalog.lookup" in namespaces
        assert "dev.shopify.catalog" in namespaces

    def test_parse_capability_spec_url_mapped_from_spec(self):
        result = self.parser.parse(KITH_WIRE_PROFILE, "kith.com")
        checkout = next(
            c for c in result["capabilities"] if c["namespace"] == "dev.ucp.shopping.checkout"
        )
        assert checkout["spec_url"] == "https://ucp.dev/2026-04-08/specification/checkout"

    def test_parse_capability_schema_url_preserved(self):
        result = self.parser.parse(KITH_WIRE_PROFILE, "kith.com")
        checkout = next(
            c for c in result["capabilities"] if c["namespace"] == "dev.ucp.shopping.checkout"
        )
        assert checkout["schema_url"] == "https://ucp.dev/2026-04-08/schemas/shopping/checkout.json"

    def test_parse_services_only_mcp_included(self):
        # "embedded" transport must be dropped
        result = self.parser.parse(KITH_WIRE_PROFILE, "kith.com")
        types = [s["type"] for s in result["services"]]
        assert "mcp" in types
        assert "embedded" not in types

    def test_parse_services_mcp_endpoint(self):
        result = self.parser.parse(KITH_WIRE_PROFILE, "kith.com")
        mcp = next(s for s in result["services"] if s["type"] == "mcp")
        assert mcp["base_url"] == "https://kithnyc.myshopify.com/api/ucp/mcp"

    def test_parse_payment_handlers_count(self):
        result = self.parser.parse(KITH_WIRE_PROFILE, "kith.com")
        assert len(result["payment_handlers"]) == 3

    def test_parse_payment_handler_ids(self):
        result = self.parser.parse(KITH_WIRE_PROFILE, "kith.com")
        ids = {h["id"] for h in result["payment_handlers"]}
        assert "gpay" in ids
        assert "shopify.card" in ids
        assert "shop_pay" in ids

    def test_parse_payment_handler_name_falls_back_to_namespace(self):
        # Handler dicts don't have a "name" field — must use namespace key
        result = self.parser.parse(KITH_WIRE_PROFILE, "kith.com")
        gpay = next(h for h in result["payment_handlers"] if h["id"] == "gpay")
        assert gpay["name"] == "com.google.pay"

    def test_parse_produces_valid_ucp_profile(self):
        result = self.parser.parse(KITH_WIRE_PROFILE, "kith.com")
        profile = UCPProfile(**result)
        assert profile.merchant_domain == "kith.com"
        assert len(profile.capabilities) == 8
        assert profile.has_capability("dev.ucp.shopping.checkout")
        assert profile.has_capability("dev.ucp.shopping.catalog.search")
        assert profile.preferred_transport() == "mcp"

    def test_parse_empty_capabilities_dict(self):
        data = {
            "ucp": {
                "version": "2026-04-08",
                "capabilities": {},
                "services": {},
                "payment_handlers": {},
            }
        }
        result = self.parser.parse(data, "empty.com")
        assert result["capabilities"] == []
        assert result["services"] == []
        assert result["payment_handlers"] == []

    def test_parse_missing_sections_default_to_empty(self):
        data = {"ucp": {"version": "2026-04-08"}}
        result = self.parser.parse(data, "minimal.com")
        assert result["capabilities"] == []
        assert result["services"] == []
        assert result["payment_handlers"] == []
        assert result["signing_keys"] == []


# ── ProfileParserRegistry ─────────────────────────────────────────────────────


class TestProfileParserRegistry:
    def _make_registry(self):
        reg = ProfileParserRegistry()
        reg.register(V2026_04_08Parser())
        reg.register(VFlatParser())
        return reg

    def test_known_versions_includes_2026_04_08(self):
        reg = self._make_registry()
        assert "2026-04-08" in reg.known_versions()

    def test_known_versions_newest_first(self):
        reg = self._make_registry()
        versions = reg.known_versions()
        assert versions == sorted(versions, reverse=True)

    def test_exact_match_routes_to_2026_parser(self):
        reg = self._make_registry()
        result = reg.parse(KITH_WIRE_PROFILE, "kith.com")
        assert result is not None
        namespaces = {c["namespace"] for c in result["capabilities"]}
        assert "dev.ucp.shopping.checkout" in namespaces

    def test_flat_format_routes_to_flat_parser(self):
        reg = self._make_registry()
        result = reg.parse(FLAT_STUB_PROFILE, "athletic-co.myshopify.com")
        assert result is not None
        assert result["merchant_domain"] == "athletic-co.myshopify.com"
        assert "_note" not in result

    def test_unknown_future_version_falls_back_to_newest(self):
        reg = self._make_registry()
        future_profile = {
            "ucp": {
                "version": "2099-01-01",
                "capabilities": {
                    "dev.ucp.shopping.checkout": [
                        {"version": "2099-01-01", "spec": "x", "schema": "x"}
                    ]
                },
                "services": {},
                "payment_handlers": {},
            }
        }
        result = reg.parse(future_profile, "future.com")
        # Should not return None — falls back to newest (2026-04-08) parser
        assert result is not None
        assert len(result["capabilities"]) == 1

    def test_no_parsers_returns_none(self):
        reg = ProfileParserRegistry()
        result = reg.parse(KITH_WIRE_PROFILE, "kith.com")
        assert result is None

    def test_negotiate_version_url_picks_highest_known(self):
        reg = self._make_registry()
        supported = {
            "2026-04-08": "https://example.com/.well-known/ucp/2026-04-08",
            "2026-01-23": "https://example.com/.well-known/ucp/2026-01-23",
        }
        url = reg.negotiate_version_url(supported)
        assert url == "https://example.com/.well-known/ucp/2026-04-08"

    def test_negotiate_version_url_returns_none_when_no_overlap(self):
        reg = self._make_registry()
        supported = {"1999-01-01": "https://example.com/.well-known/ucp/1999-01-01"}
        url = reg.negotiate_version_url(supported)
        assert url is None

    def test_negotiate_version_url_empty_dict(self):
        reg = self._make_registry()
        assert reg.negotiate_version_url({}) is None


# ── Default registry ──────────────────────────────────────────────────────────


class TestDefaultRegistry:
    def test_singleton_identity(self):
        r1 = get_default_registry()
        r2 = get_default_registry()
        assert r1 is r2

    def test_default_registry_parses_kith_profile(self):
        reg = get_default_registry()
        result = reg.parse(KITH_WIRE_PROFILE, "kith.com")
        assert result is not None
        profile = UCPProfile(**result)
        assert profile.has_capability("dev.ucp.shopping.checkout")
        assert profile.preferred_transport() == "mcp"

    def test_default_registry_parses_flat_stub(self):
        reg = get_default_registry()
        result = reg.parse(FLAT_STUB_PROFILE, "athletic-co.myshopify.com")
        assert result is not None
        assert result["merchant_domain"] == "athletic-co.myshopify.com"


# ── UCPProfileDiscovery integration (mocked HTTP) ────────────────────────────


class TestDiscoveryFetchReal:
    """Verifies _fetch_real uses the registry and performs version negotiation."""

    def _make_mock_http(self, responses: dict[str, dict]) -> MagicMock:
        """responses: {url: json_body}"""

        async def fake_get(url, **kw):
            body = responses.get(url)
            if body is None:
                r = MagicMock()
                r.status_code = 404
                return r
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = body
            return r

        client = MagicMock()
        client.get = fake_get
        return client

    def test_fetch_real_parses_2026_04_08_profile(self):
        from ucp.discovery import UCPProfileDiscovery
        from storage.db import DB
        import tempfile
        import os

        db_path = tempfile.mktemp(suffix=".db")
        try:
            db = DB(db_path)
            http = self._make_mock_http(
                {
                    "https://kith.com/.well-known/ucp": KITH_WIRE_PROFILE,
                }
            )
            disc = UCPProfileDiscovery(db, http_client=http)
            loop = asyncio.get_event_loop()
            profile = loop.run_until_complete(disc._fetch_real("kith.com"))

            assert profile is not None
            assert profile.merchant_domain == "kith.com"
            assert profile.has_capability("dev.ucp.shopping.checkout")
            assert profile.has_capability("dev.ucp.shopping.catalog.search")
            assert profile.preferred_transport() == "mcp"
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_fetch_real_negotiates_versioned_url(self):
        """When merchant advertises supported_versions, _fetch_real re-fetches
        the best matching versioned URL."""
        from ucp.discovery import UCPProfileDiscovery
        from storage.db import DB
        import tempfile
        import os

        versioned_profile = {
            "ucp": {
                "version": "2026-04-08",
                "capabilities": {
                    "dev.ucp.shopping.checkout": [
                        {
                            "version": "2026-04-08",
                            "spec": "https://ucp.dev/x",
                            "schema": "https://ucp.dev/s",
                        }
                    ]
                },
                "services": {
                    "dev.ucp.shopping": [
                        {
                            "transport": "mcp",
                            "endpoint": "https://kithnyc.myshopify.com/api/ucp/mcp",
                            "spec": "x",
                        }
                    ]
                },
                "payment_handlers": {},
            }
        }

        db_path = tempfile.mktemp(suffix=".db")
        try:
            db = DB(db_path)
            http = self._make_mock_http(
                {
                    "https://kith.com/.well-known/ucp": KITH_WIRE_PROFILE,
                    "https://kithnyc.myshopify.com/.well-known/ucp/2026-04-08": versioned_profile,
                }
            )
            disc = UCPProfileDiscovery(db, http_client=http)
            loop = asyncio.get_event_loop()
            profile = loop.run_until_complete(disc._fetch_real("kith.com"))

            assert profile is not None
            # Profile came from the versioned URL (1 capability, not 8)
            assert len(profile.capabilities) == 1
            assert profile.has_capability("dev.ucp.shopping.checkout")
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_fetch_real_returns_none_on_404(self):
        from ucp.discovery import UCPProfileDiscovery
        from storage.db import DB
        import tempfile
        import os

        db_path = tempfile.mktemp(suffix=".db")
        try:
            db = DB(db_path)
            http = self._make_mock_http({})  # no URLs registered → 404
            disc = UCPProfileDiscovery(db, http_client=http)
            loop = asyncio.get_event_loop()
            profile = loop.run_until_complete(disc._fetch_real("unknown.com"))
            assert profile is None
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_fetch_real_accepts_custom_registry(self):
        """parser_registry= param lets tests inject a custom registry."""
        from ucp.discovery import UCPProfileDiscovery
        from storage.db import DB
        import tempfile
        import os

        sentinel_result = {
            "merchant_domain": "custom.com",
            "capabilities": [],
            "services": [],
            "payment_handlers": [],
            "signing_keys": [],
        }

        class _SentinelParser:
            versions = ("2026-04-08",)

            def parse(self, data, domain):
                return {**sentinel_result, "merchant_domain": domain}

        custom_reg = ProfileParserRegistry()
        custom_reg.register(_SentinelParser())

        db_path = tempfile.mktemp(suffix=".db")
        try:
            db = DB(db_path)
            http = self._make_mock_http(
                {
                    "https://custom.com/.well-known/ucp": KITH_WIRE_PROFILE,
                }
            )
            disc = UCPProfileDiscovery(db, http_client=http, parser_registry=custom_reg)
            loop = asyncio.get_event_loop()
            profile = loop.run_until_complete(disc._fetch_real("custom.com"))
            assert profile is not None
            assert profile.merchant_domain == "custom.com"
            assert profile.capabilities == []
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)
