"""SpendingLimiter — DB-backed cap enforcement.

Independent of AP2 (which also enforces caps). Why both? Defence in depth: an agent
tool that bypasses the mandate engine still hits this guardrail before any payment
call lands. This is the L3 'structural enforcement' described in ARCHITECTURE.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from models.mandate import AuthResult
from storage.db import DB, MandateQ, SpendQ


class SpendingLimiter:
    def __init__(self, db: DB):
        self.db = db

    def check(self, mandate_id: str, amount: Decimal, now: datetime | None = None) -> AuthResult:
        amount = Decimal(amount)
        now = now or datetime.now(timezone.utc)

        m = self.db.mandates.get(MandateQ.mandate_id == mandate_id)
        if not m:
            return AuthResult(
                authorized=False,
                mandate_id=mandate_id,
                amount=amount,
                reason="mandate_not_found",
            )

        max_amount = Decimal(m["max_amount"])
        daily_cap = Decimal(m["daily_cap"])
        monthly_cap = Decimal(m["monthly_cap"])

        if amount > max_amount:
            return AuthResult(
                authorized=False,
                mandate_id=mandate_id,
                amount=amount,
                reason="exceeds_per_transaction_cap",
            )

        spent_day = Decimal("0")
        spent_month = Decimal("0")
        for r in self.db.spend_records.search(SpendQ.mandate_id == mandate_id):
            ts_str = r["timestamp"]
            if ts_str.endswith("Z"):
                ts_str = ts_str[:-1] + "+00:00"
            ts = datetime.fromisoformat(ts_str)
            amt = Decimal(r["amount"])
            if ts.year == now.year and ts.month == now.month:
                spent_month += amt
                if ts.date() == now.date():
                    spent_day += amt

        if spent_day + amount > daily_cap:
            return AuthResult(
                authorized=False,
                mandate_id=mandate_id,
                amount=amount,
                headroom_daily=daily_cap - spent_day,
                reason="exceeds_daily_cap",
            )
        if spent_month + amount > monthly_cap:
            return AuthResult(
                authorized=False,
                mandate_id=mandate_id,
                amount=amount,
                headroom_monthly=monthly_cap - spent_month,
                reason="exceeds_monthly_cap",
            )

        return AuthResult(
            authorized=True,
            mandate_id=mandate_id,
            amount=amount,
            headroom_per_tx=max_amount - amount,
            headroom_daily=daily_cap - spent_day - amount,
            headroom_monthly=monthly_cap - spent_month - amount,
        )
