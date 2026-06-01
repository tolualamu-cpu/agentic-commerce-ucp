"""Shared tools available to every agent.

Conventions:
  - Every tool is a plain async function taking ToolContext as the first arg.
  - Return types are Pydantic objects or dict — never raw SDK objects.
  - Side effects (DB writes) happen BEFORE the dependent action, never after.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from models.mandate import AuthResult
from storage.db import append_audit
from tools.context import ToolContext


async def audit_log(
    ctx: ToolContext,
    *,
    agent: str,
    tool: str,
    action: str,
    mandate_id: str | None = None,
    args: dict[str, Any] | None = None,
) -> None:
    """Write an immutable audit entry. Call this BEFORE the action it logs."""
    append_audit(ctx.db, agent=agent, tool=tool, action=action, mandate_id=mandate_id, args=args)


async def get_user_profile(ctx: ToolContext) -> dict[str, Any]:
    """Return an agent-safe view of the user profile (payment_method_id stripped)."""
    return ctx.user.agent_safe_view()


async def check_spending_limits(
    ctx: ToolContext,
    *,
    mandate_id: str,
    amount: Decimal,
) -> AuthResult:
    """Independent cap check (SpendingLimiter, not AP2).

    Agents can use this for headroom inspection before attempting a purchase.
    Note: PaymentGateway re-runs the full AP2 authorisation at purchase time —
    this is a pre-flight check only.
    """
    return ctx.spending_limiter.check(mandate_id=mandate_id, amount=Decimal(amount))


async def get_active_mandate_summary(
    ctx: ToolContext,
    *,
    mandate_id: str,
) -> dict[str, Any]:
    """Return the active mandate's caps + current spend in a single payload.

    This is the AUTHORITATIVE source for answering any user question about
    their spending limits. Never answer such questions from
    ``get_user_profile.budget`` — those fields are non-binding hints; the
    mandate's caps are the enforced limits.

    Returns:
      {
        "mandate_id": str,
        "status": "active" | "revoked" | "expired",
        "per_transaction_cap": str,
        "daily_cap": str,
        "spent_today": str,
        "daily_headroom": str,
        "monthly_cap": str,
        "spent_this_month": str,
        "monthly_headroom": str,
        "currency": str,
        "expiry": str (ISO timestamp),
        "allowed_categories": list[str],
        "allowed_vendors": list[str],
      }
    or {"error": "mandate_not_found"} if no such mandate.
    """
    mandate = ctx.ap2.get_mandate(mandate_id)
    if mandate is None:
        return {"error": "mandate_not_found"}
    now = datetime.now(timezone.utc)
    spent_day, spent_month = ctx.ap2._compute_spend(mandate_id, now)
    return {
        "mandate_id": mandate.mandate_id,
        "status": mandate.is_active(now).value,
        "per_transaction_cap": str(mandate.max_amount),
        "daily_cap": str(mandate.daily_cap),
        "spent_today": str(spent_day),
        "daily_headroom": str(mandate.daily_cap - spent_day),
        "monthly_cap": str(mandate.monthly_cap),
        "spent_this_month": str(spent_month),
        "monthly_headroom": str(mandate.monthly_cap - spent_month),
        "currency": mandate.currency,
        "expiry": mandate.expiry.isoformat(),
        "allowed_categories": list(mandate.allowed_categories),
        "allowed_vendors": list(mandate.allowed_vendors),
    }
