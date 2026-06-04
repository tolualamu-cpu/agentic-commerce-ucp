"""Tests for the ``show_product_cards`` re-render tool.

When the user asks to see an already-discovered product card again ("show me
that card", "pull up the running shoes"), the agent must NOT dump raw product
JSON (product_id, merchant_domain, image URLs, descriptions) into its prose
reply. Instead it calls the ``show_product_cards`` tool, which:

  * selects the matching products from ``ctx.session.last_discovered_products``
    (no re-running of discovery),
  * stashes them on ``ctx.session.cards_to_show`` for the web layer to drain,
  * and returns ONLY a minimal status count — never the product data — so the
    model has nothing to echo as prose.

The web layer (``_run_orchestrator`` in web/routers/chat.py) drains
``cards_to_show`` after each run: it emits a ``products`` SSE event, appends to
``product_card_sets`` (reload persistence), then clears the list.

Coverage spans all three merchants (Athletic Co, Audio Hub, Coffee Bar),
specific-id selection, no-id (show-all), non-existent ids, mixed valid/invalid,
and an end-to-end drain through the real web handler.

This file sorts BEFORE test_user_journeys.py (s < u), so per CLAUDE.md it must
use ``asyncio.get_event_loop().run_until_complete()`` — never ``asyncio.run()``.
"""

from __future__ import annotations

import asyncio

from agents.orchestrator import OrchestratorAgent
from cli.confirmation import AutoConfirmProvider
from tests.fake_anthropic import (
    FakeAnthropicClient,
    text_response,
    tool_use_response,
)


# ─── Product fixtures (one per merchant) ─────────────────────────────────────


def _shoe():
    return {
        "product_id": "ath_001",
        "name": "Demo Running Shoes",
        "description": "Lightweight. Cushioned midsole.",
        "price": "129.99",
        "currency": "USD",
        "merchant": "Athletic Co",
        "merchant_domain": "athletic-co.myshopify.com",
        "in_stock": True,
        "images": ["https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=800&q=80"],
        "source_protocol": "stub",
        "confidence_score": 1.0,
    }


def _headphones():
    return {
        "product_id": "aud_002",
        "name": "Noise-Cancelling Headphones",
        "description": "30h battery. Bluetooth 5.3.",
        "price": "249.00",
        "currency": "USD",
        "merchant": "Audio Hub",
        "merchant_domain": "audio-hub.myshopify.com",
        "in_stock": True,
        "images": ["https://images.unsplash.com/photo-1484704849700-f032a568e944?w=800&q=80"],
        "source_protocol": "stub",
        "confidence_score": 1.0,
    }


def _mug():
    return {
        "product_id": "cof_001",
        "name": "Ceramic Coffee Mug",
        "description": "12oz ceramic mug.",
        "price": "14.00",
        "currency": "USD",
        "merchant": "Coffee Bar",
        "merchant_domain": "coffee-bar.myshopify.com",
        "in_stock": True,
        "images": ["https://images.unsplash.com/photo-1509042239860-f550ce710b93?w=800&q=80"],
        "source_protocol": "stub",
        "confidence_score": 1.0,
    }


def _all_three():
    return [_shoe(), _headphones(), _mug()]


def _orch():
    """Bare orchestrator — handler unit tests call _show_product_cards directly."""
    return OrchestratorAgent(
        client=FakeAnthropicClient([]),
        confirmation=AutoConfirmProvider(),
        mandate_id="m_test",
    )


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ─── 1. Handler selects specific ids (per merchant) ──────────────────────────


