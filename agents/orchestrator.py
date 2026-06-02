"""OrchestratorAgent — coordinates subagents, enforces HITL gates.

The Orchestrator's tools are mostly Python wrappers that spawn subagents and
return their structured outputs. The HITL confirmation gate is intercepted
inside the tool dispatch for ``call_purchase_agent`` — the model never sees
the gate as a tool it could route around.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Awaitable, Callable

from agents.base import AnthropicLike, BaseAgent, ToolSpec, make_tool_spec
from agents.discovery import DiscoveryAgent
from agents.evaluation import EvaluationAgent
from agents.prompts import ORCHESTRATOR, orchestrator_prompt
from agents.purchase import PurchaseAgent
from agents.tracking import TrackingAgent
from cli.confirmation import (
    AutoConfirmProvider,
    ConfirmationProvider,
    GateData,
    classify_gate,
)
from tools import shared_tools
from tools.context import ToolContext


# ─── Gate input handling ────────────────────────────────────────────────────


@dataclass
class GateAction:
    """Outcome of handling free-text input at the confirmation gate.

    ``kind`` determines what the orchestrator does next:
      - "answer": display ``text`` to user, re-present gate (basket unchanged)
      - "remove": drop item ``target_product_id`` from the basket
      - "change_quantity": set ``target_product_id``'s qty to ``new_quantity``
      - "add": append a new item (must be present in the discovery cache)
      - "swap": atomically remove ``target_product_id`` and add ``new_item``
      - "clear": empty the entire basket atomically
      - "refused": modification was refused — ``text`` carries the friendly
                    explanation; basket is unchanged
    """

    kind: str
    text: str = ""
    target_product_id: str = ""
    new_quantity: int = 0
    new_item: dict | None = None  # for "add" / "swap" — full item dict


# ─── Streaming callbacks (no-op by default; CLI wires Rich here in Phase 4) ──


@dataclass
class StreamingCallbacks:
    on_text: Callable[[str], Awaitable[None]] | None = None
    on_tool_start: Callable[[str, dict], Awaitable[None]] | None = None
    on_tool_end: Callable[[str, Any], Awaitable[None]] | None = None
    on_gate: Callable[[str, GateData], Awaitable[None]] | None = None
    # Tier ("soft"|"explicit"|"explicit_with_summary"), gate data
    on_bubble_end: Callable[[], Awaitable[None]] | None = None
    # Signals the SSE client to close the current agent bubble so the next
    # on_text call starts a fresh one. Used after each gate Q&A response so
    # intermediate replies and the final purchase confirmation appear as
    # separate bubbles rather than being concatenated.


class OrchestratorAgent(BaseAgent):
    model = "claude-sonnet-4-6"
    system_prompt = ORCHESTRATOR
    max_tokens = 4096

    # Max times the user can ask a question at a single gate before we give
    # up and cancel. Prevents infinite loops if model + user disagree forever.
    MAX_GATE_QUESTIONS = 5

    def __init__(
        self,
        client: AnthropicLike,
        *,
        confirmation: ConfirmationProvider | None = None,
        callbacks: StreamingCallbacks | None = None,
        mandate_id: str | None = None,
        available_merchants: list[str] | None = None,
    ):
        # The subagent classes live on the orchestrator. Built lazily per run.
        self._client = client
        self.confirmation = confirmation or AutoConfirmProvider()
        self.callbacks = callbacks or StreamingCallbacks()
        self.mandate_id = mandate_id
        self.available_merchants = list(available_merchants or [])

        # Buffer for gate-time Q&A. Anthropic's API requires tool_use blocks
        # to be IMMEDIATELY followed by tool_result blocks — we cannot append
        # user/assistant text turns to ctx.session.conversation during a tool
        # dispatch. We buffer them here and flush post-run() instead.
        self._pending_gate_history: list[dict] = []

        # Subagent factories — let tests substitute alt clients if needed
        self._discovery = DiscoveryAgent(client)
        self._evaluation = EvaluationAgent(client)
        self._purchase = PurchaseAgent(client)
        self._tracking = TrackingAgent(client)

        # Per-instance system prompt rendered with the live merchant list.
        # Append the active mandate_id so the model knows which handle to
        # pass to tools like get_active_mandate_summary / validate_mandate /
        # check_spending_limits without having to guess.
        self.system_prompt = orchestrator_prompt(self.available_merchants)
        if self.mandate_id:
            self.system_prompt += (
                f"\n\nACTIVE MANDATE\n"
                f"Your active mandate_id is `{self.mandate_id}`. Pass this "
                f"string verbatim to any tool that requires a mandate_id "
                f"(get_active_mandate_summary, validate_mandate, "
                f"check_spending_limits, audit_log). Do not invent or "
                f"abbreviate it."
            )

        # Build tool specs that bind to instance methods
        self.tool_specs = self._build_tool_specs()
        super().__init__(client)

    # ── tool spec construction ───────────────────────────────────────────────

    def _build_tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="call_discovery_agent",
                description="Search and return candidate products. "
                "Args: brief (string describing what to find), "
                "merchant_domains (list[str] of domains to search).",
                input_schema={
                    "type": "object",
                    "properties": {
                        "brief": {"type": "string"},
                        "merchant_domains": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["brief", "merchant_domains"],
                },
                handler=self._call_discovery,
                takes_context=True,
            ),
            ToolSpec(
                name="call_evaluation_agent",
                description="Rank products. Args: brief (string), products (list of ProductResult dicts).",
                input_schema={
                    "type": "object",
                    "properties": {
                        "brief": {"type": "string"},
                        "products": {"type": "array"},
                    },
                    "required": ["brief", "products"],
                },
                handler=self._call_evaluation,
                takes_context=True,
            ),
            ToolSpec(
                name="call_purchase_agent",
                description=(
                    "Execute a purchase for a basket of 1+ items at ONE merchant. "
                    "HITL-gated — runtime may cancel via {status: 'cancelled_by_user'}. "
                    "All items must share the same merchant_domain. "
                    "For multi-merchant purchases call this tool once per merchant. "
                    "Args: brief (string), merchant_domain, "
                    "items (list of {product_id, name, price, quantity})."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "brief": {"type": "string"},
                        "merchant_domain": {"type": "string"},
                        "items": {
                            "type": "array",
                            "description": "One or more items to purchase",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "product_id": {"type": "string"},
                                    "name": {"type": "string"},
                                    "price": {"type": "string"},
                                    "quantity": {"type": "integer"},
                                },
                                "required": ["product_id", "name", "price"],
                            },
                        },
                    },
                    "required": ["brief", "merchant_domain", "items"],
                },
                handler=self._call_purchase,
                takes_context=True,
            ),
            ToolSpec(
                name="call_tracking_agent",
                description="Check order status. Args: order_id, merchant_domain.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "order_id": {"type": "string"},
                        "merchant_domain": {"type": "string"},
                    },
                    "required": ["order_id", "merchant_domain"],
                },
                handler=self._call_tracking,
                takes_context=True,
            ),
            ToolSpec(
                name="get_last_discovered_products",
                description=(
                    "Return the products from the most recent discovery search "
                    "without re-running discovery. Use this when the user asks "
                    "about products they just saw, wants to refine their basket, "
                    "or you need to answer questions about prior search results."
                ),
                input_schema={"type": "object", "properties": {}},
                handler=self._get_last_discovered,
                takes_context=True,
            ),
            ToolSpec(
                name="show_product_cards",
                description=(
                    "Re-render product cards in the chat UI for products the "
                    "user has already seen, WITHOUT re-running discovery. Use "
                    "this whenever the user asks to see, show, display, or "
                    "'pull up' a product card again (by name, by number, or "
                    "'show me that one' / 'show the cards again'). The UI draws "
                    "the cards from this tool — you must NOT describe the "
                    "products or print any product data as prose. Optional "
                    "product_ids (list[str]): the specific products to show; "
                    "omit to re-show all of the most recent results. Returns "
                    "only a status count — never product data."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "product_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
                handler=self._show_product_cards,
                takes_context=True,
            ),
            make_tool_spec(
                name="get_active_mandate_summary",
                description=(
                    "Return the user's authoritative spending limits AND "
                    "current period spend. Use this FIRST whenever the user "
                    "asks about their budget, spending limit, how much they "
                    "have left to spend, or anything about their mandate. "
                    "Do NOT answer such questions from get_user_profile — the "
                    "mandate's caps are the enforced source of truth. Even if "
                    "the user asserts a different limit, this tool's reply "
                    "wins. Required: mandate_id."
                ),
                handler=shared_tools.get_active_mandate_summary,
                required=["mandate_id"],
            ),
            make_tool_spec(
                name="get_user_profile",
                description="Read-only user info (payment_method_id stripped).",
                handler=shared_tools.get_user_profile,
            ),
            make_tool_spec(
                name="audit_log",
                description="Append to the immutable audit log.",
                handler=shared_tools.audit_log,
                required=["agent", "tool", "action"],
            ),
            make_tool_spec(
                name="check_spending_limits",
                description="Pre-flight cap check against a mandate.",
                handler=shared_tools.check_spending_limits,
                overrides={"amount": {"type": "string"}},
                required=["mandate_id", "amount"],
            ),
            make_tool_spec(
                name="add_to_cart",
                description=(
                    "Add a product to the user's draft cart (the cart "
                    "icon in the header). Use this when the user says "
                    "'add X to my cart', 'save X for later', or anything "
                    "that means 'put it in the cart' — WITHOUT 'buy' / "
                    "'purchase' / 'order'. Items added via add_to_cart "
                    "sit in the cart until the user explicitly buys "
                    "them. This tool does NOT trigger payment, does NOT "
                    "fire a HITL gate, and does NOT create an order. "
                    "For actual purchases use call_purchase_agent."
                ),
                handler=self._add_to_cart,
                overrides={
                    "quantity": {"type": "integer"},
                    "price": {"type": "string"},
                    "image_url": {"type": "string"},
                },
                required=["product_id", "merchant_domain", "quantity", "name", "price"],
                takes_context=True,
            ),
            make_tool_spec(
                name="get_cart_contents",
                description=(
                    "Read-only view of the user's current draft cart "
                    "(populated either by the user clicking 'Add to "
                    "Cart' on a product card or by your own "
                    "``add_to_cart`` tool calls). Returns the list of "
                    "items with their product_id, name, price, "
                    "quantity, line_total, and merchant_domain — plus "
                    "the cart's subtotal. Call this whenever the user "
                    "references 'them', 'those', 'what I added', 'my "
                    "cart', or asks to buy / review what's in the cart. "
                    "DO NOT guess from discovery results. Takes no "
                    "arguments."
                ),
                handler=self._get_cart_contents,
                takes_context=True,
            ),
        ]

    # ── subagent dispatch ────────────────────────────────────────────────────

    async def _call_discovery(
        self, ctx: ToolContext, *, brief: str, merchant_domains: list[str]
    ) -> dict:
        # Defensive fallback: if model passed an empty list, or invented domains
        # we don't have adapters for, route through the known-available ones.
        registered = set(ctx.merchant_gateway.direct_adapters.keys()) | set(
            self.available_merchants
        )
        if not merchant_domains:
            merchant_domains = sorted(registered)
        else:
            # Drop any hallucinated domains the gateway can't reach
            usable = [d for d in merchant_domains if d in registered]
            if not usable and registered:
                usable = sorted(registered)
            merchant_domains = usable

        message = f"{brief}\n\nMerchant domains to search: {json.dumps(merchant_domains)}"
        await self._emit_tool_start(
            "call_discovery_agent",
            {"brief": brief, "merchant_domains": merchant_domains},
        )
        result = await self._discovery.run(ctx, message)
        # Cache the products so we don't have to re-discover when the user
        # asks follow-up questions or returns to refine the basket.
        if isinstance(result, dict) and isinstance(result.get("products"), list):
            ctx.session.last_discovered_products = result["products"]
        await self._emit_tool_end("call_discovery_agent", result)
        return result

    async def _get_last_discovered(self, ctx: ToolContext) -> dict:
        """Return the most recent discovery cache, no re-search."""
        products = ctx.session.last_discovered_products or []
        return {"products": products, "count": len(products), "source": "session_cache"}

    async def _show_product_cards(
        self, ctx: ToolContext, *, product_ids: list[str] | None = None
    ) -> dict:
        """Queue already-discovered products for the UI to (re-)render as cards.

        Selects from ``ctx.session.last_discovered_products`` (no re-search) and
        stashes the matches on ``ctx.session.cards_to_show`` for the web layer to
        drain into a ``products`` SSE event. Returns ONLY a minimal status — never
        the product data — so the model has nothing to echo as prose.
        """
        cached = ctx.session.last_discovered_products or []
        if product_ids:
            wanted = set(product_ids)
            selected = [
                p for p in cached if str(p.get("product_id") or p.get("id") or "") in wanted
            ]
        else:
            selected = list(cached)
        ctx.session.cards_to_show = selected
        return {"status": "cards_rendered", "shown": len(selected)}

    async def _add_to_cart(
        self,
        ctx: ToolContext,
        *,
        product_id: str,
        merchant_domain: str,
        quantity: int,
        name: str,
        price,
        image_url: str = "",
    ) -> dict:
        """Add a product to ``ctx.session.click_basket`` (the user's
        draft cart visible via the header cart icon + /cart drawer).

        Strictly distinct from ``call_purchase_agent``:
          - No HITL gate, no payment, no order, no spend.
          - Idempotent at the (merchant_domain, product_id) level —
            calling twice for the same product bumps the existing
            line's quantity rather than duplicating it.
          - Audits as ``add_to_cart`` so /audit shows the action.
          - If the web layer wired a ``cart_event_notifier`` on the
            ToolContext, push a ``cart_update`` event so the
            browser's badge bumps immediately.
        """
        try:
            qty = int(quantity)
        except Exception:
            qty = 1
        if qty < 1:
            qty = 1
        try:
            price_dec = Decimal(str(price))
        except Exception:
            price_dec = Decimal("0")

        bucket = ctx.session.click_basket.setdefault(merchant_domain, [])
        for line in bucket:
            if line.get("product_id") == product_id:
                line["quantity"] = int(line["quantity"]) + qty
                line["line_total"] = str(Decimal(str(line["price"])) * line["quantity"])
                break
        else:
            bucket.append(
                {
                    "product_id": product_id,
                    "name": name,
                    "price": str(price_dec),
                    "currency": "USD",
                    "quantity": qty,
                    "line_total": str(price_dec * qty),
                    "image_url": image_url or "",
                }
            )

        # Audit the action — keyed under the ``add_to_cart`` tool name
        # so /audit clearly separates it from the purchase chain.
        await shared_tools.audit_log(
            ctx,
            agent="OrchestratorAgent",
            tool="add_to_cart",
            action=f"merchant={merchant_domain} product={product_id} qty={qty}",
            mandate_id=self.mandate_id,
            args={
                "merchant": merchant_domain,
                "product_id": product_id,
                "quantity": qty,
                "name": name,
                "price": str(price_dec),
            },
        )

        # Real-time badge bump on whatever page the user is on
        notifier = getattr(ctx, "cart_event_notifier", None)
        if notifier is not None:
            total = sum(
                int(item.get("quantity", 0) or 0)
                for items in ctx.session.click_basket.values()
                for item in items
            )
            try:
                notifier({"type": "cart_update", "data": {"count": total}})
            except Exception:  # noqa: BLE001
                pass

        return {
            "added": True,
            "product_id": product_id,
            "merchant_domain": merchant_domain,
            "quantity": qty,
        }

    async def _get_cart_contents(self, ctx: ToolContext) -> dict:
        """Read-only inventory of the user's click_basket.

        Returns the same shape the /cart page uses — flat list of
        items each tagged with merchant_domain, plus a subtotal and
        item_count. Useful when the user references 'them' / 'those'
        / 'what's in my cart' and the agent needs to resolve the
        reference without guessing from discovery results.
        """
        lines = []
        subtotal = Decimal("0")
        for merchant, items in ctx.session.click_basket.items():
            for it in items:
                lines.append(
                    {
                        "product_id": it.get("product_id", ""),
                        "name": it.get("name", ""),
                        "price": str(it.get("price", "0")),
                        "currency": it.get("currency", "USD"),
                        "quantity": int(it.get("quantity", 0) or 0),
                        "line_total": str(it.get("line_total", "0")),
                        "merchant_domain": merchant,
                    }
                )
                try:
                    subtotal += Decimal(str(it.get("line_total", "0")))
                except Exception:  # noqa: BLE001
                    pass
        return {
            "items": lines,
            "subtotal": str(subtotal),
            "currency": "USD",
            "item_count": sum(int(ln["quantity"]) for ln in lines),
            "is_empty": not lines,
        }

    async def _call_evaluation(self, ctx: ToolContext, *, brief: str, products: list[dict]) -> dict:
        message = f"{brief}\n\nCandidate products (as JSON): {json.dumps(products, default=str)}"
        await self._emit_tool_start(
            "call_evaluation_agent", {"brief": brief, "product_count": len(products)}
        )
        result = await self._evaluation.run(ctx, message)
        await self._emit_tool_end("call_evaluation_agent", result)
        return result

    async def _call_tracking(
        self, ctx: ToolContext, *, order_id: str, merchant_domain: str
    ) -> dict:
        message = f"Check the status of order {order_id} at {merchant_domain}."
        await self._emit_tool_start(
            "call_tracking_agent",
            {"order_id": order_id, "merchant_domain": merchant_domain},
        )
        result = await self._tracking.run(ctx, message)
        await self._emit_tool_end("call_tracking_agent", result)
        return result

    # ── the HITL-gated one ───────────────────────────────────────────────────

    async def _call_purchase(
        self,
        ctx: ToolContext,
        *,
        brief: str,
        merchant_domain: str,
        items: list[dict],
    ) -> dict:
        if self.mandate_id is None:
            return {"status": "failed", "reason": "no_active_mandate"}

        if not items:
            return {"status": "failed", "reason": "empty_basket"}

        # Build the initial basket. Server-side totals throughout — never
        # trust model-supplied amount claims.
        basket_items = self._normalise_basket(items)

        # Gate loop with in-place basket editing. Each iteration:
        #   1. Recompute total + tier from current basket_items
        #   2. (Optional) re-validate mandate if basket has been mutated
        #   3. Show gate
        #   4. If confirm/cancel — done
        #   5. If question/modification — apply, loop back
        approved = False
        cancelled_reason: str | None = None
        is_new_merchant = self._is_first_purchase(ctx, merchant_domain)
        # Counts non-empty-basket iterations against MAX_GATE_QUESTIONS.
        # Empty-basket iterations DON'T count — Refinement D guarantees the
        # gate stays open as long as the user keeps engaging.
        non_empty_questions = 0
        # First time the basket goes empty, we render the full empty-state
        # banner; subsequent loops re-prompt compactly to avoid noise.
        empty_banner_shown = False
        # Tracks active search sub-flow (Refinement C): if non-None, the user
        # is in a picker choosing from search results rather than the main gate.
        pending_search_query: str | None = None
        # Whether the assistant's last text already addressed CONFIRM/cancel —
        # used to suppress the redundant "Gate still open" hint.
        last_assistant_gave_hint: bool = False
        # Hard ceiling to prevent any pathological infinite loop. Generous
        # because empty-basket browsing is legitimate, but bounded.
        ABSOLUTE_MAX_ITERATIONS = 50
        total_iterations = 0
        # Phase 8f: when the previous iteration handled a non-mutating
        # answer, we re-present the same basket but flag the gate event
        # so the browser modal stays HIDDEN (user reads the answer in
        # chat). Mutations leave this False so the modal updates with
        # the new basket.
        answer_only_for_next_iter = False

        while total_iterations < ABSOLUTE_MAX_ITERATIONS:
            total_iterations += 1
            total = self._compute_basket_total(basket_items)

            # Refinement D: empty basket no longer auto-cancels. Stay open
            # indefinitely (bounded only by ABSOLUTE_MAX_ITERATIONS) and let
            # the user add items, ask questions, or explicitly cancel.
            if not basket_items:
                if not empty_banner_shown:
                    empty_msg = (
                        "Your basket is now empty.\n"
                        '  - Add items (e.g. "add a coffee mug") to keep shopping\n'
                        "  - Type cancel to abort this purchase\n"
                        "  - CONFIRM does nothing while the basket is empty"
                    )
                    if self.callbacks.on_text:
                        await self.callbacks.on_text(empty_msg)
                    empty_banner_shown = True
                # Empty iterations don't count toward MAX_GATE_QUESTIONS
            else:
                # We re-entered a populated state — reset the empty banner
                # so it shows again if the user empties the basket later.
                empty_banner_shown = False

            tier = classify_gate(
                total,
                is_first_purchase_from_merchant=is_new_merchant,
            )
            gate = self._build_gate_data(
                merchant_domain,
                basket_items,
                total,
                tier,
                answer_only=answer_only_for_next_iter,
            )
            # Reset the flag for the NEXT iteration — only set again if
            # this iteration handles another non-mutating answer.
            answer_only_for_next_iter = False
            if self.callbacks.on_gate and total_iterations == 1:
                await self.callbacks.on_gate(tier, gate)

            response = (
                await self.confirmation.soft_confirm(gate)
                if tier == "soft"
                else await self.confirmation.explicit_confirm(gate)
            )

            if response.decision == "confirm":
                # Refinement D: CONFIRM with empty basket is a no-op.
                if not basket_items:
                    no_op_msg = (
                        "The basket is empty — there is nothing to purchase. "
                        "Add items first, or type cancel to abort."
                    )
                    if self.callbacks.on_text:
                        await self.callbacks.on_text(no_op_msg)
                    if self.callbacks.on_bubble_end:
                        await self.callbacks.on_bubble_end()
                    self._buffer_gate_qa("[CONFIRM on empty basket]", no_op_msg)
                    continue
                approved = True
                break

            if response.decision == "cancel":
                break

            # response.decision == "question" → could be Q&A, basket edit, or
            # a request we can't fulfil. Let the helper classify the intent
            # and return a structured action.
            await shared_tools.audit_log(
                ctx,
                agent="OrchestratorAgent",
                tool="hitl_gate",
                action=f"gate_input tier={tier}",
                mandate_id=self.mandate_id,
                args={"merchant": merchant_domain, "input": response.text[:200]},
            )
            # Refinement B: resolve numeric references ("remove 1", "2", "#3")
            # in Python before making a Claude round-trip. Saves latency and
            # is 100% deterministic for both human and agent callers.
            # IMPORTANT: when a search sub-flow is pending, numeric input is
            # interpreted as a cache-picker selection, not a basket remove.
            # We skip this block in that case and let the search-picker
            # logic below handle it.
            if pending_search_query is None:
                resolved_pid = self._resolve_numeric_reference(
                    response.text,
                    basket_items,
                )
                if resolved_pid is not None:
                    action = GateAction(
                        kind="remove",
                        target_product_id=resolved_pid,
                        text="",
                    )
                else:
                    action = await self._handle_gate_input(
                        ctx,
                        user_input=response.text,
                        merchant_domain=merchant_domain,
                        basket_items=basket_items,
                        total=total,
                    )
            else:
                # In search picker state — get a placeholder action;
                # the search-picker code block below will handle it.
                action = GateAction(kind="answer", text=response.text)

            # Refinement C: if the model returned intent="answer" with a
            # search signal, run the search sub-flow and show results inline.
            # The user's NEXT input will be their picker selection.
            if action.kind == "answer" and self._looks_like_search_intent(action.text):
                # Extract a query from the user's original input
                search_q = response.text.strip()
                search_text, _ = await self._search_and_offer_sub_flow(
                    ctx,
                    query=search_q,
                    merchant_domain=merchant_domain,
                )
                pending_search_query = search_q
                self._buffer_gate_qa(response.text, search_text)
                if self.callbacks.on_text:
                    await self.callbacks.on_text(search_text)
                if self.callbacks.on_bubble_end:
                    await self.callbacks.on_bubble_end()
                continue

            # If we're in a search picker state and the user typed a response,
            # resolve their pick using numeric reference or LLM classification.
            if pending_search_query is not None and action.kind == "answer":
                # Try numeric resolution against the current cache
                cache = ctx.session.last_discovered_products or []
                pid = self._resolve_numeric_reference(response.text, cache)
                if pid is not None:
                    # Resolve to the cached product
                    cached = next(
                        (p for p in cache if isinstance(p, dict) and p.get("product_id") == pid),
                        None,
                    )
                    if cached:
                        action = GateAction(
                            kind="add",
                            new_item={
                                "product_id": pid,
                                "name": cached.get("name", ""),
                                "price": str(cached.get("price", "0")),
                                "quantity": 1,
                            },
                            text="",
                        )
                        pending_search_query = None
                    # fall through to _apply_gate_action with the add action
                elif response.text.strip().lower() in {
                    "cancel that",
                    "cancel",
                    "none",
                    "no",
                }:
                    pending_search_query = None
                    display_text = "Search cancelled. The basket is unchanged."
                    self._buffer_gate_qa(response.text, display_text)
                    if self.callbacks.on_text:
                        await self.callbacks.on_text(display_text)
                    if self.callbacks.on_bubble_end:
                        await self.callbacks.on_bubble_end()
                    continue
                else:
                    # Unrecognised picker input — re-show the options
                    re_search_text, _ = await self._search_and_offer_sub_flow(
                        ctx,
                        query=pending_search_query,
                        merchant_domain=merchant_domain,
                    )
                    self._buffer_gate_qa(response.text, re_search_text)
                    if self.callbacks.on_text:
                        await self.callbacks.on_text(re_search_text)
                    if self.callbacks.on_bubble_end:
                        await self.callbacks.on_bubble_end()
                    continue

            # Safety rail: if the action is "add", verify the new_item is
            # actually in the discovery cache. We can't trust the model to
            # invent items the user hasn't seen.
            if action.kind == "add":
                cache_ids = {
                    p.get("product_id")
                    for p in (ctx.session.last_discovered_products or [])
                    if isinstance(p, dict)
                }
                already_in_basket = any(
                    i["product_id"] == (action.new_item or {}).get("product_id")
                    for i in basket_items
                )
                pid = (action.new_item or {}).get("product_id", "")
                if pid not in cache_ids and not already_in_basket:
                    display_text = (
                        "I don't have that item in my recent search results. "
                        "To add it, finish this purchase (or cancel) and ask "
                        "me to search for it first."
                    )
                    self._buffer_gate_qa(response.text, display_text)
                    if self.callbacks.on_text:
                        await self.callbacks.on_text(display_text)
                    if self.callbacks.on_bubble_end:
                        await self.callbacks.on_bubble_end()
                    continue

            # Apply the action — friendly messages for refusals.
            new_text, candidate_basket = self._apply_gate_action(
                action,
                basket_items,
                merchant_domain=merchant_domain,
            )

            # Safety rail: if the mutation would push the new total over the
            # per-transaction cap (or break any AP2 rule), refuse with a
            # friendly message and KEEP the original basket.
            if candidate_basket is not basket_items:
                candidate_total = self._compute_basket_total(candidate_basket)
                # Re-validate mandate at the proposed total.
                # Note: an empty candidate basket short-circuits the next loop
                # iteration's cancellation path, so we let it through.
                if candidate_basket:
                    auth = ctx.ap2.verify_and_authorize(
                        self.mandate_id,
                        candidate_total,
                        vendor=merchant_domain,
                    )
                    if not auth.authorized:
                        display_text = self._friendly_cap_refusal(
                            ctx,
                            auth.reason,
                            candidate_total,
                            basket_items,
                            attempted_basket=candidate_basket,
                        )
                        self._buffer_gate_qa(response.text, display_text)
                        if self.callbacks.on_text:
                            await self.callbacks.on_text(display_text)
                        if self.callbacks.on_bubble_end:
                            await self.callbacks.on_bubble_end()
                        continue
                # Modification accepted
                basket_items = candidate_basket
                display_text = new_text
            else:
                display_text = new_text

            if self.callbacks.on_text:
                await self.callbacks.on_text(display_text)
            if self.callbacks.on_bubble_end:
                await self.callbacks.on_bubble_end()
            self._buffer_gate_qa(response.text, display_text)

            # Phase 8f: when this iteration was a non-mutating answer
            # (basket unchanged), set the flag so the NEXT iteration's
            # gate event carries ``is_answer_only=True``. The web modal
            # reads this hint and stays HIDDEN — the user is already
            # reading the answer in chat. Mutations leave the flag
            # False so the next gate event re-renders the modal with
            # the updated basket. The Q&A loop continues either way.
            if action.kind == "answer" and candidate_basket is basket_items:
                answer_only_for_next_iter = True

            # Refinement D: only non-empty-basket Q&A counts against the cap.
            # Empty-basket browsing is legitimate and stays open until the
            # user explicitly acts or the absolute iteration ceiling hits.
            if basket_items:
                non_empty_questions += 1
                if non_empty_questions > self.MAX_GATE_QUESTIONS:
                    cancelled_reason = "max_questions_reached"
                    break
            # loop back to the gate (with possibly mutated basket)

        # Recompute total + tier for the final audit & subagent dispatch
        total = self._compute_basket_total(basket_items)
        tier = (
            classify_gate(
                total,
                is_first_purchase_from_merchant=is_new_merchant,
            )
            if basket_items
            else "n/a"
        )

        # If we exited the loop without explicit approval/cancel, treat as
        # an iteration-cap timeout — distinct from a user-initiated cancel.
        if (
            cancelled_reason is None
            and not approved
            and total_iterations >= ABSOLUTE_MAX_ITERATIONS
        ):
            cancelled_reason = "iteration_limit_reached"

        if not approved:
            # Compose a status string that reflects WHY the flow ended, so
            # the caller can emit context-appropriate messaging instead of
            # always saying "purchase cancelled at the gate".
            status = (
                "cancelled_by_user"
                if cancelled_reason in (None, "basket_emptied_by_user")
                else "gate_closed"
            )
            await shared_tools.audit_log(
                ctx,
                agent="OrchestratorAgent",
                tool="hitl_gate",
                action=f"cancelled tier={tier} amount={total}"
                + (f" reason={cancelled_reason}" if cancelled_reason else ""),
                mandate_id=self.mandate_id,
                args={
                    "merchant": merchant_domain,
                    "amount": str(total),
                    "item_count": len(basket_items),
                    "reason": cancelled_reason,
                },
            )
            return {
                "status": status,
                "tier": tier,
                "merchant_domain": merchant_domain,
                "amount": str(total),
                "reason": cancelled_reason,
                "basket_was_empty": not basket_items,
            }

        await shared_tools.audit_log(
            ctx,
            agent="OrchestratorAgent",
            tool="hitl_gate",
            action=f"approved tier={tier} amount={total} items={len(basket_items)}",
            mandate_id=self.mandate_id,
            args={
                "merchant": merchant_domain,
                "amount": str(total),
                "item_count": len(basket_items),
            },
        )

        items_desc = "\n".join(
            f"  - product_id={i['product_id']} name={i['name']!r} "
            f"price={i['price']} qty={i['quantity']}"
            for i in basket_items
        )
        message = (
            f"{brief}\n\n"
            f"Basket details:\n"
            f"- merchant_domain: {merchant_domain}\n"
            f"- mandate_id: {self.mandate_id}\n"
            f"- total_amount: {total}\n"
            f"- items ({len(basket_items)}):\n{items_desc}\n\n"
            f"Execute the full purchase chain for all items in one session. "
            f"Return the JSON output."
        )
        await self._emit_tool_start(
            "call_purchase_agent",
            {
                "merchant": merchant_domain,
                "amount": str(total),
                "item_count": len(basket_items),
            },
        )
        result = await self._purchase.run(ctx, message)
        await self._emit_tool_end("call_purchase_agent", result)

        # Phase 8h: when the purchase succeeds, purge the just-bought
        # items from the user's click_basket (the cart). Without this,
        # items would linger in the cart after being bought, which is
        # both visually wrong (badge still shows them) and operationally
        # confusing (next "buy what's in my cart" would re-order them).
        # We match by product_id within the same merchant; items in
        # other merchants are untouched.
        purchased_status = (result or {}).get("status") if isinstance(result, dict) else None
        if purchased_status in ("completed", "succeeded"):
            self._purge_purchased_from_cart(
                ctx,
                merchant_domain,
                basket_items,
            )

        return result

    def _purge_purchased_from_cart(
        self,
        ctx: ToolContext,
        merchant_domain: str,
        purchased_items: list[dict],
    ) -> None:
        """Remove purchased product_ids from ``ctx.session.click_basket``
        and fire a cart_update SSE event so the header badge syncs.

        Items purchased from the gate basket but not present in the
        click_basket are no-ops (e.g., the user purchased without
        adding to cart first). Items in other merchants are
        untouched.
        """
        try:
            bucket = ctx.session.click_basket.get(merchant_domain, [])
            if not bucket:
                return
            purchased_ids = {
                str(p.get("product_id"))
                for p in (purchased_items or [])
                if isinstance(p, dict) and p.get("product_id")
            }
            if not purchased_ids:
                return
            remaining = [
                line for line in bucket if str(line.get("product_id")) not in purchased_ids
            ]
            if remaining:
                ctx.session.click_basket[merchant_domain] = remaining
            else:
                ctx.session.click_basket.pop(merchant_domain, None)

            # Notify the web layer so the badge updates immediately
            # (mirrors the cart_event_notifier path used by add_to_cart).
            notifier = getattr(ctx, "cart_event_notifier", None)
            if notifier is not None:
                total = sum(
                    int(item.get("quantity", 0) or 0)
                    for items in ctx.session.click_basket.values()
                    for item in items
                )
                try:
                    notifier({"type": "cart_update", "data": {"count": total}})
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001 — never fail a purchase on bookkeeping
            import sys

            print(
                f"[orchestrator] _purge_purchased_from_cart swallowed: {type(exc).__name__}: {exc}",
                file=sys.stdout,
                flush=True,
            )

    # ── basket-edit helpers ──────────────────────────────────────────────────

    @staticmethod
    def _normalise_basket(items: list[dict]) -> list[dict]:
        """Convert raw item dicts to canonical basket entries with line_total.

        Server-side normalisation: model-provided fields are coerced to the
        canonical types so the rest of the gate logic can trust them.
        Items with quantity <= 0 are dropped (handles "qty 0 = remove").
        """
        out = []
        for item in items:
            qty = int(item.get("quantity", 1))
            if qty <= 0:
                continue
            price = Decimal(str(item.get("price", "0")))
            line_total = price * qty
            out.append(
                {
                    "product_id": item["product_id"],
                    "name": item.get("name", ""),
                    "price": str(price),
                    "quantity": qty,
                    "line_total": str(line_total),
                }
            )
        return out

    @staticmethod
    def _compute_basket_total(basket_items: list[dict]) -> Decimal:
        """Sum of line_totals across the basket."""
        return sum((Decimal(i["line_total"]) for i in basket_items), start=Decimal("0"))

    def _build_gate_data(
        self,
        merchant_domain: str,
        basket_items: list[dict],
        total: Decimal,
        tier: str,
        *,
        answer_only: bool = False,
    ) -> GateData:
        """Construct a GateData with the appropriate item summary + full summary.

        ``answer_only`` (Phase 8f): True when this gate is being
        re-presented after a non-mutating Q&A iteration — the web
        modal will keep itself hidden so the user can read the
        agent's reply in chat. Mutations leave it False so the
        modal re-renders with the updated basket.
        """
        if len(basket_items) == 1:
            i = basket_items[0]
            item_summary = f"{i['name']} × {i['quantity']} @ ${i['price']} from {merchant_domain}"
        else:
            item_summary = (
                f"{len(basket_items)} items from {merchant_domain} — basket total ${total}"
            )

        full_summary = None
        if tier == "explicit_with_summary":
            full_summary = f"BASKET SUMMARY  ({merchant_domain})\n" + "\n".join(
                f"  {i['name']} × {i['quantity']}  ${i['line_total']}" for i in basket_items
            )

        return GateData(
            merchant_domain=merchant_domain,
            amount=total,
            currency="USD",
            item_summary=item_summary,
            items=basket_items,
            full_summary=full_summary,
            is_answer_only=answer_only,
        )

    async def _handle_gate_input(
        self,
        ctx: ToolContext,
        *,
        user_input: str,
        merchant_domain: str,
        basket_items: list[dict],
        total: Decimal,
    ) -> GateAction:
        """Classify the user's free-text input at the gate.

        Returns a GateAction describing what to do: answer the question,
        remove/change/add an item, or refuse if the request isn't fulfillable.

        Uses Claude with a strict JSON output contract. Falls back to a plain
        "answer" action if the model returns non-JSON or an unparseable shape
        (this preserves backward compatibility with the old text-only tests).
        """
        cache = ctx.session.last_discovered_products or []
        cache_blob = json.dumps(cache[:20], default=str) if cache else "[]"
        basket_blob = json.dumps(basket_items, default=str)

        # Recent context: helps with "why did YOU say X" questions
        recent = self._summarise_recent_conversation(
            ctx.session.conversation,
            max_turns=8,
        )
        for entry in self._pending_gate_history[-6:]:
            text = self._extract_text_from_entry(entry)
            if text:
                recent.append(f"{entry['role']}: {text}")
        history_blob = "\n".join(recent) if recent else "(no prior context)"

        system_prompt = (
            "You are handling the user's input at a purchase confirmation gate.\n"
            "Confirm and cancel intents are handled by the runtime BEFORE\n"
            "you see them — confirm/CONFIRM/'yes buy'/'approve' all resolve\n"
            "the gate, and cancel/'no'/'stop' all abort it (case-insensitive,\n"
            "any reasonable phrasing). So the input you receive is something\n"
            "other than a plain confirm/cancel: a question, a basket edit, or\n"
            "a clarification request.\n"
            "Classify their intent and respond with STRICT JSON.\n\n"
            "PROSE GUIDANCE:\n"
            "When you generate an 'answer' text that nudges the user toward\n"
            "confirming or cancelling, write 'confirm' / 'cancel' in plain\n"
            "lowercase. Do NOT say 'type CONFIRM (all caps)' or anything\n"
            "suggesting case matters — it doesn't.\n\n"
            "INTENTS:\n"
            "- 'answer': question, clarification, or unsupported request.\n"
            "- 'remove': remove an item. target_product_id matches basket item.\n"
            "- 'change_quantity': update item qty. target_product_id + "
            "new_quantity (0 = remove).\n"
            "- 'add': add a new item. new_product_id must be in DISCOVERY CACHE.\n"
            "- 'swap': replace one basket item with another in one step. "
            "target_product_id = item to remove; new_product_id/name/price = "
            "replacement (from cache or being added anew).\n"
            "- 'clear': empty the entire basket. No fields needed beyond answer.\n\n"
            "RULES:\n"
            "1. Be honest. If the user asks for something the system can't do "
            "in this gate (swap merchants, change shipping), use intent='answer' "
            "and offer a concrete recovery path. For cross-merchant requests "
            "say something like: 'This gate is for {merchant_domain} only. "
            "After you finish or cancel this purchase, I can search "
            "[other_merchant] for [item].'\n"
            "2. For 'remove'/'change_quantity' — match generously by substring: "
            "'tumbler' should match 'Travel Coffee Tumbler'. Set "
            "target_product_id to the matching basket item's product_id. "
            "If MULTIPLE items match the same substring, use intent='answer' "
            "and list the numbered options so the user can pick — do NOT guess.\n"
            "3. For 'add' — if the item IS in the discovery cache, use intent='add'. "
            "If NOT in the cache, use intent='answer' with text starting "
            "'I'll search for [item] at [merchant] now.' (any close variant is "
            "fine — 'Let me look up [item]', 'I'll find that for you', etc. — "
            "the system triggers an inline search at the current merchant).\n"
            "4. Do NOT compute 'new basket total' or 'leaves you with' in your "
            "answer text — the system shows the updated gate.\n"
            "5. For 'add', copy price/name exactly from the cache.\n"
            "6. No emojis. No 'would you like to purchase' nudges. Be brief.\n"
            "7. Use the conversation context. Don't deny things you said earlier.\n"
            "8. RELATIVE QUANTITY PHRASES ('+N', '-N', 'add N more', 'remove N',\n"
            "   'N fewer', 'one more', 'two less', etc.):\n"
            "   - Compute new_quantity = current_quantity ± N from the BASKET JSON.\n"
            "   - If the basket has ONE item and no product is named, apply the\n"
            "     delta to that item.\n"
            "   - If the basket has MULTIPLE items and the phrase does not clearly\n"
            "     identify which item, use intent='answer' and ask the user to\n"
            "     specify which item before acting — do NOT guess.\n"
            "   - new_quantity must be ≥ 1; use intent='remove' to drop to zero.\n"
            "9. NON-QUANTITY EDIT REQUESTS (colour, size, variant, brand, model):\n"
            "   - These cannot be changed at this gate. Use intent='answer', explain\n"
            "     briefly, and suggest: 'Cancel this purchase and search for\n"
            "     [specific variant] instead.'\n"
            "10. UNCERTAIN INTENT: when you are unsure what the user wants, use\n"
            "    intent='answer' and ask one specific clarifying question rather\n"
            "    than guessing.\n\n"
            "OUTPUT FORMAT — STRICT JSON, NO PROSE:\n"
            "{\n"
            '  "intent": "answer" | "remove" | "change_quantity" | "add" | "swap" | "clear",\n'
            '  "target_product_id": "<id from basket — for remove/change_quantity>",\n'
            '  "new_quantity": <int — for change_quantity, 0=remove>,\n'
            '  "new_product_id": "<id from discovery cache — for add>",\n'
            '  "new_product_name": "<name — for add>",\n'
            '  "new_product_price": "<price as string — for add>",\n'
            '  "new_product_quantity": <int — for add, default 1>,\n'
            '  "answer": "<brief plain text shown to the user>"\n'
            "}\n\n"
            f"Merchant: {merchant_domain}\n"
            f"BASKET (JSON): {basket_blob}\n"
            f"Basket total: ${total}\n"
            f"DISCOVERY CACHE (recent search results, JSON): {cache_blob}\n\n"
            f"RECENT CONVERSATION (most recent last):\n{history_blob}\n"
        )

        try:
            resp = await self._client.messages.create(
                model=self.model,
                max_tokens=800,
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=[],  # no tools — pure response, no orchestration
                messages=[{"role": "user", "content": user_input}],
            )
        except Exception as e:
            return GateAction(
                kind="answer",
                text=(
                    f"(Couldn't reach the agent to handle that — "
                    f"{type(e).__name__}). The gate is still open."
                ),
            )

        # Collect text from response blocks
        text_parts = []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(getattr(block, "text", ""))
        raw_text = "\n".join(text_parts).strip()

        # Try to parse as structured JSON
        parsed = self._try_parse_json(raw_text)
        if isinstance(parsed, dict) and "intent" in parsed:
            return self._gate_action_from_parsed(parsed, raw_text=raw_text)

        # Fallback: treat the whole response as a plain answer
        return GateAction(
            kind="answer",
            text=raw_text or "(no answer returned)",
        )

    @staticmethod
    def _try_parse_json(text: str) -> Any:
        """Attempt to parse text as JSON, including fenced code blocks."""
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Strip ```json ... ``` fences if present
        if "```" in text:
            for chunk in text.split("```")[1::2]:
                cleaned = chunk.lstrip("json").strip()
                try:
                    return json.loads(cleaned)
                except json.JSONDecodeError:
                    continue
        # Last resort: find the first {...} JSON object
        start = text.find("{")
        end = text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        return None

    @staticmethod
    def _gate_action_from_parsed(parsed: dict, *, raw_text: str = "") -> GateAction:
        """Convert the model's parsed JSON into a GateAction."""
        intent = parsed.get("intent", "answer")
        answer_text = parsed.get("answer", "") or raw_text

        if intent == "remove":
            return GateAction(
                kind="remove",
                text=answer_text,
                target_product_id=parsed.get("target_product_id", ""),
            )
        if intent == "change_quantity":
            return GateAction(
                kind="change_quantity",
                text=answer_text,
                target_product_id=parsed.get("target_product_id", ""),
                new_quantity=int(parsed.get("new_quantity", 1)),
            )
        if intent == "add":
            new_item = {
                "product_id": parsed.get("new_product_id", ""),
                "name": parsed.get("new_product_name", ""),
                "price": str(parsed.get("new_product_price", "0")),
                "quantity": int(parsed.get("new_product_quantity", 1)),
            }
            return GateAction(kind="add", text=answer_text, new_item=new_item)
        if intent == "clear":
            return GateAction(kind="clear", text=answer_text)
        if intent == "swap":
            new_item = {
                "product_id": parsed.get("new_product_id", ""),
                "name": parsed.get("new_product_name", ""),
                "price": str(parsed.get("new_product_price", "0")),
                "quantity": int(parsed.get("new_product_quantity", 1)),
            }
            return GateAction(
                kind="swap",
                text=answer_text,
                target_product_id=parsed.get("target_product_id", ""),
                new_item=new_item,
            )
        # Default / fallback
        return GateAction(kind="answer", text=answer_text)

    def _apply_gate_action(
        self,
        action: GateAction,
        basket_items: list[dict],
        *,
        merchant_domain: str,
    ) -> tuple[str, list[dict]]:
        """Apply a GateAction to the basket. Returns (display_text, new_basket).

        Safety rails return the basket unchanged with a friendly, factual
        explanation of why the modification couldn't be applied. The goal is
        to make every refusal feel like a helpful nudge, not an abrupt cut.
        """
        if action.kind == "answer":
            return action.text, basket_items

        # Look up the cap from the mandate so we can include it in messages
        cap_text = ""
        if self.mandate_id:
            mandate = self.ap2.get_mandate(self.mandate_id) if hasattr(self, "ap2") else None
            if mandate is None:
                # Re-fetch via the same context we're handed
                pass

        if action.kind == "remove":
            target = action.target_product_id
            existing = [i for i in basket_items if i["product_id"] == target]
            if not existing:
                numbered = self._format_basket_numbered(basket_items)
                friendly = (
                    f"I couldn't find that item in your basket. "
                    f"The basket currently has:\n{numbered}\n\n"
                    f'Say the item name, its number (e.g. "remove 1"), '
                    f"or its id — or type cancel."
                )
                return friendly, basket_items
            removed = existing[0]
            new_basket = [i for i in basket_items if i["product_id"] != target]
            friendly = f"Removed {removed['name']} (${removed['line_total']}) from your basket."
            return friendly, new_basket

        if action.kind == "change_quantity":
            target = action.target_product_id
            new_qty = action.new_quantity
            existing = [i for i in basket_items if i["product_id"] == target]
            if not existing:
                numbered = self._format_basket_numbered(basket_items)
                friendly = (
                    f"I couldn't find that item in your basket. "
                    f"The basket currently has:\n{numbered}\n\n"
                    f"Say the item name or number to update its quantity."
                )
                return friendly, basket_items
            if new_qty < 0:
                return ("Quantity must be zero or positive.", basket_items)
            if new_qty == 0:
                # Treat as remove
                removed = existing[0]
                new_basket = [i for i in basket_items if i["product_id"] != target]
                friendly = f"Removed {removed['name']} from your basket (quantity set to 0)."
                return friendly, new_basket
            # Apply quantity change
            item = existing[0]
            old_qty = item["quantity"]
            price = Decimal(item["price"])
            new_basket = [
                {**i, "quantity": new_qty, "line_total": str(price * new_qty)}
                if i["product_id"] == target
                else i
                for i in basket_items
            ]
            friendly = f"Updated {item['name']} quantity from {old_qty} to {new_qty}."
            return friendly, new_basket

        if action.kind == "add":
            new_item = action.new_item or {}
            product_id = new_item.get("product_id", "")

            # Safety rail: don't trust the model's claimed product details.
            # Verify the product is in the discovery cache (or already in basket).
            # We can't access ctx here directly; the caller (gate loop) will
            # need to validate this. For now, we accept the model's data and
            # apply — the caller's cap check will still hold.

            # Reject empty/malformed adds
            if not product_id or not new_item.get("name"):
                friendly = (
                    "I couldn't identify a specific product to add. "
                    "Could you tell me which item by name? "
                    "If it wasn't in my recent search results, type cancel "
                    "and ask me to search for it first."
                )
                return friendly, basket_items

            # If the item is already in the basket, treat as quantity bump
            existing = [i for i in basket_items if i["product_id"] == product_id]
            if existing:
                cur_qty = existing[0]["quantity"]
                add_qty = int(new_item.get("quantity", 1))
                price = Decimal(existing[0]["price"])
                new_qty = cur_qty + add_qty
                new_basket = [
                    {**i, "quantity": new_qty, "line_total": str(price * new_qty)}
                    if i["product_id"] == product_id
                    else i
                    for i in basket_items
                ]
                friendly = f"Increased {existing[0]['name']} from {cur_qty} to {new_qty}."
                return friendly, new_basket

            # Otherwise, add as a new line item
            try:
                price = Decimal(str(new_item.get("price", "0")))
            except Exception:
                friendly = (
                    "I couldn't add that — the price wasn't a valid number. "
                    "Could you say which item from the recent search results "
                    "you'd like to add?"
                )
                return friendly, basket_items

            if price <= 0:
                friendly = (
                    "I couldn't add that — the price wasn't valid. "
                    "Try saying the item's full name from the recent search."
                )
                return friendly, basket_items

            qty = int(new_item.get("quantity", 1))
            if qty <= 0:
                qty = 1
            entry = {
                "product_id": product_id,
                "name": new_item.get("name", ""),
                "price": str(price),
                "quantity": qty,
                "line_total": str(price * qty),
            }
            new_basket = basket_items + [entry]
            friendly = f"Added {entry['name']} × {qty} (${entry['line_total']}) to your basket."
            return friendly, new_basket

        if action.kind == "clear":
            if not basket_items:
                return "The basket is already empty.", basket_items
            count = len(basket_items)
            friendly = f"Cleared {count} item{'s' if count != 1 else ''} from your basket."
            return friendly, []

        if action.kind == "swap":
            target = action.target_product_id
            new_item = action.new_item or {}
            # Validate remove target
            existing = [i for i in basket_items if i["product_id"] == target]
            if not existing:
                numbered = self._format_basket_numbered(basket_items)
                return (
                    f"I couldn't find the item to swap out in your basket.\n"
                    f"{numbered}\n\nSpecify exactly which item to replace.",
                    basket_items,
                )
            removed = existing[0]
            # Validate new item
            if not new_item.get("product_id") or not new_item.get("name"):
                return (
                    "I couldn't identify the replacement item. Try 'add [item name]' separately.",
                    basket_items,
                )
            try:
                new_price = Decimal(str(new_item.get("price", "0")))
            except Exception:
                return ("The replacement item has an invalid price.", basket_items)
            new_qty = int(new_item.get("quantity", 1)) or 1
            new_entry = {
                "product_id": new_item["product_id"],
                "name": new_item["name"],
                "price": str(new_price),
                "quantity": new_qty,
                "line_total": str(new_price * new_qty),
            }
            # Substitute in place — the new item lands at the same basket
            # position the old one occupied. Preserves the user's mental
            # numbering (otherwise a swap shuffles items around).
            new_basket = [(new_entry if i["product_id"] == target else i) for i in basket_items]
            old_total = Decimal(removed["line_total"])
            new_total = new_price * new_qty
            delta = new_total - old_total
            delta_str = f"+${delta}" if delta >= 0 else f"-${abs(delta)}"
            friendly = (
                f"Swapped {removed['name']} (${removed['line_total']}) "
                f"for {new_entry['name']} (${new_entry['line_total']}) "
                f"[{delta_str}]."
            )
            return friendly, new_basket

        # Unknown action kind — return text as-is
        return action.text or "(no change)", basket_items

    @staticmethod
    def _format_basket_numbered(basket_items: list[dict]) -> str:
        """Return a numbered list of basket items for disambiguation messages.

        e.g.
          1. Ceramic Coffee Mug × 1 — $14  [id: cof_001]
          2. Travel Coffee Tumbler × 1 — $28  [id: cof_002]

        Includes product_id so an agent caller can resolve unambiguously.
        """
        lines = []
        for i, item in enumerate(basket_items, start=1):
            lines.append(
                f"  {i}. {item['name']} × {item['quantity']} — "
                f"${item['line_total']}  [id: {item['product_id']}]"
            )
        return "\n".join(lines)

    @staticmethod
    def _resolve_numeric_reference(user_input: str, basket_items: list[dict]) -> str | None:
        """If the user said 'remove 1' / 'drop 2' / '3', resolve to product_id.

        Returns the product_id at that 1-based position, or None if the input
        doesn't look like a numeric basket reference. Handles both humans
        ("remove 1") and agents passing a bare integer string ("2").
        This runs in Python before hitting the LLM — no round-trip needed.

        Crucially, only treats input as a numeric reference when the ENTIRE
        input is either:
          a) a bare integer ("1", "2")
          b) a recognised action verb followed by whitespace + integer
             ("remove 1", "drop 2", "item 3", "#2")
        This prevents product_ids like "cof_001" from being mis-parsed.
        """
        import re

        text = user_input.strip()
        # Bare integer — agent canonical form
        if re.match(r"^\d+$", text):
            n = int(text)
            if 1 <= n <= len(basket_items):
                return basket_items[n - 1]["product_id"]
            return None
        # "remove 1" / "drop 2" / "item 3" / "#3" — verb + space + integer
        m = re.match(
            r"^(?:remove|drop|delete|item|#)\s+(\d+)$",
            text,
            re.IGNORECASE,
        )
        if m:
            n = int(m.group(1))
            if 1 <= n <= len(basket_items):
                return basket_items[n - 1]["product_id"]
        return None

    @staticmethod
    def _looks_like_search_intent(text: str) -> bool:
        """Heuristically detect 'I'll search for that' style answers.

        Returns True when the answer text contains an action verb co-occurring
        with a search keyword. Tolerant of phrasing variation so the model
        doesn't have to remember an exact magic string. Avoids false positives
        like 'the search results were great' (no action verb).
        """
        t = (text or "").lower()
        search_words = (
            "search",
            "look up",
            "look for",
            "find that",
            "find it",
            "look that up",
            "check the catalogue",
            "check the catalog",
        )
        # Future-tense action signal — distinguishes "I'll search" from "the
        # search showed nothing" or "your search returned X".
        action_words = (
            "i'll",
            "i will",
            "let me",
            "going to",
            "will search",
            "i'm going",
            "shall ",
            "we'll",
            "we will",
        )
        has_search = any(w in t for w in search_words)
        has_action = any(a in t for a in action_words)
        return has_search and has_action

    async def _search_and_offer_sub_flow(
        self,
        ctx: ToolContext,
        *,
        query: str,
        merchant_domain: str,
        max_results: int = 8,
    ) -> tuple[str, list[dict]]:
        """Search the current merchant for ``query`` and present results inline.

        Returns (display_text, found_products_as_cache_dicts).
        display_text is shown to the user immediately; products are added to
        the discovery cache so a subsequent "add" intent can resolve them.

        Works for both human users (read numbered list, type a number) and
        agent callers (read the structured product_id list in the text).
        """
        from tools.discovery_tools import search_products

        results = await search_products(
            ctx,
            query=query,
            merchant_domains=[merchant_domain],
            limit_per_merchant=max_results,
        )
        if not results:
            return (
                f"I searched {merchant_domain} for '{query}' and found nothing. "
                f"Try a different name, or type cancel to abort.",
                [],
            )

        # Populate the discovery cache so the picked item can be "add"ed
        cache = list(ctx.session.last_discovered_products or [])
        existing_ids = {p.get("product_id") for p in cache if isinstance(p, dict)}
        for p in results:
            if p.product_id not in existing_ids:
                cache.append(
                    {
                        "product_id": p.product_id,
                        "name": p.name,
                        "price": str(p.price),
                        "merchant_domain": p.merchant_domain,
                        "in_stock": p.in_stock,
                    }
                )
        ctx.session.last_discovered_products = cache

        if len(results) == 1:
            p = results[0]
            text = (
                f"Found 1 match at {merchant_domain}:\n"
                f"  1. {p.name} — ${p.price}"
                + (" (out of stock)" if not p.in_stock else "")
                + f"  [id: {p.product_id}]\n\n"
                f'Type "1" or "add {p.name}" to add it, '
                f'or "cancel that" to keep the basket as-is.'
            )
        else:
            lines = []
            for i, p in enumerate(results, start=1):
                oos = " (out of stock)" if not p.in_stock else ""
                lines.append(f"  {i}. {p.name} — ${p.price}{oos}  [id: {p.product_id}]")
            text = (
                f"Found {len(results)} results at {merchant_domain}:\n"
                + "\n".join(lines)
                + "\n\nType a number to add that item, "
                'or "cancel that" to keep the basket unchanged.'
            )
        return text, [r.__dict__ if hasattr(r, "__dict__") else {} for r in results]

    def _buffer_gate_qa(self, user_input: str, assistant_text: str) -> None:
        """Append the Q&A to the post-flight buffer.

        Direct append to ctx.session.conversation would corrupt the
        tool_use → tool_result adjacency the Anthropic API requires while
        the orchestrator is mid-dispatch. The buffer is flushed by
        ``run()`` after super().run() completes.
        """
        self._pending_gate_history.append(
            {
                "role": "user",
                "content": [{"type": "text", "text": f"[at confirmation gate] {user_input}"}],
            }
        )
        self._pending_gate_history.append(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": assistant_text}],
            }
        )

    def _friendly_cap_refusal(
        self,
        ctx: ToolContext,
        reason: str,
        attempted_total: Decimal,
        original_basket: list[dict],
        attempted_basket: list[dict],
    ) -> str:
        """Generate a customer-friendly explanation when a basket mutation
        would push the total past the mandate's cap.

        Tells the user:
          - WHAT they tried to do (in plain language)
          - WHY it didn't go through (which cap was hit, and the headroom)
          - WHAT they can do instead (remove a different item, smaller qty,
            or cancel and start a smaller order)
        """
        mandate = ctx.ap2.get_mandate(self.mandate_id) if self.mandate_id else None

        # Figure out what changed in the attempt
        orig_ids = {i["product_id"] for i in original_basket}
        attempt_ids = {i["product_id"] for i in attempted_basket}
        added_ids = attempt_ids - orig_ids
        added_names = [i["name"] for i in attempted_basket if i["product_id"] in added_ids]

        if added_names:
            attempted_str = (
                f"Adding {' and '.join(added_names)} would bring the basket to ${attempted_total}"
            )
        else:
            # Could be a quantity bump
            attempted_str = f"That change would bring the basket to ${attempted_total}"

        original_total = self._compute_basket_total(original_basket)

        if reason == "exceeds_per_transaction_cap" and mandate:
            cap = mandate.max_amount
            overage = attempted_total - cap
            drop_suggestions = self._suggest_drops_to_fit(
                original_basket,
                overage,
                max_suggestions=3,
            )
            if drop_suggestions:
                drops_text = "\n".join(
                    f"  - Remove {s['name']} (${s['line_total']}) "
                    f"→ basket would be ${s['post_remove_total']}"
                    for s in drop_suggestions
                )
                return (
                    f"{attempted_str}, which is over your ${cap} "
                    f"per-transaction limit by ${overage}.\n\n"
                    f"To make room, you could:\n{drops_text}\n"
                    f"  - Proceed with the current ${original_total} basket "
                    f"(no change)\n\n"
                    f'Just say "remove [item]" or "no thanks".'
                )
            # Basket too small to drop-and-fit. Different copy for the
            # common single-item-basket case (Fix #13).
            if len(original_basket) == 1:
                only = original_basket[0]
                return (
                    f"{attempted_str}, which is over your ${cap} "
                    f"per-transaction limit by ${overage}.\n\n"
                    f"Your basket has only one item — "
                    f"{only['name']} (${only['line_total']}) — so there's "
                    f"nothing to drop. To proceed, try a lower quantity, "
                    f"a cheaper alternative, or keep the current basket "
                    f"and skip this addition."
                )
            return (
                f"{attempted_str}, which is over your ${cap} per-transaction "
                f"limit by ${overage}. None of the current basket items are "
                f"large enough on their own to make room for this change. "
                f"Try a lower quantity, a cheaper alternative, or proceed "
                f"with the current basket of ${original_total}."
            )
        if reason == "exceeds_daily_cap" and mandate:
            cap = mandate.daily_cap
            return (
                f"{attempted_str}, which would push your daily spending past "
                f"your ${cap} limit for today. The current basket "
                f"(${original_total}) is still within your daily limit. "
                f"You could finish this purchase as-is, or wait until "
                f"tomorrow to add more."
            )
        if reason == "exceeds_monthly_cap" and mandate:
            cap = mandate.monthly_cap
            return (
                f"{attempted_str}, which would exceed your ${cap} monthly "
                f"spending limit. Consider proceeding with the smaller "
                f"basket of ${original_total}."
            )
        if reason == "mandate_revoked":
            return (
                "Your spending mandate has been revoked, so I can't make "
                "any changes to this purchase. Type cancel to abort, then "
                "create a new mandate to resume shopping."
            )
        if reason == "mandate_expired":
            return (
                "Your spending mandate has expired, so I can't make any "
                "changes. Type cancel and create a fresh mandate to keep "
                "shopping."
            )
        # Generic fallback
        return (
            f"{attempted_str} — that change couldn't be applied "
            f"({reason or 'unknown reason'}). Your current basket of "
            f"${original_total} is still intact. Try a smaller change or "
            f"type cancel to start over."
        )

    @staticmethod
    def _suggest_drops_to_fit(
        basket_items: list[dict],
        overage: Decimal,
        *,
        max_suggestions: int = 3,
    ) -> list[dict]:
        """Find items whose line_total ≥ overage. Removing any one would
        bring the proposed total back under the cap.

        Returns up to ``max_suggestions`` items, each annotated with a
        ``post_remove_total`` showing what the basket would total after
        removing just that item (so the user can see the impact).

        Ordering: smallest line_total first that still covers the overage
        — i.e. the least-painful drop. If nothing covers the overage,
        returns []  (caller falls back to "lower quantity / proceed").
        """
        original_total = OrchestratorAgent._compute_basket_total(basket_items)
        candidates = [i for i in basket_items if Decimal(i["line_total"]) >= overage]
        # Sort: line_total ascending (smallest sacrifice first)
        candidates.sort(key=lambda i: Decimal(i["line_total"]))
        out = []
        for item in candidates[:max_suggestions]:
            post = original_total - Decimal(item["line_total"])
            out.append(
                {
                    **item,
                    "post_remove_total": str(post),
                }
            )
        return out

    @staticmethod
    def _summarise_recent_conversation(
        conversation: list[dict],
        *,
        max_turns: int = 8,
    ) -> list[str]:
        """Pull the last N text turns from the conversation history.

        Skips tool_use / tool_result blocks (just JSON noise for the helper)
        and returns ``role: text`` strings suitable for a system prompt blob.
        """
        result: list[str] = []
        # Walk from the end backwards to pick up the most recent text-bearing
        # turns first, then reverse to chronological order at the end.
        recent: list[str] = []
        for entry in reversed(conversation):
            if len(recent) >= max_turns:
                break
            text = OrchestratorAgent._extract_text_from_entry(entry)
            if text:
                recent.append(f"{entry['role']}: {text}")
        return list(reversed(recent))

    @staticmethod
    def _extract_text_from_entry(entry: dict) -> str:
        """Pull text out of a conversation entry. Handles str or block list."""
        content = entry.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif hasattr(block, "type") and getattr(block, "type") == "text":
                    parts.append(getattr(block, "text", ""))
            return " ".join(p for p in parts if p).strip()
        return ""

    @staticmethod
    def _is_first_purchase(ctx: ToolContext, merchant_domain: str) -> bool:
        from storage.db import OrderQ

        return ctx.db.orders.get(OrderQ.merchant_domain == merchant_domain) is None

    # ── streaming callback helpers ───────────────────────────────────────────

    async def _emit_tool_start(self, name: str, args: dict) -> None:
        if self.callbacks.on_tool_start:
            await self.callbacks.on_tool_start(name, args)

    async def _emit_tool_end(self, name: str, result: Any) -> None:
        if self.callbacks.on_tool_end:
            await self.callbacks.on_tool_end(name, result)

    # ── final-text parsing override ──────────────────────────────────────────

    def _parse_final(self, text: str) -> dict:
        """Orchestrator responds with plain text for the user, not JSON."""
        return {"reply": text}

    # ── conversation memory across REPL turns ────────────────────────────────

    MAX_HISTORY_ENTRIES = 40

    async def run(self, ctx: ToolContext, user_message: str) -> dict:
        """Thread the persistent conversation history through every turn.

        The Orchestrator is stateful: each REPL prompt is a new turn in one
        long conversation. Tool results from prior turns (e.g. product lists
        the user just saw) remain visible so the user can refer back to them
        without forcing a re-search.

        Subagents stay stateless — they get a self-contained brief each call.
        """
        # Reset the gate buffer for this run
        self._pending_gate_history = []

        result = await super().run(ctx, user_message, history=ctx.session.conversation)

        # Flush any gate Q&A captured during _call_purchase to the persistent
        # conversation NOW — after run()'s tool loop has placed the tool_result
        # blocks. Appending the Q&A here keeps tool_use/tool_result adjacent
        # while preserving the Q&A for future orchestrator turns to reference.
        if self._pending_gate_history:
            ctx.session.conversation.extend(self._pending_gate_history)
            self._pending_gate_history = []

        # Soft cap: drop oldest entries when the history grows beyond MAX
        if len(ctx.session.conversation) > self.MAX_HISTORY_ENTRIES:
            ctx.session.conversation = ctx.session.conversation[-self.MAX_HISTORY_ENTRIES :]
        return result
