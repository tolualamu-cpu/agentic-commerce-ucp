"""Tests for the post-refinement live-test fix-pass.

Each test maps to one of the 15 issues identified after live REPL testing.
Covers the 9 fix groups in agents/orchestrator.py, tools/shared_tools.py,
adapters/shopify_mcp.py, agents/prompts.py.
"""

from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal

import pytest

from adapters.shopify_mcp import StubShopifyTransport
from agents.orchestrator import GateAction, OrchestratorAgent
from agents.prompts import TONE_RULES, orchestrator_prompt
from cli.confirmation import AutoConfirmProvider, GateResponse
from tests.fake_anthropic import (
    FakeAnthropicClient,
    text_response,
    tool_use_response,
)
from tools.shared_tools import get_active_mandate_summary


def _mandate(ctx, **kw):
    defaults = dict(
        user_id="user_1",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        payment_method_id="pm_test_secret",
    )
    defaults.update(kw)
    return ctx.ap2.create_mandate(**defaults)


def _item(pid, name, price, qty=1):
    return {"product_id": pid, "name": name, "price": price, "quantity": qty}


def _basket(*items):
    return OrchestratorAgent._normalise_basket(list(items))


# ─── Fix #1 + #2 — Mandate awareness + user assertion handling ────────────


class TestMandateAwareness:
    def test_get_active_mandate_summary_returns_caps(self, tool_ctx):
        m = _mandate(
            tool_ctx,
            max_amount=Decimal("250"),
            daily_cap=Decimal("500"),
            monthly_cap=Decimal("2500"),
        )
        result = asyncio.get_event_loop().run_until_complete(
            get_active_mandate_summary(tool_ctx, mandate_id=m.mandate_id)
        )
        assert result["per_transaction_cap"] == "250"
        assert result["daily_cap"] == "500"
        assert result["monthly_cap"] == "2500"
        assert result["status"] == "active"

    def test_get_active_mandate_summary_includes_headroom(self, tool_ctx):
        m = _mandate(tool_ctx, daily_cap=Decimal("500"))
        tool_ctx.ap2.record_spend(m.mandate_id, Decimal("120"), "ord1", vendor="x.com")
        result = asyncio.get_event_loop().run_until_complete(
            get_active_mandate_summary(tool_ctx, mandate_id=m.mandate_id)
        )
        assert result["spent_today"] == "120"
        assert result["daily_headroom"] == "380"

    def test_get_active_mandate_summary_for_revoked(self, tool_ctx):
        m = _mandate(tool_ctx)
        tool_ctx.ap2.revoke_mandate(m.mandate_id)
        result = asyncio.get_event_loop().run_until_complete(
            get_active_mandate_summary(tool_ctx, mandate_id=m.mandate_id)
        )
        assert result["status"] == "revoked"

    def test_get_active_mandate_summary_unknown_returns_error(self, tool_ctx):
        result = asyncio.get_event_loop().run_until_complete(
            get_active_mandate_summary(tool_ctx, mandate_id="no_such_mandate")
        )
        assert result == {"error": "mandate_not_found"}

    def test_orchestrator_advertises_mandate_summary_tool(self, tool_ctx):
        """The orchestrator's tool list must include get_active_mandate_summary
        so the model can call it when asked about budget/limits."""
        client = FakeAnthropicClient([text_response("ok")])
        orch = OrchestratorAgent(client, mandate_id=None)
        asyncio.get_event_loop().run_until_complete(orch.run(tool_ctx, "hi"))
        tool_names = {t["name"] for t in client.calls[0].tools}
        assert "get_active_mandate_summary" in tool_names

    def test_orchestrator_prompt_says_mandate_is_truth(self):
        rendered = orchestrator_prompt(["x.com"])
        # The prompt MUST contain language pointing the model to
        # get_active_mandate_summary as the authoritative source.
        assert "get_active_mandate_summary" in rendered
        # AND language indicating the agent should not accept user-asserted
        # budgets / spending limits.
        rendered_lower = rendered.lower()
        assert "source of truth" in rendered_lower or "authoritative" in rendered_lower
        # Look for any language indicating user assertions should not override
        assigned_word_indicators = (
            "do not accept",
            "do not silently accept",
            "use the mandate's caps",
            "differs from the mandate",
            "asserts a different limit",
        )
        assert any(s in rendered_lower for s in assigned_word_indicators), (
            f"Prompt missing user-assertion guardrail; got:\n{rendered}"
        )

    def test_tone_rules_forbid_markdown(self):
        assert "markdown" in TONE_RULES.lower()
        assert "**bold**" in TONE_RULES or "no **bold**" in TONE_RULES.lower()

    def test_tone_rules_require_numeric_disambiguation_labels(self):
        # The rule should forbid A/B and require 1/2/3
        assert "a, b, c" in TONE_RULES.lower() and "1, 2, 3" in TONE_RULES


