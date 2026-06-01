"""REPL command dispatch in main._handle_command.

Tests the side effects of each command without spinning up the full REPL.
The orchestrator and confirmation provider are stubbed since the commands
under test don't route through them.
"""

from __future__ import annotations

import asyncio
import io
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from rich.console import Console

import main as cli_main
from cli import display as display_module
from main import _handle_command


@pytest.fixture(autouse=True)
def silent_console(monkeypatch):
    """Quiet Rich's shared console across the test module."""
    quiet = Console(file=io.StringIO(), width=120)
    monkeypatch.setattr(display_module, "console", quiet)
    monkeypatch.setattr(cli_main, "console", quiet)


class _FakeOrchestrator:
    """Minimal stand-in — REPL dispatch only invokes it on free-text."""

    def __init__(self):
        self.calls = []

    async def run(self, ctx, line):
        self.calls.append(line)
        return {"reply": f"echo: {line}"}


def _dispatch(line, ctx, mandate_id, orch=None):
    """Awaits the dispatch coroutine for one input line."""
    orch = orch or _FakeOrchestrator()
    return asyncio.get_event_loop().run_until_complete(_handle_command(line, ctx, orch, mandate_id))


# ─── exit ───────────────────────────────────────────────────────────────────


def test_exit_returns_false(tool_ctx):
    assert _dispatch("exit", tool_ctx, "m_1") is False


def test_quit_returns_false(tool_ctx):
    assert _dispatch("quit", tool_ctx, "m_1") is False


def test_empty_line_continues(tool_ctx):
    assert _dispatch("", tool_ctx, "m_1") is True
    assert _dispatch("   ", tool_ctx, "m_1") is True


# ─── orders ─────────────────────────────────────────────────────────────────


def test_orders_with_empty_db(tool_ctx):
    assert _dispatch("orders", tool_ctx, "m_1") is True


def test_orders_with_one_row(tool_ctx):
    tool_ctx.db.orders.insert(
        {
            "order_id": "ord_1",
            "merchant_domain": "x.com",
            "total": "50",
            "status": "confirmed",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    assert _dispatch("orders", tool_ctx, "m_1") is True


# ─── mandate ────────────────────────────────────────────────────────────────


def test_mandate_command_displays_active(tool_ctx):
    m = tool_ctx.ap2.create_mandate(
        "user_1",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
    )
    assert _dispatch("mandate", tool_ctx, m.mandate_id) is True


def test_revoke_mandate_marks_revoked(tool_ctx):
    m = tool_ctx.ap2.create_mandate(
        "user_1",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
    )
    assert _dispatch("revoke mandate", tool_ctx, m.mandate_id) is True
    reloaded = tool_ctx.ap2.get_mandate(m.mandate_id)
    assert reloaded.revoked is True


# ─── profile ────────────────────────────────────────────────────────────────


def test_profile_command_runs(tool_ctx):
    assert _dispatch("profile", tool_ctx, "m_1") is True


# ─── block <merchant> ──────────────────────────────────────────────────────


def test_block_adds_to_blocklist(tool_ctx):
    assert "evil.com" not in tool_ctx.user.vendor_blocklist
    _dispatch("block evil.com", tool_ctx, "m_1")
    assert "evil.com" in tool_ctx.user.vendor_blocklist


def test_block_is_idempotent(tool_ctx):
    _dispatch("block evil.com", tool_ctx, "m_1")
    _dispatch("block evil.com", tool_ctx, "m_1")
    assert tool_ctx.user.vendor_blocklist.count("evil.com") == 1


# ─── audit ──────────────────────────────────────────────────────────────────


def test_audit_with_empty_log(tool_ctx):
    assert _dispatch("audit", tool_ctx, "m_1") is True


def test_audit_displays_recent_entries(tool_ctx):
    for i in range(3):
        tool_ctx.db.audit_log.insert(
            {
                "agent": "TestAgent",
                "tool": f"tool_{i}",
                "action": "x",
                "mandate_id": "m_1",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "args": {},
            }
        )
    assert _dispatch("audit", tool_ctx, "m_1") is True


# ─── track <id> ─────────────────────────────────────────────────────────────


def test_track_unknown_order_returns_true_with_warning(tool_ctx):
    """Unknown ord_id shouldn't crash — just warn."""
    assert _dispatch("track ord_nonexistent", tool_ctx, "m_1") is True


def test_track_known_order_polls_merchant(tool_ctx):
    """Insert an order then track it. Stub merchant returns 'pending'."""
    tool_ctx.db.orders.insert(
        {
            "order_id": "ord_1",
            "merchant_domain": "demo-shop.myshopify.com",
            "total": "50",
            "status": "confirmed",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    assert _dispatch("track ord_1", tool_ctx, "m_1") is True


# ─── free-text falls through to orchestrator ──────────────────────────────


def test_free_text_routes_to_orchestrator(tool_ctx):
    orch = _FakeOrchestrator()
    _dispatch("hello there", tool_ctx, "m_1", orch=orch)
    assert orch.calls == ["hello there"]
