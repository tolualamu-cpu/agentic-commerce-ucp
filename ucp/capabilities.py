"""UCP capability namespaces + negotiation.

Capability namespaces use reverse-domain convention (per the UCP spec).
The agent declares what it supports; the merchant declares what it supports;
the intersection determines what operations are available for that merchant.
"""

from __future__ import annotations

from dataclasses import dataclass

from models.ucp_profile import UCPProfile


class UCPCapability:
    # Core
    CHECKOUT = "dev.ucp.shopping.checkout"
    IDENTITY_LINKING = "dev.ucp.identity.linking"
    ORDER_MANAGEMENT = "dev.ucp.orders.management"
    PAYMENT_TOKENS = "dev.ucp.payments.token_exchange"
    # Extensions
    AP2_MANDATES = "dev.ucp.extensions.ap2"
    DISCOUNTS = "dev.ucp.extensions.discounts"
    FULFILLMENT = "dev.ucp.extensions.fulfillment"


AGENT_CAPABILITIES: tuple[str, ...] = (
    UCPCapability.CHECKOUT,
    UCPCapability.ORDER_MANAGEMENT,
    UCPCapability.PAYMENT_TOKENS,
    UCPCapability.AP2_MANDATES,
    UCPCapability.DISCOUNTS,
)


@dataclass
class NegotiationResult:
    merchant_domain: str
    shared: list[str]
    agent_only: list[str]
    merchant_only: list[str]

    def supports(self, namespace: str) -> bool:
        return namespace in self.shared


class CapabilityNegotiator:
    """Computes the intersection of agent + merchant capabilities."""

    def __init__(self, agent_capabilities: tuple[str, ...] = AGENT_CAPABILITIES):
        self.agent = set(agent_capabilities)

    def negotiate(self, profile: UCPProfile) -> NegotiationResult:
        merchant = {c.namespace for c in profile.capabilities}
        return NegotiationResult(
            merchant_domain=profile.merchant_domain,
            shared=sorted(self.agent & merchant),
            agent_only=sorted(self.agent - merchant),
            merchant_only=sorted(merchant - self.agent),
        )