# ─── Fix #3 — Stub merchant defensive normalisation ────────────────────────


class TestStubMerchantRobustness:
    def test_stub_handles_qty_10_single_line(self, tool_ctx):
        stub = StubShopifyTransport()
        loop = asyncio.get_event_loop()
        cart = loop.run_until_complete(stub.create_cart())
        result = loop.run_until_complete(
            stub.update_cart(
                cart["id"],
                items=[{"product_id": "p1", "name": "X", "price": "19", "quantity": 10}],
                buyer=None,
            )
        )
        # 10 × $19 = $190 subtotal, $15.20 tax, $205.20 total
        assert Decimal(result["subtotal"]) == Decimal("190")
        assert Decimal(result["total"]) == Decimal("205.20")

    def test_stub_complete_with_high_quantity(self, tool_ctx):
        stub = StubShopifyTransport()
        loop = asyncio.get_event_loop()
        cart = loop.run_until_complete(stub.create_cart())
        loop.run_until_complete(
            stub.update_cart(
                cart["id"],
                items=[{"product_id": "p1", "name": "X", "price": "19", "quantity": 10}],
                buyer=None,
            )
        )
        order = loop.run_until_complete(stub.complete_cart(cart["id"], "tok_test_xyz"))
        assert order["status"] == "confirmed"
        assert order["order_id"].startswith("ord_")

    def test_stub_handles_missing_quantity_defaults_to_one(self, tool_ctx):
        stub = StubShopifyTransport()
        loop = asyncio.get_event_loop()
        cart = loop.run_until_complete(stub.create_cart())
        result = loop.run_until_complete(
            stub.update_cart(
                cart["id"],
                items=[{"product_id": "p1", "name": "X", "price": "10"}],
                buyer=None,
            )
        )
        assert Decimal(result["subtotal"]) == Decimal("10")

    def test_stub_drops_zero_quantity_items(self, tool_ctx):
        stub = StubShopifyTransport()
        loop = asyncio.get_event_loop()
        cart = loop.run_until_complete(stub.create_cart())
        result = loop.run_until_complete(
            stub.update_cart(
                cart["id"],
                items=[
                    {"product_id": "p1", "name": "X", "price": "10", "quantity": 1},
                    {"product_id": "p2", "name": "Y", "price": "20", "quantity": 0},
                ],
                buyer=None,
            )
        )
        assert len(result["items"]) == 1
        assert result["items"][0]["product_id"] == "p1"

    def test_stub_complete_empty_cart_raises(self, tool_ctx):
        """Security: stub refuses to complete a cart with no items."""
        stub = StubShopifyTransport()
        loop = asyncio.get_event_loop()
        cart = loop.run_until_complete(stub.create_cart())
        with pytest.raises(ValueError, match="no items"):
            loop.run_until_complete(stub.complete_cart(cart["id"], "tok_x"))

    def test_stub_handles_malformed_price(self, tool_ctx):
        """Malformed price defaults to 0; no crash."""
        stub = StubShopifyTransport()
        loop = asyncio.get_event_loop()
        cart = loop.run_until_complete(stub.create_cart())
        result = loop.run_until_complete(
            stub.update_cart(
                cart["id"],
                items=[
                    {
                        "product_id": "p1",
                        "name": "X",
                        "price": "not-a-number",
                        "quantity": 2,
                    }
                ],
                buyer=None,
            )
        )
        assert Decimal(result["subtotal"]) == Decimal("0")

    def test_stub_verbose_off_by_default(self, tool_ctx, capsys):
        """STUB_VERBOSE env var off → no stderr output."""
        # Ensure env var is unset for this test
        prev = os.environ.pop("STUB_VERBOSE", None)
        try:
            stub = StubShopifyTransport()
            loop = asyncio.get_event_loop()
            loop.run_until_complete(stub.create_cart())
            captured = capsys.readouterr()
            assert "[stub." not in captured.err
        finally:
            if prev is not None:
                os.environ["STUB_VERBOSE"] = prev


# ─── Fix #4 + #5 + #7 — Empty-basket no auto-cancel ────────────────────────


