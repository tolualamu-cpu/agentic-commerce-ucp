"""Parser for UCP spec version 2026-04-08.

Wire format published by merchants (e.g. Kith at /.well-known/ucp):

    {
      "ucp": {
        "version": "2026-04-08",
        "supported_versions": { "2026-04-08": "https://...", ... },
        "services": {
          "dev.ucp.shopping": [
            {"transport": "mcp", "endpoint": "https://...", "spec": "...", "schema": "..."},
            {"transport": "embedded", ...}
          ]
        },
        "capabilities": {
          "dev.ucp.shopping.checkout": [
            {"version": "2026-04-08", "spec": "...", "schema": "..."}
          ],
          "dev.ucp.shopping.catalog.search": [...],
          ...
        },
        "payment_handlers": {
          "com.google.pay":       [{"id": "gpay",      "version": "...", "spec": "...", "config": {...}}],
          "dev.shopify.shop_pay": [{"id": "shop_pay",  ...}],
          "dev.shopify.card":     [{"id": "shopify.card", ...}]
        }
      }
    }

Key differences from our internal (flat) model:
  - Everything nested inside a "ucp" root key
  - capabilities / services / payment_handlers are dicts keyed by namespace,
    each value is a list of version declarations
  - "transport" (not "type"), "endpoint" (not "base_url"), "spec" (not "spec_url")
  - Transports we have no client for ("embedded") are dropped during normalisation
"""

from __future__ import annotations

# Transports this codebase can actually route to. "embedded" is declared by
# some merchants (e.g. Kith) but we have no UCPEmbeddedClient yet — drop it
# so it never causes a spurious routing attempt.
_SUPPORTED_TRANSPORTS = frozenset({"rest", "mcp", "a2a"})


class V2026_04_08Parser:
    versions: tuple[str, ...] = ("2026-04-08",)

    def parse(self, data: dict, merchant_domain: str) -> dict:
        inner = data["ucp"]
        return {
            "merchant_domain": merchant_domain,
            "capabilities": self._unpack_caps(inner.get("capabilities", {})),
            "services": self._unpack_services(inner.get("services", {})),
            "payment_handlers": self._unpack_handlers(inner.get("payment_handlers", {})),
            "signing_keys": inner.get("signing_keys", []),
        }

    # ── private helpers ───────────────────────────────────────────────────

    def _unpack_caps(self, caps: dict) -> list[dict]:
        """dict-of-lists  →  flat list of UCPCapabilityDeclaration dicts."""
        result = []
        for namespace, versions in caps.items():
            for v in versions or []:
                result.append(
                    {
                        "namespace": namespace,
                        "version": v.get("version", ""),
                        # wire uses "spec"; internal model uses "spec_url"
                        "spec_url": v.get("spec") or v.get("spec_url", ""),
                        "schema_url": v.get("schema"),
                    }
                )
        return result

    def _unpack_services(self, services: dict) -> list[dict]:
        """dict-of-lists  →  flat list of UCPService dicts.

        Drops transports we have no implementation for (e.g. "embedded").
        """
        result = []
        for _svc_namespace, transports in services.items():
            for t in transports or []:
                transport_type = t.get("transport", "")
                if transport_type not in _SUPPORTED_TRANSPORTS:
                    continue
                result.append(
                    {
                        # wire uses "transport"; internal model uses "type"
                        "type": transport_type,
                        "spec_url": t.get("spec") or t.get("spec_url", ""),
                        # wire uses "endpoint"; internal model uses "base_url"
                        "base_url": t.get("endpoint") or t.get("base_url"),
                    }
                )
        return result

    def _unpack_handlers(self, handlers: dict) -> list[dict]:
        """dict-of-lists  →  flat list of PaymentHandler dicts.

        The handler namespace key (e.g. "com.google.pay") is used as the
        display name when no explicit name field is present in the config.
        """
        result = []
        for handler_ns, configs in handlers.items():
            for h in configs or []:
                result.append(
                    {
                        "id": h.get("id", handler_ns),
                        # synthesise name from namespace if absent
                        "name": h.get("name", handler_ns),
                        "spec_url": h.get("spec") or h.get("spec_url", ""),
                    }
                )
        return result
