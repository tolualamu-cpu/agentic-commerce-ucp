"""AP2MandateEngine — local, HMAC-SHA256 signed spending mandates.

In the MVP this runs entirely client-side: no merchant-side AP2 support required.
When merchants ship ``dev.ucp.extensions.ap2``, mandate-proof headers can be added
to outgoing UCP requests via ``present_mandate_proof`` — the engine itself stays.

Key safety properties:
- HMAC over a canonical field set; any tampering invalidates the signature
- Spending caps (per-tx, daily, monthly) re-checked from DB on every authorise call
- Mandate revocation is instant (DB flag)
- payment_method_id lives on the mandate row but never leaves this layer
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from models.mandate import AgentMandate, AuthResult, MandateStatus, SpendRecord
from storage.db import DB, MandateQ, SpendQ


def _canonical_json(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _parse_dt(s: str) -> datetime:
    """Tolerant ISO-8601 parser — accepts trailing Z (py3.9 fromisoformat does not)."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


class AP2MandateEngine:
    """Creates, verifies, and tracks spending against AP2 mandates."""

    def __init__(self, db: DB, signing_key: str):
        if not signing_key:
            raise ValueError("AP2 signing key required")
        # Accept either hex or raw bytes-like string
        try:
            self._key = bytes.fromhex(signing_key)
        except ValueError:
            self._key = signing_key.encode("utf-8")
        self.db = db

    # ── creation ──────────────────────────────────────────────────────────────

    def create_mandate(
        self,
        user_id: str,
        *,
        max_amount: Decimal,
        daily_cap: Decimal,
        monthly_cap: Decimal,
        allowed_categories: Iterable[str] = (),
        allowed_vendors: Iterable[str] = (),
        blocked_vendors: Iterable[str] = (),
        currency: str = "USD",
        expiry_hours: int = 24,
        payment_method_id: str | None = None,
    ) -> AgentMandate:
        now = datetime.now(timezone.utc)
        mandate = AgentMandate(
            mandate_id=f"mandate_{uuid.uuid4().hex[:16]}",
            user_id=user_id,
            max_amount=Decimal(max_amount),
            daily_cap=Decimal(daily_cap),
            monthly_cap=Decimal(monthly_cap),
            allowed_categories=list(allowed_categories),
            allowed_vendors=list(allowed_vendors),
            blocked_vendors=list(blocked_vendors),
            currency=currency,
            created_at=now,
            expiry=now + timedelta(hours=expiry_hours),
            payment_method_id=payment_method_id,
        )
        mandate.digital_signature = self._sign(mandate)
        self.db.mandates.insert(mandate.model_dump(mode="json"))
        return mandate

    # ── retrieval ─────────────────────────────────────────────────────────────

    def get_mandate(self, mandate_id: str) -> AgentMandate | None:
        row = self.db.mandates.get(MandateQ.mandate_id == mandate_id)
        if not row:
            return None
        return AgentMandate.model_validate(row)

    # ── verification ──────────────────────────────────────────────────────────

    def verify_signature(self, mandate: AgentMandate) -> bool:
        if not mandate.digital_signature:
            return False
        expected = self._sign(mandate)
        return hmac.compare_digest(expected, mandate.digital_signature)

    def _sign(self, mandate: AgentMandate) -> str:
        msg = _canonical_json(mandate.signed_fields())
        return hmac.new(self._key, msg, hashlib.sha256).hexdigest()

    # ── authorisation ─────────────────────────────────────────────────────────

    def verify_and_authorize(
        self,
        mandate_id: str,
        amount: Decimal,
        vendor: str | None = None,
        category: str | None = None,
        now: datetime | None = None,
    ) -> AuthResult:
        amount = Decimal(amount)
        now = now or datetime.now(timezone.utc)

        mandate = self.get_mandate(mandate_id)
        if mandate is None:
            return AuthResult(
                authorized=False,
                mandate_id=mandate_id,
                amount=amount,
                reason="mandate_not_found",
            )

        if not self.verify_signature(mandate):
            return AuthResult(
                authorized=False,
                mandate_id=mandate_id,
                amount=amount,
                reason="invalid_signature",
            )

        status = mandate.is_active(now)
        if status == MandateStatus.REVOKED:
            return AuthResult(
                authorized=False,
                mandate_id=mandate_id,
                amount=amount,
                reason="mandate_revoked",
            )
        if status == MandateStatus.EXPIRED:
            return AuthResult(
                authorized=False,
                mandate_id=mandate_id,
                amount=amount,
                reason="mandate_expired",
            )

        # Vendor checks
        if vendor and mandate.blocked_vendors and vendor in mandate.blocked_vendors:
            return AuthResult(
                authorized=False,
                mandate_id=mandate_id,
                amount=amount,
                vendor=vendor,
                reason="vendor_blocked",
            )
        if vendor and mandate.allowed_vendors and vendor not in mandate.allowed_vendors:
            return AuthResult(
                authorized=False,
                mandate_id=mandate_id,
                amount=amount,
                vendor=vendor,
                reason="vendor_not_allowed",
            )

        # Category check
        if category and mandate.allowed_categories and category not in mandate.allowed_categories:
            return AuthResult(
                authorized=False,
                mandate_id=mandate_id,
                amount=amount,
                category=category,
                reason="category_not_allowed",
            )

        # Per-transaction cap
        if amount > mandate.max_amount:
            return AuthResult(
                authorized=False,
                mandate_id=mandate_id,
                amount=amount,
                vendor=vendor,
                category=category,
                reason="exceeds_per_transaction_cap",
            )

        # Daily + monthly spend headroom (from DB records)
        spent_day, spent_month = self._compute_spend(mandate_id, now)
        headroom_daily = mandate.daily_cap - spent_day
        headroom_monthly = mandate.monthly_cap - spent_month

        if amount > headroom_daily:
            return AuthResult(
                authorized=False,
                mandate_id=mandate_id,
                amount=amount,
                vendor=vendor,
                category=category,
                headroom_daily=headroom_daily,
                headroom_monthly=headroom_monthly,
                reason="exceeds_daily_cap",
            )
        if amount > headroom_monthly:
            return AuthResult(
                authorized=False,
                mandate_id=mandate_id,
                amount=amount,
                vendor=vendor,
                category=category,
                headroom_daily=headroom_daily,
                headroom_monthly=headroom_monthly,
                reason="exceeds_monthly_cap",
            )

        return AuthResult(
            authorized=True,
            mandate_id=mandate_id,
            amount=amount,
            vendor=vendor,
            category=category,
            headroom_per_tx=mandate.max_amount - amount,
            headroom_daily=headroom_daily - amount,
            headroom_monthly=headroom_monthly - amount,
        )

    # ── revocation ────────────────────────────────────────────────────────────

    def revoke_mandate(self, mandate_id: str) -> bool:
        existing = self.get_mandate(mandate_id)
        if not existing:
            return False
        now = datetime.now(timezone.utc)
        self.db.mandates.update(
            {"revoked": True, "revoked_at": now.isoformat()},
            MandateQ.mandate_id == mandate_id,
        )
        return True

    # ── spend recording ───────────────────────────────────────────────────────

    def record_spend(
        self,
        mandate_id: str,
        amount: Decimal,
        order_id: str,
        vendor: str,
        category: str | None = None,
        now: datetime | None = None,
    ) -> SpendRecord:
        rec = SpendRecord(
            mandate_id=mandate_id,
            order_id=order_id,
            amount=Decimal(amount),
            currency="USD",
            vendor=vendor,
            category=category,
            timestamp=now or datetime.now(timezone.utc),
        )
        self.db.spend_records.insert(rec.model_dump(mode="json"))
        return rec

    def _compute_spend(self, mandate_id: str, now: datetime) -> tuple[Decimal, Decimal]:
        """Returns (spent_today, spent_this_month) for the mandate."""
        rows = self.db.spend_records.search(SpendQ.mandate_id == mandate_id)
        spent_day = Decimal("0")
        spent_month = Decimal("0")
        for r in rows:
            ts = _parse_dt(r["timestamp"])
            amt = Decimal(r["amount"])
            if ts.year == now.year and ts.month == now.month:
                spent_month += amt
                if ts.date() == now.date():
                    spent_day += amt
        return spent_day, spent_month

    # ── merchant-side AP2 (stub — activates when merchants ship the extension) ─

    def present_mandate_proof(
        self, mandate: AgentMandate, headers: dict[str, str]
    ) -> dict[str, str]:
        """Attach an AP2 proof header for merchant-side mandate verification.

        Only meaningful when the merchant profile declares ``dev.ucp.extensions.ap2``.
        Until then this is a no-op in practice but always safe to call.
        """
        if mandate.digital_signature:
            headers = dict(headers)
            headers["AP2-Mandate-Id"] = mandate.mandate_id
            headers["AP2-Mandate-Signature"] = mandate.digital_signature
        return headers
