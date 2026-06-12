"""UCP spec currency gate.

These tests verify that our parser registry can handle the wire format of
every known live merchant without falling back to the lenient "newest parser"
path. If any of these tests fail, a merchant has updated their spec version and
we need a new parser in ucp/parsers/.

Profile fixtures are stored in this file (captured from real merchants) so
tests run without network calls. The fixture must be refreshed when a merchant
upgrades their spec version — that refresh is itself the signal that a new
parser is needed.

How to add a new merchant:
  1. Capture: curl -s https://{domain}/.well-known/ucp | python3 -m json.tool
  2. Add the captured JSON as MERCHANT_PROFILES["{domain}"]
  3. Add the domain to KNOWN_LIVE_MERCHANTS
  4. If _detect_version returns a version not in registry.known_versions(),
     create ucp/parsers/vYYYY_MM_DD.py and register it in get_default_registry().
  5. Run this file — all tests must pass.

This file sorts before test_user_journeys.py ('u' + 'c' < 'u' + 's'), so
async tests must use loop.run_until_complete(). All tests here are sync.
"""

from __future__ import annotations

import pytest

from ucp.profile_parser import (
    _detect_version,
    get_default_registry,
)

# ── Stored merchant profiles (no network calls) ───────────────────────────────
# Each entry is the raw JSON body of /.well-known/ucp for that merchant.
# Refresh when the merchant publishes a new spec version.

MERCHANT_PROFILES: dict[str, dict] = {
    # Captured 2026-06
    "kith.com": {
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
                        "schema": "x",
                    }
                ],
                "dev.ucp.shopping.cart": [
                    {
                        "version": "2026-04-08",
                        "spec": "https://ucp.dev/2026-04-08/specification/cart",
                        "schema": "x",
                    }
                ],
                "dev.ucp.shopping.fulfillment": [
                    {
                        "version": "2026-04-08",
                        "spec": "https://ucp.dev/2026-04-08/specification/fulfillment",
                        "schema": "x",
                    }
                ],
                "dev.ucp.shopping.discount": [
                    {
                        "version": "2026-04-08",
                        "spec": "https://ucp.dev/2026-04-08/specification/discount",
                        "schema": "x",
                    }
                ],
                "dev.ucp.shopping.order": [
                    {
                        "version": "2026-04-08",
                        "spec": "https://ucp.dev/2026-04-08/specs/shopping/order",
                        "schema": "x",
                    }
                ],
                "dev.ucp.shopping.catalog.search": [
                    {
                        "version": "2026-04-08",
                        "spec": "https://ucp.dev/2026-04-08/specification/catalog",
                        "schema": "x",
                    }
                ],
                "dev.ucp.shopping.catalog.lookup": [
                    {
                        "version": "2026-04-08",
                        "spec": "https://ucp.dev/2026-04-08/specification/catalog",
                        "schema": "x",
                    }
                ],
                "dev.shopify.catalog": [
                    {
                        "version": "2026-04-08",
                        "spec": "https://shopify.dev/docs/agents/catalog/storefront-catalog",
                        "schema": "x",
                    }
                ],
            },
            "payment_handlers": {
                "com.google.pay": [
                    {
                        "id": "gpay",
                        "version": "2026-01-11",
                        "spec": "https://pay.google.com/gp/p/ucp/2026-01-11/",
                        "schema": "x",
                        "config": {},
                    }
                ],
                "dev.shopify.card": [
                    {
                        "id": "shopify.card",
                        "version": "2026-01-15",
                        "spec": "https://ucp.dev/specification/payment-handler-guide",
                        "schema": "x",
                        "config": {},
                    }
                ],
                "dev.shopify.shop_pay": [
                    {
                        "id": "shop_pay",
                        "version": "2026-04-08",
                        "spec": "https://shopify.dev/ucp/shop-pay-handler/2026-04-08/spec.md",
                        "schema": "x",
                        "config": {},
                    }
                ],
            },
        }
    },
    # Add new merchants here as they are onboarded.
}

