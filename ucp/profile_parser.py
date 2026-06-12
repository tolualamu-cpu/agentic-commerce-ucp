"""Versioned UCP profile parser registry.

Decouples wire-format parsing from the rest of the system so that new spec
versions only require adding a parser file and registering it here — no
changes to discovery.py, the UCPProfile model, or the gateway.

Resolution order inside parse():
  1. Detect the version string in the raw response.
  2. Exact match in the registry  →  use that parser.
  3. No match but a version string exists  →  use the newest known parser
     (lenient forward-compatibility: best-effort rather than silent empty profile).
  4. No version string detected  →  flat/stub format parser.

Adding support for a new spec version:
  1. Create ucp/parsers/vYYYY_MM_DD.py with a class that has:
       versions: tuple[str, ...] = ("YYYY-MM-DD",)
       def parse(self, data: dict, merchant_domain: str) -> dict: ...
  2. Register it in get_default_registry() below.
  Done — nothing else changes.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


# ── Protocol ──────────────────────────────────────────────────────────────────


@runtime_checkable
class ProfileParser(Protocol):
    """Contract every versioned parser must satisfy."""

    #: Spec version strings this parser handles, e.g. ("2026-04-08",).
    #: Empty tuple means "handles the flat/no-version format".
    versions: tuple[str, ...]

    def parse(self, data: dict, merchant_domain: str) -> dict:
        """Translate raw wire-format JSON into a flat dict suitable for
        UCPProfile(**result). Must always return a dict (never raise)."""
        ...


# ── Helpers ───────────────────────────────────────────────────────────────────


def _detect_version(data: dict) -> str | None:
    """Return the UCP spec version string from raw profile JSON, or None.

    Handles both the 2026-04-08 wrapped format {"ucp": {"version": "..."}}
    and hypothetical flat formats {"version": "..."}.
    """
    if isinstance(data.get("ucp"), dict):
        return data["ucp"].get("version")
    return data.get("version")


def _extract_supported_versions(data: dict) -> dict[str, str]:
    """Return the supported_versions map {version: url} from a raw profile, or {}."""
    if isinstance(data.get("ucp"), dict):
        return data["ucp"].get("supported_versions", {})
    return {}


# ── Registry ──────────────────────────────────────────────────────────────────


class ProfileParserRegistry:
    """Maps UCP spec version strings to their parsers.

    Thread-safe for reads after initial registration; do not mutate after
    the application starts serving requests.
    """

    def __init__(self) -> None:
        # version string → parser (e.g. "2026-04-08" → V2026_04_08Parser)
        self._versioned: dict[str, ProfileParser] = {}
        # parser for flat/no-version format
        self._flat: ProfileParser | None = None

    def register(self, parser: ProfileParser) -> None:
        """Register a parser for the version strings it declares."""
        if not parser.versions:
            # Empty versions tuple = flat/stub format parser
            self._flat = parser
        else:
            for v in parser.versions:
                self._versioned[v] = parser

    def parse(self, data: dict, merchant_domain: str) -> dict | None:
        """Normalise raw profile JSON to a flat UCPProfile-compatible dict.

        Returns None only if no parser is registered at all.
        """
        version = _detect_version(data)

        if version is not None:
            parser = self._versioned.get(version)
            if parser is None:
                # Unknown future version — try newest known (lenient)
                parser = self._newest_versioned_parser()
        else:
            parser = self._flat

        if parser is None:
            return None
        return parser.parse(data, merchant_domain)

    def negotiate_version_url(self, supported: dict[str, str]) -> str | None:
        """Given a merchant's supported_versions map, return the URL for the
        highest spec version we have a parser for, or None.

        Used by _fetch_real to re-fetch a version-specific profile endpoint
        instead of parsing the default root profile.
        """
        for version in self._versions_newest_first():
            if version in supported:
                return supported[version]
        return None

    def known_versions(self) -> list[str]:
        """Sorted list of registered version strings, newest first."""
        return self._versions_newest_first()

    # ── private ──────────────────────────────────────────────────────────

    def _newest_versioned_parser(self) -> ProfileParser | None:
        versions = self._versions_newest_first()
        return self._versioned[versions[0]] if versions else None

    def _versions_newest_first(self) -> list[str]:
        # Version strings are YYYY-MM-DD so reverse-lexicographic = newest first
        return sorted(self._versioned.keys(), reverse=True)


# ── Default registry (module singleton) ───────────────────────────────────────

_default_registry: ProfileParserRegistry | None = None


def get_default_registry() -> ProfileParserRegistry:
    """Return the shared application-wide parser registry.

    To add a new spec version:
      1. Create ucp/parsers/vYYYY_MM_DD.py
      2. Import and register it in the block below
    """
    global _default_registry
    if _default_registry is None:
        from ucp.parsers.v2026_04_08 import V2026_04_08Parser
        from ucp.parsers.v_flat import VFlatParser

        reg = ProfileParserRegistry()
        reg.register(V2026_04_08Parser())
        reg.register(VFlatParser())
        _default_registry = reg
    return _default_registry