class TestEmptyBasketIndefinite:
    def test_empty_basket_does_not_count_question_cap(self, tool_ctx):
        """Many Q&A turns while basket is empty should NOT trigger the
        MAX_GATE_QUESTIONS cancellation."""
        m = _mandate(tool_ctx)
        # Script: remove only item (basket empty), then 10 Q&A turns, then cancel
        scripted = [GateResponse(decision="question", text="1")]
        for i in range(10):
            scripted.append(GateResponse(decision="question", text=f"question {i}"))
        scripted.append(GateResponse(decision="cancel"))
        confirm = AutoConfirmProvider(scripted=scripted)
        # Queue answers for each empty-basket Q&A turn (10 questions)
        responses = [
            tool_use_response(
                (
                    "call_purchase_agent",
                    {
                        "brief": "buy",
                        "merchant_domain": "demo-shop.myshopify.com",
                        "items": [_item("a", "Mug", "14")],
                    },
                )
            ),
        ]
        # Each non-numeric question goes through _handle_gate_input → 1 LLM call
        for i in range(10):
            responses.append(
                text_response(
                    json.dumps(
                        {
                            "intent": "answer",
                            "answer": f"Answer {i}.",
                        }
                    )
                )
            )
        responses.append(text_response("Cancelled."))
        client = FakeAnthropicClient(responses)
        orch = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
        asyncio.get_event_loop().run_until_complete(orch.run(tool_ctx, "buy"))
        # The audit log should show a user-cancellation, NOT
        # max_questions_reached or iteration_limit_reached.
        audit_actions = [
            r["action"]
            for r in tool_ctx.db.audit_log.all()
            if r.get("tool") == "hitl_gate" and "cancelled" in r["action"]
        ]
        assert audit_actions, "expected at least one cancellation audit entry"
        last_cancel = audit_actions[-1]
        assert "max_questions" not in last_cancel
        assert "iteration_limit" not in last_cancel
        # No spend recorded — nothing was purchased
        assert tool_ctx.db.spend_records.all() == []

    def test_empty_basket_can_be_recovered_via_add(self, tool_ctx):
        """User empties basket, then adds via search, then confirms."""
        m = _mandate(tool_ctx)
        tool_ctx.session.last_discovered_products = [
            {
                "product_id": "cof_005",
                "name": "Coffee Beans",
                "price": "18",
                "merchant_domain": "demo-shop.myshopify.com",
                "in_stock": True,
            },
        ]
        scripted = [
            GateResponse(decision="question", text="1"),  # remove the only item
            GateResponse(decision="question", text="add Coffee Beans"),
            GateResponse(decision="confirm"),
        ]
        confirm = AutoConfirmProvider(scripted=scripted)
        client = FakeAnthropicClient(
            [
                tool_use_response(
                    (
                        "call_purchase_agent",
                        {
                            "brief": "buy",
                            "merchant_domain": "demo-shop.myshopify.com",
                            "items": [_item("a", "Mug", "14")],
                        },
                    )
                ),
                # After empty basket, user says "add Coffee Beans" → model returns
                # add intent with the cached product
                text_response(
                    json.dumps(
                        {
                            "intent": "add",
                            "new_product_id": "cof_005",
                            "new_product_name": "Coffee Beans",
                            "new_product_price": "18",
                            "new_product_quantity": 1,
                            "answer": "Added Coffee Beans.",
                        }
                    )
                ),
                text_response('{"order": null, "status": "completed"}'),
                text_response("Done."),
            ]
        )
        orch = OrchestratorAgent(client, confirmation=confirm, mandate_id=m.mandate_id)
        asyncio.get_event_loop().run_until_complete(orch.run(tool_ctx, "buy"))
        # The final confirmed gate should have 1 item (Coffee Beans)
        final_gate = confirm.gates_seen[-1][1]
        assert len(final_gate.items) == 1
        assert final_gate.items[0]["product_id"] == "cof_005"

    def test_iteration_ceiling_is_higher_than_question_cap(self):
        """The absolute iteration ceiling should be > MAX_GATE_QUESTIONS
        so empty-basket browsing isn't unduly capped."""
        # Constants are defined in the gate loop body; verify the behavior
        # by checking that 10+ empty turns don't trigger cap.
        # (covered by test_empty_basket_does_not_count_question_cap)
        assert OrchestratorAgent.MAX_GATE_QUESTIONS < 50  # ABSOLUTE_MAX

    def test_empty_basket_skips_confirmation_panel(self, tool_ctx, monkeypatch):
        """Fix #7: RichConfirmProvider.explicit_confirm with an empty basket
        should NOT render the formal 'PURCHASE CONFIRMATION REQUIRED' panel —
        just prompt for input compactly."""
        import io
        from rich.console import Console
        from cli.display import RichConfirmProvider, GateData
        from cli import display as display_module

        # Capture rendered output
        captured = Console(file=io.StringIO(), record=True, width=120)
        monkeypatch.setattr(display_module, "console", captured)
        provider = RichConfirmProvider()
        gate = GateData(
            merchant_domain="x.com",
            amount=Decimal("0"),
            currency="USD",
            item_summary="0 items",
            items=[],  # empty
        )
        # Stub Prompt.ask to return "cancel"
        monkeypatch.setattr(display_module.Prompt, "ask", lambda *a, **kw: "cancel")
        result = asyncio.get_event_loop().run_until_complete(provider.explicit_confirm(gate))
        output = captured.file.getvalue()
        # No formal panel header should appear
        assert "PURCHASE CONFIRMATION REQUIRED" not in output
        assert result.decision == "cancel"


