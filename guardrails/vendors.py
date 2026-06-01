"""VendorGate — allowlist/blocklist enforcement at the merchant boundary.

Sources of truth (checked in order):
1. UserProfile.vendor_blocklist  — absolute deny
2. UserProfile.vendor_allowlist  — if non-empty, only listed merchants allowed
3. AgentMandate.blocked_vendors  — per-mandate deny
4. AgentMandate.allowed_vendors  — per-mandate allow (if non-empty, restrict)
"""

from __future__ import annotations

from dataclasses import dataclass

from models.mandate import AgentMandate
from models.user import UserProfile


@dataclass
class VendorDecision:
    allowed: bool
    reason: str | None = None


class VendorGate:
    def __init__(self, user: UserProfile, mandate: AgentMandate | None = None):
        self.user = user
        self.mandate = mandate

    def check(self, merchant_domain: str) -> VendorDecision:
        d = merchant_domain.lower()

        if d in {v.lower() for v in self.user.vendor_blocklist}:
            # Per ARCHITECTURE: refuse silently — don't tell agent the reason.
            return VendorDecision(allowed=False, reason="user_blocklist")

        if self.user.vendor_allowlist:
            if d not in {v.lower() for v in self.user.vendor_allowlist}:
                return VendorDecision(allowed=False, reason="not_in_user_allowlist")

        if self.mandate:
            if d in {v.lower() for v in self.mandate.blocked_vendors}:
                return VendorDecision(allowed=False, reason="mandate_blocklist")
            if self.mandate.allowed_vendors:
                if d not in {v.lower() for v in self.mandate.allowed_vendors}:
                    return VendorDecision(allowed=False, reason="not_in_mandate_allowlist")

        return VendorDecision(allowed=True)
