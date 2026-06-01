"""Rich-based display helpers + RichConfirmProvider.

Two things live here:
  1. Visual primitives: product cards, order panels, tracking displays, mandate status.
  2. RichConfirmProvider — the human-facing implementation of ConfirmationProvider
     (the test-only AutoConfirmProvider lives in cli/confirmation.py).

Everything renders on a shared Rich Console so spinners + panels + prompts coexist.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Iterable

from rich.box import ROUNDED, HEAVY
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from cli.confirmation import ConfirmationProvider, GateData, GateResponse
from models.mandate import AgentMandate
from models.order import CheckoutSession, OrderStatus, PurchaseOrder, TrackingInfo
from models.product import RankedProduct
from models.user import UserProfile


# ─── Shared console singleton ───────────────────────────────────────────────

console = Console()


# ─── Welcome / mandate status ───────────────────────────────────────────────


def display_welcome(
    user_name: str, mandate: AgentMandate, spent_today: Decimal, spent_month: Decimal
) -> None:
    body = Text()
    body.append("Hi ", style="bold")
    body.append(user_name, style="bold cyan")
    body.append(". I'm your purchasing agent.\n\n")
    body.append("Active mandate: ", style="dim")
    body.append(mandate.mandate_id, style="cyan")
    body.append("\n")
    body.append(f"  · Per-transaction: ${mandate.max_amount}\n")
    body.append(f"  · Daily cap: ${mandate.daily_cap}  ", style="dim")
    body.append(f"(spent ${spent_today})", style="dim yellow")
    body.append("\n")
    body.append(f"  · Monthly cap: ${mandate.monthly_cap}  ", style="dim")
    body.append(f"(spent ${spent_month})", style="dim yellow")
    body.append("\n")
    body.append(f"  · Expires: {_fmt_dt(mandate.expiry)}\n\n", style="dim")
    body.append("Type a request, or one of: ", style="dim")
    body.append(
        "profile · orders · track <id> · mandate · "
        "revoke mandate · audit · block <merchant> · exit",
        style="dim italic",
    )
    console.print(
        Panel(
            body,
            title="🛍 Agentic Commerce",
            border_style="cyan",
            box=ROUNDED,
            padding=(1, 2),
        )
    )


def display_mandate_status(
    mandate: AgentMandate, spent_today: Decimal, spent_month: Decimal
) -> None:
    t = Table(box=ROUNDED, show_header=False, padding=(0, 1))
    t.add_column(style="dim", width=18)
    t.add_column()
    t.add_row("Mandate", f"[cyan]{mandate.mandate_id}[/]")
    t.add_row("Status", _status_text(mandate))
    t.add_row("Per-transaction", f"${mandate.max_amount}")
    t.add_row(
        "Daily cap",
        f"${spent_today} / ${mandate.daily_cap}  " f"({_pct(spent_today, mandate.daily_cap)})",
    )
    t.add_row(
        "Monthly cap",
        f"${spent_month} / ${mandate.monthly_cap}  " f"({_pct(spent_month, mandate.monthly_cap)})",
    )
    if mandate.allowed_categories:
        t.add_row("Categories", ", ".join(mandate.allowed_categories))
    if mandate.allowed_vendors:
        t.add_row("Allowed vendors", ", ".join(mandate.allowed_vendors))
    t.add_row("Expires", _fmt_dt(mandate.expiry))
    console.print(Panel(t, title="Mandate", border_style="cyan", box=ROUNDED))


def _status_text(mandate: AgentMandate) -> str:
    status = mandate.is_active()
    color = {"ACTIVE": "green", "REVOKED": "red", "EXPIRED": "yellow"}.get(status.name, "white")
    return f"[{color}]{status.value}[/]"


# ─── Product cards ──────────────────────────────────────────────────────────


def display_products(ranked: Iterable[RankedProduct]) -> None:
    ranked = list(ranked)
    if not ranked:
        console.print("[dim]No products found.[/]")
        return
    for r in ranked:
        p = r.product
        flags = ""
        if r.risk_flags:
            flags = " · " + " ".join(f"[yellow]⚠ {f}[/]" for f in r.risk_flags)
        title = f"[bold]#{r.rank}  {p.name}[/]   [green]${p.price}[/]{flags}"
        body = Text()
        rating = f"{p.rating}★" if p.rating else "—"
        reviews = f"({p.review_count} reviews)" if p.review_count else ""
        body.append(f"{p.merchant} · ", style="bold")
        body.append(f"{rating} {reviews} · ", style="dim")
        body.append(
            "in stock\n" if p.in_stock else "OUT OF STOCK\n",
            style="green" if p.in_stock else "red",
        )
        body.append(f"{p.merchant_domain}", style="dim cyan")
        if r.rationale:
            body.append(f"\nwhy: {r.rationale}", style="italic dim")
        body.append(f"\nscore: {r.score:.2f} · via {p.source_protocol}", style="dim")
        console.print(Panel(body, title=title, border_style="cyan", box=ROUNDED, padding=(0, 1)))


# ─── Checkout summary ───────────────────────────────────────────────────────


def display_checkout_summary(session: CheckoutSession) -> None:
    t = Table(box=ROUNDED, show_header=True, header_style="bold")
    t.add_column("Item")
    t.add_column("Qty", justify="right")
    t.add_column("Price", justify="right")
    t.add_column("Line", justify="right")
    for item in session.line_items:
        t.add_row(item.name, str(item.quantity), f"${item.price}", f"${item.line_total}")
    t.add_section()
    t.add_row("Subtotal", "", "", f"${session.subtotal}")
    if session.discount:
        t.add_row("Discount", "", "", f"-${session.discount}")
    if session.tax:
        t.add_row("Tax", "", "", f"${session.tax}")
    if session.shipping:
        t.add_row("Shipping", "", "", f"${session.shipping}")
    t.add_row("[bold]Total[/]", "", "", f"[bold green]${session.total}[/]")
    console.print(
        Panel(
            t,
            title=f"Checkout at {session.merchant_domain}",
            border_style="cyan",
            box=ROUNDED,
        )
    )


# ─── Order confirmation ─────────────────────────────────────────────────────


def display_order(order: PurchaseOrder) -> None:
    body = Text()
    body.append("✓ ", style="bold green")
    body.append("Order confirmed\n\n", style="bold")
    body.append("Order ID: ", style="dim")
    body.append(f"{order.order_id}\n", style="cyan")
    body.append("Merchant: ", style="dim")
    body.append(f"{order.merchant_domain}\n")
    body.append("Total: ", style="dim")
    body.append(f"${order.total} {order.currency}\n", style="green")
    if order.estimated_delivery:
        body.append("Delivery: ", style="dim")
        body.append(f"{order.estimated_delivery}\n")
    if order.tracking_number:
        body.append("Tracking: ", style="dim")
        body.append(f"{order.tracking_number}\n", style="cyan")
    body.append("\nPayment intent: ", style="dim")
    body.append(f"{order.payment_intent_id or '—'}\n", style="dim")
    body.append(f"Audit: every step logged to mandate {order.mandate_id}", style="dim italic")
    console.print(Panel(body, border_style="green", box=ROUNDED, padding=(1, 2)))


# ─── Tracking ───────────────────────────────────────────────────────────────


def display_tracking(info: TrackingInfo) -> None:
    color = {
        OrderStatus.DELIVERED: "green",
        OrderStatus.SHIPPED: "cyan",
        OrderStatus.CONFIRMED: "blue",
        OrderStatus.PENDING: "yellow",
        OrderStatus.CANCELLED: "red",
        OrderStatus.REFUNDED: "magenta",
    }.get(info.status, "white")
    body = Text()
    body.append("Status: ", style="dim")
    body.append(info.status.value.upper(), style=f"bold {color}")
    body.append("\n")
    if info.carrier:
        body.append("Carrier: ", style="dim")
        body.append(f"{info.carrier}\n")
    if info.tracking_number:
        body.append("Tracking #: ", style="dim")
        body.append(f"{info.tracking_number}\n", style="cyan")
    if info.estimated_delivery:
        body.append("ETA: ", style="dim")
        body.append(f"{info.estimated_delivery}\n")
    if info.last_event:
        body.append("Last event: ", style="dim")
        body.append(f"{info.last_event}\n", style="italic")
    body.append(f"\nUpdated: {_fmt_dt(info.last_updated)}", style="dim")
    console.print(Panel(body, title=f"Order {info.order_id}", border_style=color, box=ROUNDED))


# ─── Orders list ────────────────────────────────────────────────────────────


def display_orders(orders: list[dict]) -> None:
    if not orders:
        console.print("[dim]No orders yet.[/]")
        return
    t = Table(box=ROUNDED, show_header=True, header_style="bold")
    t.add_column("Order ID", style="cyan")
    t.add_column("Merchant")
    t.add_column("Total", justify="right")
    t.add_column("Status")
    t.add_column("Date", style="dim")
    for o in orders:
        t.add_row(
            o.get("order_id", "—"),
            o.get("merchant_domain", "—"),
            f"${o.get('total', '?')}",
            o.get("status", "—"),
            _short_dt(o.get("created_at", "")),
        )
    console.print(Panel(t, title="Your Orders", border_style="cyan", box=ROUNDED))


# ─── User profile (read-only) ───────────────────────────────────────────────


def display_profile(user: UserProfile) -> None:
    """Read-only summary of the user's profile. Payment method is intentionally
    redacted — see ``UserProfile.agent_safe_view``."""
    t = Table(box=ROUNDED, show_header=False, padding=(0, 1))
    t.add_column(style="dim", width=20)
    t.add_column()
    t.add_row("Name", user.name)
    if user.email:
        t.add_row("Email", str(user.email))
    t.add_row("User ID", f"[cyan]{user.user_id}[/]")

    if user.addresses:
        for i, a in enumerate(user.addresses):
            label = f"Address {i + 1}"
            marks = []
            if a.is_default_shipping:
                marks.append("default shipping")
            if a.is_default_billing:
                marks.append("default billing")
            mark_str = f"  [dim]({', '.join(marks)})[/]" if marks else ""
            t.add_row(
                label,
                f"{a.line1}, {a.city}, {a.region} {a.postal_code}, {a.country}" f"{mark_str}",
            )
    else:
        t.add_row("Address", "[yellow]none on file[/]")

    if user.preferred_categories:
        t.add_row("Categories", ", ".join(user.preferred_categories))
    if user.vendor_allowlist:
        t.add_row("Allowed vendors", ", ".join(user.vendor_allowlist))
    if user.vendor_blocklist:
        t.add_row("Blocked vendors", f"[red]{', '.join(user.vendor_blocklist)}[/]")

    pm = "[dim italic]configured (redacted)[/]" if user.payment_method_id else "[yellow]none[/]"
    t.add_row("Payment method", pm)

    console.print(Panel(t, title="Profile", border_style="cyan", box=ROUNDED))


# ─── Streaming-callback renderers ───────────────────────────────────────────


async def on_tool_start(name: str, args: dict) -> None:
    """Callback for OrchestratorAgent.callbacks.on_tool_start."""
    if name == "call_discovery_agent":
        domains = args.get("merchant_domains") or []
        if len(domains) == 1:
            label = f"searching {domains[0]}"
        elif len(domains) > 1:
            label = f"searching {len(domains)} merchants"
        else:
            label = "searching merchants"
    else:
        label = {
            "call_evaluation_agent": "ranking results",
            "call_purchase_agent": "placing order",
            "call_tracking_agent": "checking status",
            "get_user_profile": "reading profile",
            "get_last_discovered_products": "recalling prior search",
            "validate_mandate": "validating mandate",
            "check_spending_limits": "checking spending limits",
            "audit_log": "logging",
        }.get(name, name)
    console.print(f"  [dim]...[/] [cyan]{label}[/]")


async def on_tool_end(name: str, result) -> None:
    pass  # Quiet completion — the next panel will speak


async def on_text(delta: str) -> None:
    """Stream text deltas. The orchestrator emits full text blocks today."""
    console.print(delta, soft_wrap=True)


async def on_gate(tier: str, data: GateData) -> None:
    """Pre-gate banner — actual prompt happens in RichConfirmProvider."""
    pass


# ─── RichConfirmProvider — the human-facing HITL gate ───────────────────────


class RichConfirmProvider(ConfirmationProvider):
    """Asks the user via Rich console. Implements ConfirmationProvider Protocol.

    Use this in main.py. Tests use AutoConfirmProvider from cli/confirmation.py.
    """

    # Cancel-equivalent inputs across both gate tiers
    _CANCEL_TOKENS = {"", "no", "n", "cancel", "stop", "abort", "quit"}

    def __init__(self):
        # Track how many times we've prompted for the same gate. Counter is
        # reset each time a fresh basket is presented (different item_summary).
        self._last_gate_summary: str | None = None
        self._prompts_for_current_gate: int = 0

    def _is_repeat_gate(self, gate: GateData) -> bool:
        """True if we just prompted for this exact gate."""
        sig = f"{gate.merchant_domain}|{gate.amount}|{gate.item_summary}"
        if sig == self._last_gate_summary:
            self._prompts_for_current_gate += 1
            return True
        self._last_gate_summary = sig
        self._prompts_for_current_gate = 1
        return False

    async def soft_confirm(self, gate: GateData) -> GateResponse:
        if not self._is_repeat_gate(gate):
            console.print(
                Panel(
                    Text(f"{gate.item_summary}\n\n", style="white")
                    + Text("Press Enter to proceed, type ", style="dim")
                    + Text("no", style="bold red")
                    + Text(" to cancel, ask a question, or edit your basket.", style="dim"),
                    title="Quick confirmation",
                    border_style="cyan",
                    box=ROUNDED,
                )
            )
        else:
            console.print(
                "[dim]Still pending — press Enter to proceed, type "
                "[bold red]no[/] to cancel, ask a question, or edit your basket.[/]"
            )
        answer = Prompt.ask("", default="", show_default=False).strip()
        return self._classify(answer, soft=True)

    async def explicit_confirm(self, gate: GateData) -> GateResponse:
        # Refinement D + Fix #7: when basket is empty, suppress the formal
        # "PURCHASE CONFIRMATION REQUIRED" panel entirely. The orchestrator
        # has already emitted the empty-basket banner via on_text; we just
        # need to read user input here without confusing them with a panel
        # that says "Type CONFIRM to proceed" on a $0 basket.
        if not gate.items:
            answer = Prompt.ask(
                "[dim]Empty basket — add items, ask a question, or "
                "type [bold red]cancel[/]:[/] ",
                default="",
                show_default=False,
            ).strip()
            return self._classify(answer, soft=False)

        # Compact re-prompt for repeated gates — don't re-render the full panel.
        if self._is_repeat_gate(gate):
            console.print(
                "[dim]Gate still open — type [bold]confirm[/] to proceed, "
                "[bold red]cancel[/] to abort, or ask another question. "
                "(case-insensitive)[/]"
            )
            answer = Prompt.ask("[bold]> [/]", default="", show_default=False).strip()
            return self._classify(answer, soft=False)

        from rich.console import Group
        from rich.padding import Padding

        header = Text()
        header.append("PURCHASE CONFIRMATION REQUIRED\n\n", style="bold yellow")
        header.append(gate.item_summary, style="white")
        header.append("\n")

        # Basket sub-table — rendered when the agent passed multiple items
        renderables = [header]
        if gate.items and len(gate.items) > 1:
            basket = Table(box=ROUNDED, show_header=True, header_style="bold dim", padding=(0, 1))
            basket.add_column("Item")
            basket.add_column("Qty", justify="right")
            basket.add_column("Price", justify="right")
            basket.add_column("Line total", justify="right")
            for item in gate.items:
                basket.add_row(
                    item.get("name", "—"),
                    str(item.get("quantity", 1)),
                    f"${item.get('price', '?')}",
                    f"${item.get('line_total', '?')}",
                )
            renderables.append(Padding(basket, (0, 0, 1, 0)))

        footer = Text()
        footer.append("\nTotal: ", style="dim")
        footer.append(f"${gate.amount} {gate.currency}\n", style="bold green")
        if gate.full_summary and (not gate.items or len(gate.items) <= 1):
            footer.append("\n")
            footer.append(gate.full_summary, style="dim")
            footer.append("\n")
        if gate.risk_flags:
            footer.append("\n⚠ Risk flags: " + ", ".join(gate.risk_flags) + "\n", style="yellow")
        if gate.confidence_score is not None and gate.confidence_score < 0.8:
            footer.append(
                f"⚠ Agent confidence: {gate.confidence_score:.2f} (low)\n",
                style="yellow",
            )
        footer.append("\nType ", style="dim")
        footer.append("confirm", style="bold")
        footer.append(" to proceed, ", style="dim")
        footer.append("cancel", style="bold red")
        footer.append(" to abort, or edit your basket:\n", style="dim")
        footer.append(
            "  remove 1 · change to 2 · add [item] · " "swap [old] for [new] · clear basket",
            style="dim italic",
        )
        renderables.append(footer)

        console.print(
            Panel(
                Group(*renderables),
                title="Confirmation Required",
                border_style="yellow",
                box=HEAVY,
                padding=(1, 2),
            )
        )
        answer = Prompt.ask("[bold]> [/]", default="", show_default=False).strip()
        return self._classify(answer, soft=False)

    @classmethod
    def _classify(cls, answer: str, *, soft: bool) -> GateResponse:
        """Bucket the raw answer into confirm/cancel/question.

        Soft gate: empty input means proceed (per UX convention "press Enter").
        Explicit gate: only the literal word CONFIRM proceeds.
        Both gates: cancel words abort. Anything else is a question.
        """
        stripped = answer.strip()
        if not stripped:
            return GateResponse(decision="confirm") if soft else GateResponse(decision="cancel")
        upper = stripped.upper()
        if upper == "CONFIRM":
            return GateResponse(decision="confirm")
        if soft and upper == "OK":
            return GateResponse(decision="confirm")
        if stripped.lower() in cls._CANCEL_TOKENS:
            return GateResponse(decision="cancel")
        return GateResponse(decision="question", text=stripped)


# ─── Misc helpers ───────────────────────────────────────────────────────────


def _fmt_dt(dt: datetime | str | None) -> str:
    if dt is None:
        return "—"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.rstrip("Z"))
        except ValueError:
            return dt
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _short_dt(s: str) -> str:
    if not s:
        return "—"
    try:
        dt = datetime.fromisoformat(s.rstrip("Z"))
        return dt.strftime("%m-%d %H:%M")
    except ValueError:
        return s[:16]


def _pct(spent: Decimal, cap: Decimal) -> str:
    if not cap:
        return ""
    pct = (spent / cap) * 100
    return f"{pct:.0f}% used"
