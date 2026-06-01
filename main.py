"""Agentic Commerce — CLI entry point.

Run:
    export ANTHROPIC_API_KEY=sk-ant-...
    python main.py

Optional:
    STRIPE_TEST_KEY=...           Use real Stripe test mode (default: offline stub)
    SHOPIFY_SHOP_DOMAIN=...       Custom Shopify domain (default: demo-shop.myshopify.com)
    AGENT_PRIVATE_KEY_PEM=...     Persistent signing key (default: regenerated each run)
    AP2_SIGNING_KEY=...           Persistent HMAC key (default: regenerated each run)

Commands inside the REPL:
    orders                   — list all saved orders
    track <order_id>         — poll order status via TrackingAgent
    mandate                  — show mandate balance + cap usage
    revoke mandate           — instantly revoke spending authority
    audit                    — print the last 20 audit entries
    block <merchant_domain>  — add merchant to user blocklist (in-session)
    exit / quit              — leave

Anything else flows to the OrchestratorAgent.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
from datetime import datetime, timezone
from decimal import Decimal

from dotenv import load_dotenv

from adapters.shopify_mcp import ShopifyMCPAdapter, StubShopifyTransport
from adapters.stripe import StripeAdapter
from agents.orchestrator import OrchestratorAgent, StreamingCallbacks
from cli.display import (
    RichConfirmProvider,
    console,
    display_mandate_status,
    display_orders,
    display_profile,
    display_tracking,
    display_welcome,
    on_gate,
    on_text,
    on_tool_end,
    on_tool_start,
)
from config.catalogue import MERCHANTS
from config.settings import settings
from gateway.merchant_gateway import MerchantGateway
from gateway.payment_gateway import PaymentGateway
from guardrails.confidence import ConfidenceChecker
from guardrails.spending import SpendingLimiter
from models.user import Address, UserProfile
from storage.db import DB
from storage.state import SessionState
from tools.context import ToolContext
from ucp.ap2_extension import AP2MandateEngine
from ucp.discovery import UCPProfileDiscovery
from ucp.signing import RequestSigner, generate_keypair

load_dotenv()


# ─── Bootstrap ──────────────────────────────────────────────────────────────


def _bootstrap_keys() -> tuple[str, str]:
    """Ensure signing keys exist; regenerate ephemeral if not in env."""
    private_pem = os.getenv("AGENT_PRIVATE_KEY_PEM")
    if not private_pem:
        private_pem, _, _ = generate_keypair("agent-key-1")
    ap2_key = os.getenv("AP2_SIGNING_KEY")
    if not ap2_key:
        ap2_key = secrets.token_hex(32)
    return private_pem, ap2_key


def _build_context(db: DB) -> tuple[ToolContext, str]:
    """Wire up every layer. Returns (context, mandate_id)."""
    private_pem, ap2_key = _bootstrap_keys()

    ap2 = AP2MandateEngine(db, ap2_key)

    # Default user — MVP single-user. Has a real default shipping address
    # so the PurchaseAgent can construct BuyerInfo without prompting.
    user = UserProfile(
        user_id="local_user",
        name=os.getenv("USER_NAME", "Friend"),
        payment_method_id="pm_test_card_visa",
        preferred_categories=["running", "apparel", "electronics", "lifestyle"],
        addresses=[
            Address(
                line1="1 Demo Street",
                city="San Francisco",
                region="CA",
                postal_code="94110",
                country="US",
                is_default_shipping=True,
                is_default_billing=True,
            )
        ],
    )

    # Mandate sized so users can comfortably test the gate tiers
    mandate = ap2.create_mandate(
        user_id=user.user_id,
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        payment_method_id=user.payment_method_id,
        expiry_hours=24,
    )

    # Gateway wiring
    signer = RequestSigner(private_pem, key_id=settings.agent_key_id)

    # Register the three demo merchants. Each is a ShopifyMCPAdapter wrapping
    # its own StubShopifyTransport seeded from config/catalogue.py.
    # Production: swap each transport for the real @shopify/dev-mcp one.
    direct_adapters: dict[str, ShopifyMCPAdapter] = {}
    for domain, seed in MERCHANTS.items():
        direct_adapters[domain] = ShopifyMCPAdapter(
            domain,
            StubShopifyTransport(seed_products=seed),
        )

    discovery = UCPProfileDiscovery(db)
    gateway = MerchantGateway(
        discovery=discovery,
        signer=signer,
        direct_adapters=direct_adapters,
    )

    stripe = StripeAdapter(api_key=os.getenv("STRIPE_TEST_KEY") or None)
    payments = PaymentGateway(ap2, stripe)

    ctx = ToolContext(
        db=db,
        ap2=ap2,
        merchant_gateway=gateway,
        payment_gateway=payments,
        spending_limiter=SpendingLimiter(db),
        confidence_checker=ConfidenceChecker(),
        user=user,
        session=SessionState(user_id=user.user_id),
    )
    return ctx, mandate.mandate_id


def _build_anthropic_client():
    """Lazily import the Anthropic SDK so test runs don't require it."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        console.print(
            "[red]ANTHROPIC_API_KEY not set.[/] "
            "Export your key and try again. "
            "See .env.example for setup."
        )
        sys.exit(1)
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        console.print(
            "[red]anthropic SDK not installed.[/] " "Run: [bold]pip install anthropic>=0.40[/]"
        )
        sys.exit(1)
    return AsyncAnthropic(api_key=api_key)


