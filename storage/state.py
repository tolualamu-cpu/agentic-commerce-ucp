"""SessionState — per-conversation working memory for the Orchestrator.

This is in-memory only. Persistent state lives in DB.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class SessionState:
    user_id: str
    active_mandate_id: str | None = None
    open_checkout_sessions: dict[str, str] = field(
        default_factory=dict
    )  # merchant_domain -> session_id
    conversation: list[dict[str, Any]] = field(default_factory=list)
    last_discovered_products: list[dict[str, Any]] = field(default_factory=list)
    # Draft cart populated either by the user's click-Add-to-Cart action
    # or by the agent's ``add_to_cart`` tool. Distinct from the gate's
    # purchase basket: items here do NOT trigger payment until the user
    # explicitly buys. Shape: {merchant_domain: [item_dict, ...]} where
    # each item has product_id, variant_id (str | None), name, price,
    # currency, quantity, line_total, selected_options (dict[str, str],
    # empty for no-variant products). Line identity is the composite key
    # (product_id, variant_id) — two variants of the same product are two
    # separate lines; variant_id=None preserves single-SKU behavior.
    click_basket: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Product card sets shown in the chat UI, keyed by conversation turn count
    # at the time they were emitted. Used to re-render cards on page reload.
    # Each entry: {"turn_count": int, "products": list[dict]}
    product_card_sets: list[dict[str, Any]] = field(default_factory=list)
    # Products the agent has explicitly asked the UI to (re-)render as cards
    # via the ``show_product_cards`` tool — WITHOUT re-running discovery.
    # Drained by the web layer (chat.py) after each run: it emits a
    # ``products`` SSE event and then clears this list. Keeping it separate
    # from ``last_discovered_products`` means a re-show turn never mutates
    # the discovery cache or triggers a search.
    cards_to_show: list[dict[str, Any]] = field(default_factory=list)
    # Multi-listing "product families" (see agents.product_grouping) found
    # during the most recent discovery, keyed by the family's primary
    # ``product_id`` (the id shown as the single card). Each value is a
    # ``ProductFamily.model_dump()`` dict. Only families with >1 member are
    # stored — family-of-1 products (the common case) have nothing to
    # resolve here and are looked up directly via get_product_details.
    # Used by ``_add_to_cart``/``_get_product_variants`` to resolve
    # family-synthesized ``variant_id``s of the form
    # "{member_product_id}:{member_variant_id}" back to the underlying
    # member product + variant. Reset on each new discovery run.
    product_families: dict[str, dict[str, Any]] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def append_user(self, content: str) -> None:
        self.conversation.append({"role": "user", "content": content})

    def append_assistant(self, content: str) -> None:
        self.conversation.append({"role": "assistant", "content": content})

    def set_open_session(self, merchant_domain: str, session_id: str) -> None:
        self.open_checkout_sessions[merchant_domain] = session_id

    def get_open_session(self, merchant_domain: str) -> str | None:
        return self.open_checkout_sessions.get(merchant_domain)

    def clear_open_session(self, merchant_domain: str) -> None:
        self.open_checkout_sessions.pop(merchant_domain, None)