class TestShowSpecificIds:
    def test_shoe_by_id(self, multi_merchant_ctx):
        multi_merchant_ctx.session.last_discovered_products = _all_three()
        orch = _orch()
        result = _run(orch._show_product_cards(multi_merchant_ctx, product_ids=["ath_001"]))
        assert result == {"status": "cards_rendered", "shown": 1}
        staged = multi_merchant_ctx.session.cards_to_show
        assert [p["product_id"] for p in staged] == ["ath_001"]

    def test_headphones_by_id(self, multi_merchant_ctx):
        multi_merchant_ctx.session.last_discovered_products = _all_three()
        orch = _orch()
        result = _run(orch._show_product_cards(multi_merchant_ctx, product_ids=["aud_002"]))
        assert result["shown"] == 1
        assert multi_merchant_ctx.session.cards_to_show[0]["product_id"] == "aud_002"

    def test_mug_by_id(self, multi_merchant_ctx):
        multi_merchant_ctx.session.last_discovered_products = _all_three()
        orch = _orch()
        result = _run(orch._show_product_cards(multi_merchant_ctx, product_ids=["cof_001"]))
        assert result["shown"] == 1
        assert multi_merchant_ctx.session.cards_to_show[0]["product_id"] == "cof_001"

    def test_multiple_ids_cross_merchant(self, multi_merchant_ctx):
        multi_merchant_ctx.session.last_discovered_products = _all_three()
        orch = _orch()
        result = _run(
            orch._show_product_cards(multi_merchant_ctx, product_ids=["ath_001", "cof_001"])
        )
        assert result["shown"] == 2
        ids = {p["product_id"] for p in multi_merchant_ctx.session.cards_to_show}
        assert ids == {"ath_001", "cof_001"}


# ─── 2. No ids → show all ────────────────────────────────────────────────────


class TestShowAll:
    def test_no_ids_shows_all_recent(self, multi_merchant_ctx):
        multi_merchant_ctx.session.last_discovered_products = _all_three()
        orch = _orch()
        result = _run(orch._show_product_cards(multi_merchant_ctx))
        assert result["shown"] == 3
        ids = {p["product_id"] for p in multi_merchant_ctx.session.cards_to_show}
        assert ids == {"ath_001", "aud_002", "cof_001"}

    def test_empty_ids_list_shows_all(self, multi_merchant_ctx):
        multi_merchant_ctx.session.last_discovered_products = _all_three()
        orch = _orch()
        # An explicit empty list is falsy → treated as "show all".
        result = _run(orch._show_product_cards(multi_merchant_ctx, product_ids=[]))
        assert result["shown"] == 3


# ─── 3. Non-existent / mixed ids ─────────────────────────────────────────────


class TestMissingIds:
    def test_nonexistent_id_shows_nothing(self, multi_merchant_ctx):
        multi_merchant_ctx.session.last_discovered_products = _all_three()
        orch = _orch()
        result = _run(orch._show_product_cards(multi_merchant_ctx, product_ids=["nope_999"]))
        assert result == {"status": "cards_rendered", "shown": 0}
        assert multi_merchant_ctx.session.cards_to_show == []

    def test_mixed_valid_invalid_keeps_only_valid(self, multi_merchant_ctx):
        multi_merchant_ctx.session.last_discovered_products = _all_three()
        orch = _orch()
        result = _run(
            orch._show_product_cards(multi_merchant_ctx, product_ids=["aud_002", "nope_999"])
        )
        assert result["shown"] == 1
        assert multi_merchant_ctx.session.cards_to_show[0]["product_id"] == "aud_002"

    def test_empty_cache_shows_nothing(self, multi_merchant_ctx):
        multi_merchant_ctx.session.last_discovered_products = []
        orch = _orch()
        result = _run(orch._show_product_cards(multi_merchant_ctx, product_ids=["ath_001"]))
        assert result["shown"] == 0
        assert multi_merchant_ctx.session.cards_to_show == []


# ─── 4. Status NEVER leaks product data ──────────────────────────────────────


class TestNoDataLeak:
    def test_status_has_no_product_fields(self, multi_merchant_ctx):
        """The model must receive only a count — never the product payload,
        so it has nothing to paste into prose."""
        multi_merchant_ctx.session.last_discovered_products = _all_three()
        orch = _orch()
        result = _run(orch._show_product_cards(multi_merchant_ctx))
        assert set(result.keys()) == {"status", "shown"}
        leaky = (
            "products",
            "product_id",
            "name",
            "price",
            "description",
            "merchant_domain",
            "images",
            "url",
        )
        for key in leaky:
            assert key not in result, f"status leaked product field: {key}"

    def test_discovery_cache_not_mutated(self, multi_merchant_ctx):
        """Re-showing must NOT touch last_discovered_products."""
        before = _all_three()
        multi_merchant_ctx.session.last_discovered_products = before
        orch = _orch()
        _run(orch._show_product_cards(multi_merchant_ctx, product_ids=["ath_001"]))
        assert multi_merchant_ctx.session.last_discovered_products == before


