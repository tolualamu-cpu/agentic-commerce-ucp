"""Parser for the flat/stub UCP profile format.

This format is used by:
  - Our internal merchant_profiles.json stubs
  - Any older merchant profile that lacks a versioned wrapper

Shape (flat, fields match UCPProfile directly):
    {
        "merchant_domain": "example.com",
        "capabilities": [{"namespace": "...", "version": "...", "spec_url": "..."}],
        "services":      [{"type": "rest", "base_url": "...", "spec_url": "..."}],
        "payment_handlers": [{"id": "...", "name": "...", "spec_url": "..."}],
        "signing_keys": []
    }
"""

from __future__ import annotations


class VFlatParser:
    """Handles profiles that have no 'ucp' root wrapper (stubs and pre-2026 flat format)."""

    # versions = () means this parser handles None (no detected version).
    # The registry routes here when _detect_version returns None.
    versions: tuple[str, ...] = ()

    def parse(self, data: dict, merchant_domain: str) -> dict:
        # Drop comment-only keys (used in merchant_profiles.json for documentation)
        clean = {k: v for k, v in data.items() if not k.startswith("_")}
        clean.setdefault("merchant_domain", merchant_domain)
        return clean
