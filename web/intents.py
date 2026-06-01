"""Shared helpers for routing click-actions through the existing gate logic.

Every click in the UI produces a synthesised note in
``session.conversation`` so the agent remains aware of what the user did
between chat turns. Combined with the gate provider's question/confirm
channel, this gives us **one unified action handler** — clicks and chat
both end up at the same gate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.context import ToolContext


def append_click_note(ctx: "ToolContext", note: str) -> None:
    """Record a UI-originated action in the orchestrator's conversation
    history so the agent sees it on its next turn.

    Format mirrors how ``_buffer_gate_qa`` shapes its entries: a synthesised
    user turn tagged with `[via UI click]` so the model can distinguish it
    from human-typed text and reason about it accordingly.
    """
    ctx.session.conversation.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"[via UI click] {note} (at {datetime.now(timezone.utc).isoformat()})",
                }
            ],
        }
    )
