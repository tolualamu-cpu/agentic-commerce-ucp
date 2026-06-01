"""HITL confirmation primitives — the abstract interface, two impls.

Why an interface:
  - Tests inject ``AutoConfirmProvider`` (always yes / always no) for deterministic
    end-to-end testing without stdin.
  - Phase 4 CLI injects a ``RichConfirmProvider`` that draws Rich panels and reads
    from the terminal.
  - A future web/mobile UI could inject a websocket-backed provider.

This file deliberately has zero Rich / stdin dependencies. Phase 4 builds the
Rich-based concrete implementation in ``cli/display.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol


@dataclass
class GateData:
    """Everything a confirmation UI needs to render the gate."""

    merchant_domain: str
    amount: Decimal
    currency: str
    item_summary: str  # one-line description for soft confirm
    full_summary: str | None = None  # multi-line for >$500 confirm
    risk_flags: list[str] | None = None
    confidence_score: float | None = None
    # Multi-item basket — each dict has {name, quantity, price, line_total}
    items: list[dict] | None = None
    # Phase 8f UI hint: True when this gate event is being re-presented
    # after a NON-mutating Q&A iteration (basket unchanged; the agent
    # just answered a question). Web modal uses this to STAY HIDDEN
    # rather than reopen — the user's already reading the answer in
    # chat. False / None for ordinary gate prompts and for mutation
    # re-presentations (modal shows the updated basket).
    is_answer_only: bool = False


@dataclass
class GateResponse:
    """The richer reply from a gate prompt.

    ``decision`` is the trichotomy:
      - "confirm"   → user typed CONFIRM (case-insensitive). Proceed.
      - "cancel"    → user typed cancel/no/stop/empty. Abort.
      - "question"  → user typed something else. Treat as a question to answer;
                       the orchestrator should re-present the gate afterward.
    ``text`` is the raw user input — populated for "question", optional otherwise.
    """

    decision: Literal["confirm", "cancel", "question"]
    text: str = ""


class ConfirmationProvider(Protocol):
    """Phase 4 + tests implement this."""

    async def soft_confirm(self, gate: GateData) -> GateResponse: ...
    async def explicit_confirm(self, gate: GateData) -> GateResponse: ...


# ─── Test-only auto-confirm provider ────────────────────────────────────────


class AutoConfirmProvider:
    """For tests. Always returns the configured answer.

    Records every gate it was asked about so tests can assert on tier + amount.
    Optionally returns a scripted sequence of question/confirm responses so a
    test can simulate "user asks Q at gate, then confirms".
    """

    def __init__(
        self,
        *,
        soft: bool = True,
        explicit: bool = True,
        scripted: list[GateResponse] | None = None,
    ):
        self._soft = soft
        self._explicit = explicit
        self._scripted = list(scripted) if scripted else None
        self.gates_seen: list[tuple[str, GateData]] = []  # (tier, data)

    def _next_or_default(self, default: bool) -> GateResponse:
        if self._scripted:
            return self._scripted.pop(0)
        return GateResponse(decision="confirm" if default else "cancel")

    async def soft_confirm(self, gate: GateData) -> GateResponse:
        self.gates_seen.append(("soft", gate))
        return self._next_or_default(self._soft)

    async def explicit_confirm(self, gate: GateData) -> GateResponse:
        self.gates_seen.append(("explicit", gate))
        return self._next_or_default(self._explicit)


# ─── Tier classifier (used by the Orchestrator) ─────────────────────────────


def classify_gate(
    amount: Decimal,
    *,
    confidence_score: float = 1.0,
    confidence_threshold: float = 0.8,
    soft_max: Decimal = Decimal("30"),
    explicit_min: Decimal = Decimal("100"),
    full_summary_min: Decimal = Decimal("500"),
    is_first_purchase_from_merchant: bool = False,
) -> str:
    """Returns the required gate tier: 'soft' | 'explicit' | 'explicit_with_summary'.

    Per ARCHITECTURE.md HITL gate rules. Confidence below threshold upgrades
    any gate to explicit_with_summary regardless of amount.
    """
    if confidence_score < confidence_threshold:
        return "explicit_with_summary"
    if amount > full_summary_min:
        return "explicit_with_summary"
    if amount > explicit_min or is_first_purchase_from_merchant:
        return "explicit"
    if amount <= soft_max:
        return "soft"
    # Default for amounts in [$30, $100] not from a new merchant
    return "explicit"
