"""Tests for the post-run products emission architecture.

After the race-condition fix, products are emitted AFTER the orchestrator
run completes (from _run_orchestrator in web/routers/chat.py), NOT from
_call_discovery mid-run. This file verifies:

1. StreamingCallbacks does NOT have an on_products field (removed to
   prevent mid-run SSE events that cause conversation corruption).
2. OrchestratorAgent does NOT have a _emit_products method.
3. last_discovered_products is populated after a discovery run so
   _run_orchestrator can use it to decide whether to emit.
4. _product_id_set helper (from chat.py) works correctly.
5. _strip_orphaned_tool_use heals corrupted conversations.
"""

from __future__ import annotations

import asyncio


from agents.orchestrator import OrchestratorAgent, StreamingCallbacks
from cli.confirmation import AutoConfirmProvider
from tests.fake_anthropic import (
    FakeAnthropicClient,
    text_response,
    tool_use_response,
)
from web.callbacks import build_web_callbacks


# ─── Helpers ────────────────────────────────────────────────────────────────

import json

_DISCOVERY_PRODUCTS = [
    {
        "product_id": "ath_001",
        "name": "Demo Running Shoes",
        "price": "129.99",
        "currency": "USD",
        "merchant": "Athletic Co",
        "merchant_domain": "athletic-co.myshopify.com",
        "in_stock": True,
        "images": ["https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=800&q=80"],
        "attributes": {},
        "source_protocol": "stub",
        "confidence_score": 1.0,
    }
]

_DISCOVERY_RESULT = json.dumps(
    {
        "products": _DISCOVERY_PRODUCTS,
        "notes": "Found running shoes",
    }
)


def _make_orch(responses):
    return OrchestratorAgent(
        client=FakeAnthropicClient(responses),
        confirmation=AutoConfirmProvider(),
        mandate_id="m_test",
    )


# ─── 1. StreamingCallbacks no longer has on_products ────────────────────────


class TestNoOnProductsCallback:
    def test_streaming_callbacks_has_no_on_products(self):
        """on_products was removed to prevent mid-run conversation corruption."""
        cb = StreamingCallbacks()
        assert not hasattr(cb, "on_products"), (
            "StreamingCallbacks must NOT have on_products — it was removed "
            "to prevent the race condition where a cart click could inject a "
            "user message between a tool_use and its tool_result."
        )

    def test_build_web_callbacks_has_no_on_products(self):
        """build_web_callbacks must not wire a mid-run products callback."""
        queue = asyncio.Queue()
        cb = build_web_callbacks(queue)
        assert not hasattr(
            cb, "on_products"
        ), "build_web_callbacks must not produce an on_products callback"

    def test_orchestrator_has_no_emit_products_method(self):
        """_emit_products helper was removed along with the callback."""
        orch = _make_orch([])
        assert not hasattr(orch, "_emit_products"), (
            "OrchestratorAgent must not have _emit_products — that was the "
            "method that fired the mid-run SSE event"
        )


# ─── 2. last_discovered_products is populated after discovery ────────────────


class TestLastDiscoveredProductsPopulated:
    def test_discovery_populates_last_discovered(self, multi_merchant_ctx):
        """After a run that calls discovery, last_discovered_products is set."""
        orch = _make_orch(
            [
                tool_use_response(
                    (
                        "call_discovery_agent",
                        {
                            "brief": "shoes",
                            "merchant_domains": ["athletic-co.myshopify.com"],
                        },
                    )
                ),
                tool_use_response(
                    (
                        "search_products",
                        {
                            "query": "shoes",
                            "merchant_domain": "athletic-co.myshopify.com",
                        },
                    )
                ),
                text_response(_DISCOVERY_RESULT),
                text_response("Here are the shoes."),
            ]
        )

        asyncio.get_event_loop().run_until_complete(orch.run(multi_merchant_ctx, "find me shoes"))

        assert (
            len(multi_merchant_ctx.session.last_discovered_products) > 0
        ), "last_discovered_products must be populated after a discovery run"

    def test_get_last_discovered_returns_cache(self, multi_merchant_ctx):
        """get_last_discovered_products tool returns the cached list."""
        multi_merchant_ctx.session.last_discovered_products = _DISCOVERY_PRODUCTS

        orch = _make_orch(
            [
                tool_use_response(("get_last_discovered_products", {})),
                text_response("Here are the shoes again."),
            ]
        )

        asyncio.get_event_loop().run_until_complete(orch.run(multi_merchant_ctx, "show them again"))

        # Cache unchanged after a cache-hit run
        assert multi_merchant_ctx.session.last_discovered_products == _DISCOVERY_PRODUCTS