# ─── Command dispatch ──────────────────────────────────────────────────────


async def _handle_command(
    line: str, ctx: ToolContext, orchestrator: OrchestratorAgent, mandate_id: str
) -> bool:
    """Returns False to exit the loop. True to continue."""
    line = line.strip()
    if not line:
        return True

    if line in {"exit", "quit"}:
        return False

    if line == "orders":
        display_orders(ctx.db.orders.all())
        return True

    if line.startswith("track "):
        order_id = line.split(" ", 1)[1].strip()
        row = ctx.db.orders.get(
            __import__("storage.db", fromlist=["OrderQ"]).OrderQ.order_id == order_id
        )
        merchant = row.get("merchant_domain") if row else None
        if not merchant:
            console.print(f"[yellow]Order {order_id} not found in local DB.[/]")
            return True
        from tools.tracking_tools import get_order_status

        info = await get_order_status(
            ctx, order_id=order_id, merchant_domain=merchant, mandate_id=mandate_id
        )
        if info:
            display_tracking(info)
        else:
            console.print("[yellow]Couldn't reach merchant for tracking.[/]")
        return True

    if line == "mandate":
        spent_day, spent_month = ctx.ap2._compute_spend(mandate_id, datetime.now(timezone.utc))
        m = ctx.ap2.get_mandate(mandate_id)
        if m:
            display_mandate_status(m, spent_day, spent_month)
        return True

    if line == "profile":
        display_profile(ctx.user)
        return True

    if line == "revoke mandate":
        ctx.ap2.revoke_mandate(mandate_id)
        console.print("[red]Mandate revoked. Further purchases will be refused.[/]")
        return True

    if line == "audit":
        rows = ctx.db.audit_log.all()[-20:]
        if not rows:
            console.print("[dim]No audit entries yet.[/]")
            return True
        for r in rows:
            console.print(
                f"[dim]{r.get('timestamp', '')[:19]}[/] "
                f"[cyan]{r.get('agent', '')}[/]·[bold]{r.get('tool', '')}[/] "
                f"{r.get('action', '')}"
            )
        return True

    if line.startswith("block "):
        merchant = line.split(" ", 1)[1].strip()
        if merchant not in ctx.user.vendor_blocklist:
            ctx.user.vendor_blocklist.append(merchant)
        console.print(f"[red]Blocked: {merchant}[/]")
        return True

    # Default — flow to the orchestrator
    console.print(f"[dim]you: {line}[/]")
    result = await orchestrator.run(ctx, line)
    reply = result.get("reply", "")
    if reply:
        console.print(Panel(reply.strip(), border_style="green", padding=(0, 1)))
    return True


# ─── Main loop ──────────────────────────────────────────────────────────────


async def _amain() -> None:
    # Storage
    db_path = settings.db_path
    db = DB(db_path)

    # Wire context + mandate
    ctx, mandate_id = _build_context(db)

    # Anthropic client + orchestrator
    client = _build_anthropic_client()
    confirm = RichConfirmProvider()
    callbacks = StreamingCallbacks(
        on_text=on_text,
        on_tool_start=on_tool_start,
        on_tool_end=on_tool_end,
        on_gate=on_gate,
    )
    available_merchants = list(ctx.merchant_gateway.direct_adapters.keys())
    orchestrator = OrchestratorAgent(
        client,
        confirmation=confirm,
        callbacks=callbacks,
        mandate_id=mandate_id,
        available_merchants=available_merchants,
    )

    # Welcome
    spent_day, spent_month = ctx.ap2._compute_spend(mandate_id, datetime.now(timezone.utc))
    mandate = ctx.ap2.get_mandate(mandate_id)
    display_welcome(ctx.user.name, mandate, spent_day, spent_month)

    # REPL
    while True:
        try:
            line = console.input("\n[bold cyan]›[/] ")
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        try:
            cont = await _handle_command(line, ctx, orchestrator, mandate_id)
            if not cont:
                break
        except Exception as e:
            console.print(f"[red]Error:[/] {type(e).__name__}: {e}")

    console.print("[dim]bye[/]")
    db.close()


# Local import to avoid circular concerns
from rich.panel import Panel  # noqa: E402


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