# Every domain that must parse without falling back to the lenient path.
# Adding to LIVE_MERCHANTS in catalogue.py without updating this list is
# caught by test_all_live_merchants_have_currency_fixture below.
KNOWN_LIVE_MERCHANTS = list(MERCHANT_PROFILES.keys())


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestSpecCurrency:
    @pytest.mark.parametrize("domain", KNOWN_LIVE_MERCHANTS)
    def test_merchant_profile_parses_without_fallback(self, domain):
        """Registry must have an EXACT version match for each live merchant's profile.

        If this test fails:
          1. Refresh MERCHANT_PROFILES[domain] with the current /.well-known/ucp JSON.
          2. Check the new version string against registry.known_versions().
          3. If it's a new version, add ucp/parsers/vYYYY_MM_DD.py + register it.
        """
        reg = get_default_registry()
        data = MERCHANT_PROFILES[domain]
        version = _detect_version(data)

        assert version is not None, (
            f"{domain}: profile has no detectable version string. "
            "Update the fixture and check the format."
        )
        assert version in reg.known_versions(), (
            f"{domain} publishes UCP version '{version}' but we have no parser for it. "
            f"Known versions: {reg.known_versions()}. "
            "Add ucp/parsers/v{version.replace('-', '_')}.py and register it in "
            "get_default_registry()."
        )

    @pytest.mark.parametrize("domain", KNOWN_LIVE_MERCHANTS)
    def test_merchant_profile_produces_valid_ucp_profile(self, domain):
        """Parser output must be accepted by UCPProfile without validation errors."""
        from models.ucp_profile import UCPProfile

        reg = get_default_registry()
        data = MERCHANT_PROFILES[domain]
        result = reg.parse(data, domain)

        assert result is not None, f"{domain}: registry.parse() returned None"
        profile = UCPProfile(**result)  # raises on schema mismatch
        assert profile.merchant_domain == domain

    @pytest.mark.parametrize("domain", KNOWN_LIVE_MERCHANTS)
    def test_merchant_profile_has_at_least_one_capability(self, domain):
        """A valid live UCP merchant must advertise at least one capability."""
        from models.ucp_profile import UCPProfile

        reg = get_default_registry()
        result = reg.parse(MERCHANT_PROFILES[domain], domain)
        profile = UCPProfile(**result)
        assert len(profile.capabilities) > 0, (
            f"{domain}: profile parsed to zero capabilities — check the fixture and parser mapping."
        )

    @pytest.mark.parametrize("domain", KNOWN_LIVE_MERCHANTS)
    def test_merchant_profile_has_at_least_one_service(self, domain):
        """A valid live UCP merchant must expose at least one supported transport."""
        from models.ucp_profile import UCPProfile

        reg = get_default_registry()
        result = reg.parse(MERCHANT_PROFILES[domain], domain)
        profile = UCPProfile(**result)
        assert len(profile.services) > 0, (
            f"{domain}: profile has no supported transport services — "
            "check that the parser doesn't filter all transports."
        )

    def test_all_live_merchants_have_currency_fixture(self):
        """Every merchant in LIVE_MERCHANTS must have a fixture in MERCHANT_PROFILES.

        This test bridges catalogue.py and this file: if someone adds a merchant
        to LIVE_MERCHANTS without adding a fixture here, this test fails and
        prompts them to capture the profile and verify the parser.
        """
        from config.catalogue import LIVE_MERCHANTS

        missing = [domain for domain in LIVE_MERCHANTS if domain not in MERCHANT_PROFILES]
        assert not missing, (
            f"These live merchants have no spec-currency fixture: {missing}. "
            "Capture their /.well-known/ucp JSON and add to MERCHANT_PROFILES "
            "in tests/test_ucp_spec_currency.py."
        )
