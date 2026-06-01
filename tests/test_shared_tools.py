"""Shared tools: audit log, profile view, spending limit check."""

from __future__ import annotations

import asyncio
from decimal import Decimal

from tools.shared_tools import audit_log, check_spending_limits, get_user_profile


def test_audit_log_writes_immutable_row(tool_ctx):
    asyncio.get_event_loop().run_until_complete(
        audit_log(
            tool_ctx,
            agent="DiscoveryAgent",
            tool="search_products",
            action="query='shoes'",
            mandate_id="m1",
            args={"query": "shoes"},
        )
    )
    rows = tool_ctx.db.audit_log.all()
    assert len(rows) == 1
    assert rows[0]["agent"] == "DiscoveryAgent"
    assert rows[0]["mandate_id"] == "m1"
    assert rows[0]["args"] == {"query": "shoes"}
    assert "timestamp" in rows[0]


def test_get_user_profile_excludes_payment_method(tool_ctx):
    profile = asyncio.get_event_loop().run_until_complete(get_user_profile(tool_ctx))
    assert "payment_method_id" not in profile
    assert profile["user_id"] == "user_1"


def test_check_spending_limits_returns_auth_result(tool_ctx):
    m = tool_ctx.ap2.create_mandate(
        "user_1",
        max_amount=Decimal("100"),
        daily_cap=Decimal("500"),
        monthly_cap=Decimal("2000"),
    )
    r = asyncio.get_event_loop().run_until_complete(
        check_spending_limits(
            tool_ctx,
            mandate_id=m.mandate_id,
            amount=Decimal("50"),
        )
    )
    assert r.authorized is True
    over = asyncio.get_event_loop().run_until_complete(
        check_spending_limits(
            tool_ctx,
            mandate_id=m.mandate_id,
            amount=Decimal("150"),
        )
    )
    assert over.authorized is False