# ─── 5. Tool is wired into the orchestrator spec ─────────────────────────────


class TestToolRegistered:
    def test_show_product_cards_tool_exists(self):
        orch = _orch()
        names = {spec.name for spec in orch.tool_specs}
        assert "show_product_cards" in names

    def test_tool_runs_via_agent_loop(self, multi_merchant_ctx):
        """A full run where the model calls show_product_cards stages the cards
        and the reply carries no raw product JSON."""
        multi_merchant_ctx.session.last_discovered_products = _all_three()
        orch = OrchestratorAgent(
            client=FakeAnthropicClient(
                [
                    tool_use_response(("show_product_cards", {"product_ids": ["ath_001"]})),
                    text_response("Here it is."),
                ]
            ),
            confirmation=AutoConfirmProvider(),
            mandate_id="m_test",
        )
        result = _run(orch.run(multi_merchant_ctx, "show me the running shoes card again"))
        assert multi_merchant_ctx.session.cards_to_show
        assert multi_merchant_ctx.session.cards_to_show[0]["product_id"] == "ath_001"
        reply = (result.get("reply") or "") if isinstance(result, dict) else ""
        # The model's prose must not contain backend identifiers.
        assert "ath_001" not in reply
        assert "athletic-co.myshopify.com" not in reply
        assert "images.unsplash.com" not in reply


# ─── 6. End-to-end: web layer drains cards_to_show → products SSE event ──────


