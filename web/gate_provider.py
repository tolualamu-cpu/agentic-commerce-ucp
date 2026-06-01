"""WebsocketConfirmProvider — bridges the orchestrator's HITL gate to
a browser via WebSocket.

Implements the same ``ConfirmationProvider`` Protocol as ``RichConfirmProvider``
(CLI) and ``AutoConfirmProvider`` (tests). The orchestrator doesn't know
which provider is in use — it just calls ``soft_confirm``/``explicit_confirm``
and waits.

Flow:
  1. Orchestrator calls ``explicit_confirm(gate)``
  2. Provider puts a "gate.open" event on ``outbox``
  3. WebSocket router drains ``outbox`` and sends to browser
  4. Browser renders the gate modal; user clicks CONFIRM or types a question
  5. WebSocket router pushes the user's reply onto ``inbox``
  6. Provider pops from ``inbox``, returns ``GateResponse`` to orchestrator

Safety: a timeout on ``inbox.get()`` prevents the orchestrator from
deadlocking if the browser disconnects mid-gate.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from cli.confirmation import GateData, GateResponse


# Maximum time we'll wait for a browser reply before treating the gate as
# cancelled. Generous because users may genuinely think about a purchase.
GATE_REPLY_TIMEOUT_S = 300  # 5 minutes


class WebsocketConfirmProvider:
    """Async confirmation provider backed by a WebSocket pipe.

    Decoupled from the WebSocket framework — the router pulls from
    ``outbox`` and pushes to ``inbox``. This keeps the provider trivially
    testable without spinning up FastAPI.
    """

    def __init__(self) -> None:
        # Lazily created on first access — Python 3.9 requires a running
        # event loop to construct an asyncio.Queue.
        self._outbox: "asyncio.Queue[dict] | None" = None
        self._inbox: "asyncio.Queue[dict] | None" = None
        # True while ``_present`` is awaiting input — set BEFORE the
        # await on inbox.get() and cleared AFTER. The web layer's
        # POST /chat handler reads this to decide whether to route a
        # user's chat message to the gate's inbox (when True) instead
        # of starting a new orchestrator run (which would deadlock on
        # ``orchestrator_lock``). See Phase 8g plan.
        self.awaiting_input: bool = False

    @property
    def outbox(self) -> "asyncio.Queue[dict]":
        if self._outbox is None:
            self._outbox = asyncio.Queue()
        return self._outbox

    @property
    def inbox(self) -> "asyncio.Queue[dict]":
        if self._inbox is None:
            self._inbox = asyncio.Queue()
        return self._inbox

    async def soft_confirm(self, gate: GateData) -> GateResponse:
        return await self._present(gate, tier="soft")

    async def explicit_confirm(self, gate: GateData) -> GateResponse:
        return await self._present(gate, tier="explicit")

    async def _present(self, gate: GateData, *, tier: str) -> GateResponse:
        # CRITICAL: drain any stale replies left over from a previous gate
        # or from disconnect-triggered synthetic cancels. Without this,
        # a reconnect during navigation would leave a leftover {"decision":
        # "cancel"} in the queue and the very next gate would auto-abort
        # before the user can click.
        while not self.inbox.empty():
            try:
                self.inbox.get_nowait()
            except asyncio.QueueEmpty:
                break

        await self.outbox.put(
            {
                "type": "gate.open",
                "tier": tier,
                "gate": _gate_to_dict(gate),
            }
        )
        # Flag that we're now blocking on user input. The web layer
        # checks this to route chat-input POSTs onto our inbox instead
        # of starting a new orchestrator run (which would deadlock).
        self.awaiting_input = True
        try:
            reply = await asyncio.wait_for(
                self.inbox.get(),
                timeout=GATE_REPLY_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            # No response within the window — treat as cancel. The
            # orchestrator's loop will exit cleanly and audit the cancel.
            return GateResponse(decision="cancel")
        finally:
            # Cleared after we read a reply (or threw / timed out).
            # The next gate iteration will set it again before its own
            # inbox.get().
            self.awaiting_input = False

        decision = (reply.get("decision") or "").strip().lower()
        text = reply.get("text", "") or ""
        if decision == "confirm":
            return GateResponse(decision="confirm")
        if decision == "cancel":
            return GateResponse(decision="cancel")
        # Anything else is treated as a question — same shape as the
        # RichConfirmProvider trichotomy.
        return GateResponse(decision="question", text=text or "(empty input)")


def _gate_to_dict(gate: GateData) -> dict[str, Any]:
    """JSON-safe view of GateData for transmission to the browser."""
    return {
        "merchant_domain": gate.merchant_domain,
        "amount": str(gate.amount),
        "currency": gate.currency,
        "item_summary": gate.item_summary,
        "items": [_item_to_dict(i) for i in (gate.items or [])],
        "full_summary": gate.full_summary,
        "risk_flags": list(gate.risk_flags or []),
        "confidence_score": gate.confidence_score,
        # Phase 8f hint to the web modal: stay hidden when True.
        "is_answer_only": bool(getattr(gate, "is_answer_only", False)),
    }


def _item_to_dict(item: dict) -> dict[str, Any]:
    """Items are already plain dicts, but coerce Decimal to string for JSON."""
    out: dict[str, Any] = {}
    for k, v in item.items():
        out[k] = str(v) if isinstance(v, Decimal) else v
    return out
