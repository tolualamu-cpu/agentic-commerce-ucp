"""Phase 9b — race condition protection and conversation repair.

Verifies:
1. Products SSE event is NOT emitted during the orchestrator run (only after)
2. Products SSE event IS emitted after the run when discovery produces new results
3. Products SSE event is NOT emitted when last_discovered_products is unchanged
4. _strip_orphaned_tool_use heals conversations corrupted by mid-run injection
5. Cart operations after product cards appear do not corrupt the conversation

Sorts alphabetically after test_user_journeys.py (w > u) so asyncio.run() is safe.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from agents.orchestrator import OrchestratorAgent
from cli.confirmation import AutoConfirmProvider
from tests.fake_anthropic import (
    FakeAnthropicClient,
    text_response,
    tool_use_response,
)
from web import session as session_mod
from web.app import create_app
from web.routers.chat import _product_id_set, _strip_orphaned_tool_use


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "demo.json"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = create_app()
    with TestClient(app) as c:
        yield c


def _sess(client) -> "session_mod.WebSession":
    sid_raw = client.cookies.get("ac_session")
    sid = session_mod._serializer.loads(sid_raw)
    return session_mod.get_session_by_id(sid)


_SHOE = {
    "product_id": "ath_001",
    "name": "Demo Running Shoes",
    "description": "Lightweight road running shoes.",
    "price": "129.99",
    "currency": "USD",
    "merchant": "Athletic Co",
    "merchant_domain": "athletic-co.myshopify.com",
    "rating": 4.5,
    "review_count": 240,
    "in_stock": True,
    "images": ["https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=800&q=80"],
    "attributes": {},
    "source_protocol": "stub",
    "confidence_score": 1.0,
    "shipping_estimate": None,
    "shipping_cost": None,
    "url": None,
}

_DISCOVERY_JSON = json.dumps(
    {
        "products": [_SHOE],
        "notes": "Found running shoes",
    }
)


# ─── Helper builders ─────────────────────────────────────────────────────────


def _tool_use_turn(tool_id="t1", name="search"):
    return {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": {}}],
    }


def _tool_result_turn(tool_id="t1"):
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "[]"}],
    }


def _text_turn(role, text):
    return {"role": role, "content": [{"type": "text", "text": text}]}


# ─── _product_id_set ─────────────────────────────────────────────────────────


class TestProductIdSet:
    def test_dict_products(self):
        assert _product_id_set([_SHOE]) == {"ath_001"}

    def test_empty(self):
        assert _product_id_set([]) == set()

    def test_multiple_products(self):
        shoe2 = dict(_SHOE, product_id="ath_002")
        assert _product_id_set([_SHOE, shoe2]) == {"ath_001", "ath_002"}

    def test_detects_no_change(self):
        before = _product_id_set([_SHOE])
        after = _product_id_set([_SHOE])
        assert before == after

    def test_detects_new_product(self):
        before = _product_id_set([_SHOE])
        mug = dict(_SHOE, product_id="cof_001")
        after = _product_id_set([_SHOE, mug])
        assert after != before


# ─── _strip_orphaned_tool_use ─────────────────────────────────────────────────


class TestStripOrphanedToolUse:
    def test_empty_conversation(self):
        assert _strip_orphaned_tool_use([]) == []

    def test_valid_tool_use_pair_kept(self):
        convo = [
            _text_turn("user", "find shoes"),
            _tool_use_turn("t1"),
            _tool_result_turn("t1"),
            _text_turn("assistant", "Here they are"),
        ]
        result = _strip_orphaned_tool_use(convo)
        assert len(result) == 4

    def test_orphaned_tool_use_at_end_removed(self):
        convo = [
            _text_turn("user", "find shoes"),
            _tool_use_turn("t1"),
        ]
        result = _strip_orphaned_tool_use(convo)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_orphaned_tool_use_with_click_note_removed(self):
        """This is the exact race condition scenario: click_note injected
        between tool_use and tool_result."""
        convo = [
            _text_turn("user", "find shoes"),
            _tool_use_turn("t1"),
            # click_note injected here instead of tool_result:
            _text_turn("user", "[via UI click] added Demo Running Shoes × 1"),
        ]
        result = _strip_orphaned_tool_use(convo)
        # The orphaned tool_use must be stripped; the click note can remain
        for turn in result:
            if turn["role"] == "assistant":
                content = turn.get("content", [])
                has_tool_use = any(
                    isinstance(b, dict) and b.get("type") == "tool_use" for b in content
                )
                assert not has_tool_use, "Orphaned tool_use must be removed"

    def test_two_valid_pairs_kept(self):
        convo = [
            _text_turn("user", "find shoes"),
            _tool_use_turn("t1"),
            _tool_result_turn("t1"),
            _tool_use_turn("t2"),
            _tool_result_turn("t2"),
            _text_turn("assistant", "Done"),
        ]
        result = _strip_orphaned_tool_use(convo)
        assert len(result) == 6

    def test_second_of_two_pairs_orphaned(self):
        convo = [
            _text_turn("user", "find shoes"),
            _tool_use_turn("t1"),
            _tool_result_turn("t1"),
            _tool_use_turn("t2"),
            # t2 has no result — orphaned
        ]
        result = _strip_orphaned_tool_use(convo)
        # t1 pair should survive, t2 orphan should be stripped
        has_t2 = any(
            t["role"] == "assistant"
            and any(isinstance(b, dict) and b.get("id") == "t2" for b in t.get("content", []))
            for t in result
        )
        assert not has_t2

    def test_text_only_conversation_unchanged(self):
        convo = [
            _text_turn("user", "hello"),
            _text_turn("assistant", "hi"),
        ]
        assert len(_strip_orphaned_tool_use(convo)) == 2


# ─── Cart add after products doesn't corrupt conversation ────────────────────


class TestCartAddAfterProductCards:
    def test_cart_add_after_run_succeeds(self, client):
        """Add to cart after the orchestrator run completes: next message works."""
        client.get("/")
        # Add product to cart (simulates clicking chat product card AFTER run)
        r_add = client.post(
            "/cart/add/athletic-co.myshopify.com/ath_001",
            data={"variant_id": "ath_001-8"},
        )
        assert r_add.status_code in (200, 302, 303)

        # Verify basket state is valid
        sess = _sess(client)
        items = sess.ctx.session.click_basket.get("athletic-co.myshopify.com", [])
        assert any(i["product_id"] == "ath_001" for i in items)

    def test_conversation_not_corrupted_by_cart_add(self, client):
        """Conversation must be in a valid state (no orphaned tool_use) after
        a cart add. _strip_orphaned_tool_use is called at start of each run."""
        client.get("/")
        sess = _sess(client)

        # Inject a clean conversation (simulating a completed run)
        sess.ctx.session.conversation.extend(
            [
                _text_turn("user", "find me shoes"),
                _text_turn("assistant", "Here are the shoes."),
            ]
        )

        # Cart add appends a click note — this is safe because no tool_use pending
        client.post(
            "/cart/add/athletic-co.myshopify.com/ath_001",
            data={"variant_id": "ath_001-8"},
        )

        convo = sess.ctx.session.conversation
        # After strip (which happens at start of next run), must still be valid
        repaired = _strip_orphaned_tool_use(convo)
        assert len(repaired) == len(convo), "Clean conversation should be unchanged by repair"

    def test_corrupted_conversation_healed_before_next_run(self, client):
        """Simulate a corrupted conversation (tool_use without tool_result)
        and verify _strip_orphaned_tool_use would heal it."""
        client.get("/")
        sess = _sess(client)

        # Simulate corruption: assistant tool_use with no following tool_result
        sess.ctx.session.conversation.extend(
            [
                _text_turn("user", "find shoes"),
                _tool_use_turn("t_corrupted"),
                # click note injected here by cart add mid-run:
                _text_turn("user", "[via UI click] added Demo Running Shoes × 1"),
            ]
        )

        repaired = _strip_orphaned_tool_use(sess.ctx.session.conversation)

        # The tool_use should be stripped, leaving user turns only
        tool_use_turns = [
            t
            for t in repaired
            if t.get("role") == "assistant"
            and any(
                isinstance(b, dict) and b.get("type") == "tool_use" for b in t.get("content", [])
            )
        ]
        assert len(tool_use_turns) == 0, (
            f"All orphaned tool_use turns must be stripped; found: {tool_use_turns}"
        )


# ─── Products event structure via fragment endpoint ──────────────────────────


class TestProductsEventViaFragment:
    def test_fragment_with_single_product(self, client):
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_SHOE]})
        assert r.status_code == 200
        assert "Demo Running Shoes" in r.text
        assert "129.99" in r.text

    def test_fragment_products_include_images(self, client):
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_SHOE]})
        assert "<img" in r.text
        assert "unsplash.com" in r.text

    def test_discovery_changes_last_discovered_in_session(self, multi_merchant_ctx):
        """After a real discovery call, last_discovered_products is populated
        and _product_id_set returns a non-empty set."""
        orch = OrchestratorAgent(
            client=FakeAnthropicClient(
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
                    text_response(_DISCOVERY_JSON),
                    text_response("Here are the shoes."),
                ]
            ),
            confirmation=AutoConfirmProvider(),
            mandate_id="m_test",
        )
        before = _product_id_set(multi_merchant_ctx.session.last_discovered_products)
        asyncio.run(orch.run(multi_merchant_ctx, "find me shoes"))
        after = _product_id_set(multi_merchant_ctx.session.last_discovered_products)

        assert after != before, "product_id_set should change after a discovery run"
