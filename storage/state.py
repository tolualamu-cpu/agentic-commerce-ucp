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
    # each item has product_id, name, price, currency, quantity, line_total.
    click_basket: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Product card sets shown in the chat UI, keyed by conversation turn count
    # at the time they were emitted. Used to re-render cards on page reload.
    # Each entry: {"turn_count": int, "products": list[dict]}
    product_card_sets: list[dict[str, Any]] = field(default_factory=list)
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