class TestWebDrain:
    def _drain_queue(self, sess):
        events = []
        while True:
            try:
                events.append(sess.sse_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return events

    def _run_drain_for(self, multi_merchant_ctx, product_ids, expect_ids):
        """Build a minimal WebSession around multi_merchant_ctx, run
        _run_orchestrator with a fake orchestrator that calls
        show_product_cards, and assert a products SSE event was emitted."""
        from web import session as session_mod
        from web.routers.chat import _run_orchestrator

        multi_merchant_ctx.session.last_discovered_products = _all_three()
        fake_orch = OrchestratorAgent(
            client=FakeAnthropicClient(
                [
                    tool_use_response(("show_product_cards", {"product_ids": product_ids})),
                    text_response("Here it is."),
                ]
            ),
            confirmation=AutoConfirmProvider(),
            mandate_id="m_test",
        )
        sess = session_mod.WebSession(
            session_id="sess_test",
            db=multi_merchant_ctx.db,
            ctx=multi_merchant_ctx,
            orchestrator=fake_orch,
            mandate_id="m_test",
            gate_provider=None,
        )

        async def _go():
            await _run_orchestrator(sess, "show me that card again")
            return self._drain_queue(sess)

        events = _run(_go())
        products_events = [e for e in events if e["type"] == "products"]
        assert products_events, "a products SSE event must be emitted on re-show"
        emitted_ids = {p["product_id"] for p in products_events[0]["data"]["products"]}
        assert emitted_ids == set(expect_ids)
        # cards_to_show must be cleared after draining.
        assert sess.ctx.session.cards_to_show == []
        # Persisted for reload.
        assert sess.ctx.session.product_card_sets, "re-shown cards must persist"
        return events

    def test_drain_emits_shoe_card(self, multi_merchant_ctx):
        self._run_drain_for(multi_merchant_ctx, ["ath_001"], ["ath_001"])

    def test_drain_emits_headphones_card(self, multi_merchant_ctx):
        self._run_drain_for(multi_merchant_ctx, ["aud_002"], ["aud_002"])

    def test_drain_emits_mug_card(self, multi_merchant_ctx):
        self._run_drain_for(multi_merchant_ctx, ["cof_001"], ["cof_001"])

    def test_drain_emits_cross_merchant(self, multi_merchant_ctx):
        self._run_drain_for(multi_merchant_ctx, ["ath_001", "cof_001"], ["ath_001", "cof_001"])

    def test_discovery_plus_show_in_same_turn_emits_once(self, multi_merchant_ctx):
        """Regression: if the model calls call_discovery_agent AND
        show_product_cards in the SAME turn, the discovery set-change block
        already renders the cards — the drain must NOT emit them a second
        time (the duplicate-card bug)."""
        import json

        from web import session as session_mod
        from web.routers.chat import _run_orchestrator

        mug = _mug()
        merchant = mug["merchant_domain"]
        discovery_json = json.dumps({"products": [mug], "notes": "Found it"})

        # FakeAnthropicClient responses are consumed sequentially across the
        # orchestrator AND the discovery subagent (they share the client):
        #   1) orch  → call_discovery_agent
        #   2) discovery subagent → search_products
        #   3) discovery subagent → final JSON payload
        #   4) orch  → show_product_cards on the just-discovered mug
        #   5) orch  → final reply
        fake_orch = OrchestratorAgent(
            client=FakeAnthropicClient(
                [
                    tool_use_response(
                        ("call_discovery_agent", {"brief": "mug", "merchant_domains": [merchant]})
                    ),
                    tool_use_response(
                        ("search_products", {"query": "mug", "merchant_domain": merchant})
                    ),
                    text_response(discovery_json),
                    tool_use_response(("show_product_cards", {"product_ids": ["cof_001"]})),
                    text_response("Here it is."),
                ]
            ),
            confirmation=AutoConfirmProvider(),
            mandate_id="m_test",
        )
        sess = session_mod.WebSession(
            session_id="sess_collide",
            db=multi_merchant_ctx.db,
            ctx=multi_merchant_ctx,
            orchestrator=fake_orch,
            mandate_id="m_test",
            gate_provider=None,
        )

        async def _go():
            await _run_orchestrator(sess, "buy a coffee mug for my partner")
            return self._drain_queue(sess)

        events = _run(_go())
        products_events = [e for e in events if e["type"] == "products"]
        assert len(products_events) == 1, (
            f"expected exactly one products event, got {len(products_events)} "
            "— discovery + show_product_cards in one turn must not duplicate cards"
        )
        # cards_to_show must be cleared even though the drain was skipped.
        assert sess.ctx.session.cards_to_show == []

    def test_no_double_emit_when_nothing_staged(self, multi_merchant_ctx):
        """A normal (non-re-show) run with no cards_to_show emits no products."""
        from web import session as session_mod
        from web.routers.chat import _run_orchestrator

        multi_merchant_ctx.session.last_discovered_products = _all_three()
        fake_orch = OrchestratorAgent(
            client=FakeAnthropicClient([text_response("Your budget is fine.")]),
            confirmation=AutoConfirmProvider(),
            mandate_id="m_test",
        )
        sess = session_mod.WebSession(
            session_id="sess_test2",
            db=multi_merchant_ctx.db,
            ctx=multi_merchant_ctx,
            orchestrator=fake_orch,
            mandate_id="m_test",
            gate_provider=None,
        )

        async def _go():
            await _run_orchestrator(sess, "what's my budget?")
            return self._drain_queue(sess)

        events = _run(_go())
        assert not [e for e in events if e["type"] == "products"]


# ─── 7. SSE event order: products BEFORE the summary text (cards-first) ──────


class TestProductsBeforeTextOrdering:
    """The live SSE stream MUST enqueue the ``products`` event BEFORE the
    ``text`` summary for a discovery (find) flow. The chat-page client
    (``_chat_sse.html``) relies on this contract: it reserves the product-card
    placeholder on ``products`` and defers the summary bubble behind the card
    fetch so cards render ABOVE the text. If the server ever emitted ``text``
    first, the client would paint the summary above the cards — the exact
    "product text appears before cards" bug the user reported.

    Covered for all three merchants and a cross-merchant find, since a product
    flow must never be validated against a single category (CLAUDE.md rule 3).
    """

    def _drain(self, sess):
        events = []
        while True:
            try:
                events.append(sess.sse_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return events

    def _run_find(self, multi_merchant_ctx, product, query):
        """Drive a discovery find-flow through the real web handler and return
        the drained SSE events. The fake orchestrator runs discovery (so the
        product set changes → ``_run_orchestrator`` emits ``products``) then a
        final reply (emitted as ``text``)."""
        import json

        from web import session as session_mod
        from web.routers.chat import _run_orchestrator

        merchant = product["merchant_domain"]
        discovery_json = json.dumps({"products": [product], "notes": "Found it"})
        fake_orch = OrchestratorAgent(
            client=FakeAnthropicClient(
                [
                    tool_use_response(
                        ("call_discovery_agent", {"brief": query, "merchant_domains": [merchant]})
                    ),
                    tool_use_response(
                        ("search_products", {"query": query, "merchant_domain": merchant})
                    ),
                    text_response(discovery_json),
                    text_response("Here is what I found for you."),
                ]
            ),
            confirmation=AutoConfirmProvider(),
            mandate_id="m_test",
        )
        sess = session_mod.WebSession(
            session_id="sess_order",
            db=multi_merchant_ctx.db,
            ctx=multi_merchant_ctx,
            orchestrator=fake_orch,
            mandate_id="m_test",
            gate_provider=None,
        )

        async def _go():
            await _run_orchestrator(sess, query)
            return self._drain(sess)

        return _run(_go())

    def _assert_products_before_text(self, events):
        types = [e["type"] for e in events]
        assert "products" in types, "a products event must be emitted on a find-flow"
        assert "text" in types, "a text summary must be emitted on a find-flow"
        assert types.index("products") < types.index("text"), (
            "products must be enqueued BEFORE the text summary so the client "
            "renders cards above the summary (cards-first ordering contract)"
        )

    def test_shoe_find_products_before_text(self, multi_merchant_ctx):
        events = self._run_find(multi_merchant_ctx, _shoe(), "find running shoes")
        self._assert_products_before_text(events)

    def test_headphones_find_products_before_text(self, multi_merchant_ctx):
        events = self._run_find(multi_merchant_ctx, _headphones(), "find headphones")
        self._assert_products_before_text(events)

    def test_mug_find_products_before_text(self, multi_merchant_ctx):
        events = self._run_find(multi_merchant_ctx, _mug(), "find a coffee mug")
        self._assert_products_before_text(events)

    def test_cross_merchant_find_products_before_text(self, multi_merchant_ctx):
        """Cross-merchant discovery: a single discovery turn returns products
        from more than one store, then the summary. Cards still come first."""
        import json

        from web import session as session_mod
        from web.routers.chat import _run_orchestrator

        shoe, mug = _shoe(), _mug()
        # Discovery returns a cross-merchant set in one go.
        discovery_json = json.dumps({"products": [shoe, mug], "notes": "Found across stores"})
        fake_orch = OrchestratorAgent(
            client=FakeAnthropicClient(
                [
                    tool_use_response(
                        (
                            "call_discovery_agent",
                            {
                                "brief": "gear",
                                "merchant_domains": [
                                    shoe["merchant_domain"],
                                    mug["merchant_domain"],
                                ],
                            },
                        )
                    ),
                    tool_use_response(
                        (
                            "search_products",
                            {"query": "gear", "merchant_domain": shoe["merchant_domain"]},
                        )
                    ),
                    text_response(discovery_json),
                    text_response("Two picks across stores."),
                ]
            ),
            confirmation=AutoConfirmProvider(),
            mandate_id="m_test",
        )
        sess = session_mod.WebSession(
            session_id="sess_order_x",
            db=multi_merchant_ctx.db,
            ctx=multi_merchant_ctx,
            orchestrator=fake_orch,
            mandate_id="m_test",
            gate_provider=None,
        )

        async def _go():
            await _run_orchestrator(sess, "find gear across stores")
            return self._drain(sess)

        events = _run(_go())
        self._assert_products_before_text(events)
        # The products event must carry both merchants' items.
        prod_evt = next(e for e in events if e["type"] == "products")
        domains = {p["merchant_domain"] for p in prod_evt["data"]["products"]}
        assert domains == {shoe["merchant_domain"], mug["merchant_domain"]}