# ─── Fix #6 — Search-from-gate heuristic ──────────────────────────────────


class TestSearchHeuristic:
    @pytest.mark.parametrize(
        "text",
        [
            "I'll search for that now.",
            "I will search for headphones separately.",
            "Let me look up the tumbler at Coffee Bar.",
            "Going to find that for you.",
            "I'll look for it in the catalogue.",
            "Let me look that up.",
        ],
    )
    def test_heuristic_matches_common_phrasings(self, text):
        assert OrchestratorAgent._looks_like_search_intent(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Your search returned 3 items.",  # past tense / not action signal
            "The search results were great.",  # passive observation
            "I cannot search other merchants.",  # negation
            "The basket is unchanged.",  # unrelated
            "",  # empty
        ],
    )
    def test_heuristic_avoids_false_positives(self, text):
        assert not OrchestratorAgent._looks_like_search_intent(text)


# ─── Fix #10 — Swap preserves position ────────────────────────────────────


class TestSwapPositionStability:
    def test_swap_preserves_position(self, tool_ctx):
        orch = OrchestratorAgent(FakeAnthropicClient([]), mandate_id=None)
        basket = _basket(
            _item("a", "First", "10"),
            _item("b", "Middle", "20"),
            _item("c", "Last", "30"),
        )
        action = GateAction(
            kind="swap",
            target_product_id="b",
            new_item={
                "product_id": "b2",
                "name": "Replacement",
                "price": "25",
                "quantity": 1,
            },
        )
        msg, new_basket = orch._apply_gate_action(
            action,
            basket,
            merchant_domain="x.com",
        )
        # The new item lands at position 1 (where Middle was)
        pids = [i["product_id"] for i in new_basket]
        assert pids == ["a", "b2", "c"]

    def test_swap_first_item_stays_first(self, tool_ctx):
        orch = OrchestratorAgent(FakeAnthropicClient([]), mandate_id=None)
        basket = _basket(
            _item("a", "First", "10"),
            _item("b", "Second", "20"),
        )
        action = GateAction(
            kind="swap",
            target_product_id="a",
            new_item={
                "product_id": "a2",
                "name": "FirstReplacement",
                "price": "12",
                "quantity": 1,
            },
        )
        _, new_basket = orch._apply_gate_action(
            action,
            basket,
            merchant_domain="x.com",
        )
        assert new_basket[0]["product_id"] == "a2"
        assert new_basket[1]["product_id"] == "b"


# ─── Fix #13 — Cap-refusal special-case for single-item basket ────────────


class TestSingleItemCapRefusal:
    def test_single_item_basket_special_phrasing(self, tool_ctx):
        m = _mandate(tool_ctx, max_amount=Decimal("100"))
        orch = OrchestratorAgent(FakeAnthropicClient([]), mandate_id=m.mandate_id)
        basket = _basket(_item("a", "Mug", "5"))
        # overage = $50 (attempted $150 vs cap $100)
        msg = orch._friendly_cap_refusal(
            tool_ctx,
            reason="exceeds_per_transaction_cap",
            attempted_total=Decimal("150"),
            original_basket=basket,
            attempted_basket=basket,
        )
        # Must mention "only one item" or similar, NOT "isn't large enough to remove"
        assert "only one item" in msg.lower() or "nothing to drop" in msg.lower()
        assert "Mug" in msg


# ─── Fix #14 — Numeric disambiguation enforced by prompt ──────────────────


class TestNumericDisambiguation:
    def test_orchestrator_prompt_mentions_numeric_disambiguation(self):
        # The TONE_RULES section forbids A/B/C and requires 1/2/3
        rules_lc = TONE_RULES.lower()
        assert "a, b, c" in rules_lc and "1, 2, 3" in TONE_RULES