# ─── 3. _product_id_set helper ──────────────────────────────────────────────


class TestProductIdSet:
    def test_extracts_ids_from_dicts(self):
        from web.routers.chat import _product_id_set

        products = [
            {"product_id": "ath_001"},
            {"product_id": "ath_002"},
        ]
        assert _product_id_set(products) == {"ath_001", "ath_002"}

    def test_extracts_ids_from_pydantic(self):
        from decimal import Decimal
        from models.product import ProductResult
        from web.routers.chat import _product_id_set

        p = ProductResult(
            product_id="aud_001",
            name="Headphones",
            price=Decimal("89.00"),
            merchant="Audio Hub",
            merchant_domain="audio-hub.myshopify.com",
            source_protocol="stub",
        )
        assert _product_id_set([p]) == {"aud_001"}

    def test_empty_list_returns_empty_set(self):
        from web.routers.chat import _product_id_set

        assert _product_id_set([]) == set()

    def test_missing_product_id_key_skips(self):
        from web.routers.chat import _product_id_set

        products = [{"name": "No ID here"}]
        result = _product_id_set(products)
        assert "" in result or result == {""}


# ─── 4. _strip_orphaned_tool_use ────────────────────────────────────────────


class TestStripOrphanedToolUse:
    def _tool_use_turn(self, tool_id="toolu_abc"):
        return {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_id, "name": "search", "input": {}}],
        }

    def _tool_result_turn(self, tool_id="toolu_abc"):
        return {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "[]"}],
        }

    def _text_turn(self, role, text):
        return {"role": role, "content": [{"type": "text", "text": text}]}

    def test_valid_pairs_untouched(self):
        from web.routers.chat import _strip_orphaned_tool_use

        convo = [
            self._text_turn("user", "find shoes"),
            self._tool_use_turn("t1"),
            self._tool_result_turn("t1"),
            self._text_turn("assistant", "Here they are"),
        ]
        result = _strip_orphaned_tool_use(convo)
        assert len(result) == 4

    def test_orphaned_tool_use_removed(self):
        from web.routers.chat import _strip_orphaned_tool_use

        convo = [
            self._text_turn("user", "find shoes"),
            self._tool_use_turn("t1"),
            # ← no tool_result follows — orphaned!
            self._text_turn("user", "[via UI click] added shoes"),
        ]
        result = _strip_orphaned_tool_use(convo)
        # The orphaned tool_use turn should be removed
        roles = [t["role"] for t in result]
        # We should NOT have a pure tool_use assistant turn followed by a
        # non-tool-result user turn
        for i, turn in enumerate(result):
            if turn["role"] == "assistant":
                content = turn.get("content", [])
                tool_uses = [
                    b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"
                ]
                if tool_uses:
                    next_turn = result[i + 1] if i + 1 < len(result) else None
                    assert next_turn is not None
                    next_content = next_turn.get("content", [])
                    result_ids = {
                        b.get("tool_use_id")
                        for b in next_content
                        if isinstance(b, dict) and b.get("type") == "tool_result"
                    }
                    assert {b["id"] for b in tool_uses}.issubset(result_ids)

    def test_tool_use_at_end_removed(self):
        from web.routers.chat import _strip_orphaned_tool_use

        convo = [
            self._text_turn("user", "find shoes"),
            self._tool_use_turn("t1"),
            # ← no following turn at all — orphaned
        ]
        result = _strip_orphaned_tool_use(convo)
        assert not any(
            t["role"] == "assistant"
            and any(
                b.get("type") == "tool_use" for b in t.get("content", []) if isinstance(b, dict)
            )
            for t in result
        ), "Orphaned tool_use at end of conversation should be stripped"

    def test_empty_conversation_unchanged(self):
        from web.routers.chat import _strip_orphaned_tool_use

        assert _strip_orphaned_tool_use([]) == []

    def test_text_only_conversation_unchanged(self):
        from web.routers.chat import _strip_orphaned_tool_use

        convo = [
            self._text_turn("user", "hello"),
            self._text_turn("assistant", "hi"),
        ]
        result = _strip_orphaned_tool_use(convo)
        assert len(result) == 2
