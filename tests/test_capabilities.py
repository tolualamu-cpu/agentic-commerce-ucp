"""CapabilityNegotiator: intersection logic."""

from __future__ import annotations

from models.ucp_profile import UCPCapabilityDeclaration, UCPProfile
from ucp.capabilities import AGENT_CAPABILITIES, CapabilityNegotiator, UCPCapability


def _profile(*namespaces: str) -> UCPProfile:
    caps = [
        UCPCapabilityDeclaration(
            namespace=n, version="2025-01-15", spec_url=f"https://ucp.dev/spec/{n}"
        )
        for n in namespaces
    ]
    return UCPProfile(merchant_domain="x.com", capabilities=caps)


def test_full_match():
    p = _profile(*AGENT_CAPABILITIES)
    r = CapabilityNegotiator().negotiate(p)
    assert set(r.shared) == set(AGENT_CAPABILITIES)
    assert r.agent_only == []
    assert r.merchant_only == []


def test_partial_match():
    p = _profile(UCPCapability.CHECKOUT, UCPCapability.ORDER_MANAGEMENT)
    r = CapabilityNegotiator().negotiate(p)
    assert UCPCapability.CHECKOUT in r.shared
    assert UCPCapability.AP2_MANDATES in r.agent_only


def test_merchant_only_capabilities_tracked():
    p = _profile("dev.ucp.fancy.custom_thing", UCPCapability.CHECKOUT)
    r = CapabilityNegotiator().negotiate(p)
    assert "dev.ucp.fancy.custom_thing" in r.merchant_only
    assert r.supports(UCPCapability.CHECKOUT)


def test_empty_profile():
    r = CapabilityNegotiator().negotiate(UCPProfile(merchant_domain="x.com"))
    assert r.shared == []
