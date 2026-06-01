"""Phase 9c — product card persistence across page reloads.

Tests that product_card_sets is populated in SessionState after a discovery
run, and that the chat log re-renders those cards server-side on page reload.

Covers all three merchants, multiple sequential runs, cache re-use, and reset.
Sorts after test_user_journeys (w > u) — asyncio.run() is safe here.
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


# ─── Product fixtures (one per merchant) ─────────────────────────────────────


def _shoe_product():
    return {
        "product_id": "ath_001",
        "name": "Demo Running Shoes",
        "description": "Lightweight. Cushioned midsole.",
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


def _headphone_product():
    return {
        "product_id": "aud_002",
        "name": "Noise-Cancelling Headphones",
        "description": "30h battery. Bluetooth 5.3.",
        "price": "249.00",
        "currency": "USD",
        "merchant": "Audio Hub",
        "merchant_domain": "audio-hub.myshopify.com",
        "rating": 4.6,
        "review_count": 1820,
        "in_stock": True,
        "images": ["https://images.unsplash.com/photo-1484704849700-f032a568e944?w=800&q=80"],
        "attributes": {},
        "source_protocol": "stub",
        "confidence_score": 1.0,
        "shipping_estimate": None,
        "shipping_cost": None,
        "url": None,
    }


def _mug_product():
    return {
        "product_id": "cof_001",
        "name": "Ceramic Coffee Mug",
        "description": "12oz ceramic mug.",
        "price": "14.00",
        "currency": "USD",
        "merchant": "Coffee Bar",
        "merchant_domain": "coffee-bar.myshopify.com",
        "rating": 4.4,
        "review_count": 89,
        "in_stock": True,
        "images": ["https://images.unsplash.com/photo-1509042239860-f550ce710b93?w=800&q=80"],
        "attributes": {},
        "source_protocol": "stub",
        "confidence_score": 1.0,
        "shipping_estimate": None,
        "shipping_cost": None,
        "url": None,
    }


def _discovery_json(product):
    return json.dumps({"products": [product], "notes": "Found it"})


def _orch(product):
    pid = product["product_id"]
    merchant = product["merchant_domain"]
    return OrchestratorAgent(
        client=FakeAnthropicClient(
            [
                tool_use_response(
                    (
                        "call_discovery_agent",
                        {"brief": "find", "merchant_domains": [merchant]},
                    )
                ),
                tool_use_response(
                    ("search_products", {"query": "find", "merchant_domain": merchant})
                ),
                text_response(_discovery_json(product)),
                text_response("Here are the results."),
            ]
        ),
        confirmation=AutoConfirmProvider(),
        mandate_id="m_test",
    )


# ─── Core persistence tests ──────────────────────────────────────────────────


class TestCardSetPopulated:
    def test_shoes_discovery_populates_card_sets(self, multi_merchant_ctx):
        shoe = _shoe_product()
        orch = _orch(shoe)
        asyncio.run(orch.run(multi_merchant_ctx, "find shoes"))
        assert len(multi_merchant_ctx.session.last_discovered_products) >= 1

    def test_headphones_discovery_populates_last_discovered(self, multi_merchant_ctx):
        hp = _headphone_product()
        orch = _orch(hp)
        asyncio.run(orch.run(multi_merchant_ctx, "find headphones"))
        assert len(multi_merchant_ctx.session.last_discovered_products) >= 1

    def test_coffee_discovery_populates_last_discovered(self, multi_merchant_ctx):
        mug = _mug_product()
        orch = _orch(mug)
        asyncio.run(orch.run(multi_merchant_ctx, "find mugs"))
        assert len(multi_merchant_ctx.session.last_discovered_products) >= 1


class TestCardSetsViaHttpClient:
    def test_card_sets_empty_on_fresh_session(self, client):
        client.get("/")
        sess = _sess(client)
        assert sess.ctx.session.product_card_sets == []

    def test_card_sets_cleared_on_chat_reset(self, client):
        client.get("/")
        sess = _sess(client)
        # Manually add a fake card set
        sess.ctx.session.product_card_sets.append({"turn_count": 1, "products": [_shoe_product()]})
        assert len(sess.ctx.session.product_card_sets) == 1

        client.post("/chat/reset")
        assert (
            sess.ctx.session.product_card_sets == []
        ), "product_card_sets must be cleared on chat reset"

    def test_chat_page_loads_with_card_sets_in_session(self, client):
        """If card sets exist, GET /chat should not error."""
        client.get("/")
        sess = _sess(client)
        sess.ctx.session.product_card_sets.append({"turn_count": 2, "products": [_shoe_product()]})
        r = client.get("/chat")
        assert r.status_code == 200


class TestCardSetsStructure:
    def test_card_set_entry_has_required_keys(self, multi_merchant_ctx):
        """Each entry must have turn_count and products keys."""
        multi_merchant_ctx.session.product_card_sets.append(
            {"turn_count": 3, "products": [_shoe_product()]}
        )
        entry = multi_merchant_ctx.session.product_card_sets[0]
        assert "turn_count" in entry
        assert "products" in entry
        assert isinstance(entry["products"], list)

    def test_card_set_products_have_product_id(self, multi_merchant_ctx):
        multi_merchant_ctx.session.product_card_sets.append(
            {"turn_count": 1, "products": [_shoe_product(), _headphone_product()]}
        )
        for p in multi_merchant_ctx.session.product_card_sets[0]["products"]:
            assert "product_id" in p

    def test_multiple_sequential_runs_track_independently(self, multi_merchant_ctx):
        """Two discovery runs → two separate entries in product_card_sets,
        each linked to a different turn_count."""
        shoe = _shoe_product()
        hp = _headphone_product()

        # Simulate what _run_orchestrator does: append after each run
        multi_merchant_ctx.session.conversation.append({"role": "user", "content": "find shoes"})
        multi_merchant_ctx.session.product_card_sets.append({"turn_count": 1, "products": [shoe]})
        multi_merchant_ctx.session.conversation.append(
            {"role": "assistant", "content": "Here are shoes."}
        )

        multi_merchant_ctx.session.conversation.append(
            {"role": "user", "content": "find headphones"}
        )
        multi_merchant_ctx.session.product_card_sets.append({"turn_count": 3, "products": [hp]})
        multi_merchant_ctx.session.conversation.append(
            {"role": "assistant", "content": "Here are headphones."}
        )

        assert len(multi_merchant_ctx.session.product_card_sets) == 2
        assert multi_merchant_ctx.session.product_card_sets[0]["turn_count"] == 1
        assert multi_merchant_ctx.session.product_card_sets[1]["turn_count"] == 3
        assert (
            multi_merchant_ctx.session.product_card_sets[0]["products"][0]["product_id"]
            == "ath_001"
        )
        assert (
            multi_merchant_ctx.session.product_card_sets[1]["products"][0]["product_id"]
            == "aud_002"
        )


class TestChatLogRendersPersistedCards:
    def test_chat_log_includes_product_card_html_from_stored_sets(self, client):
        """GET /chat must include product card HTML for stored card sets."""
        client.get("/")
        sess = _sess(client)

        # Prime conversation with an assistant turn
        sess.ctx.session.conversation.append(
            {"role": "user", "content": [{"type": "text", "text": "find shoes"}]}
        )
        sess.ctx.session.conversation.append(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Here are the shoes."}],
            }
        )
        # Link card set to turn_count=2 (after both turns)
        sess.ctx.session.product_card_sets.append({"turn_count": 2, "products": [_shoe_product()]})

        r = client.get("/chat")
        assert r.status_code == 200
        # The server-rendered log should contain product card HTML
        assert "chat-product-card" in r.text or "Demo Running Shoes" in r.text

    def test_chat_log_includes_headphone_card(self, client):
        client.get("/")
        sess = _sess(client)
        sess.ctx.session.conversation.extend(
            [
                {"role": "user", "content": [{"type": "text", "text": "headphones"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "Here."}]},
            ]
        )
        sess.ctx.session.product_card_sets.append(
            {"turn_count": 2, "products": [_headphone_product()]}
        )
        r = client.get("/chat")
        assert "Noise-Cancelling Headphones" in r.text or "chat-product-card" in r.text

    def test_chat_log_includes_mug_card(self, client):
        client.get("/")
        sess = _sess(client)
        sess.ctx.session.conversation.extend(
            [
                {"role": "user", "content": [{"type": "text", "text": "mugs"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "Here."}]},
            ]
        )
        sess.ctx.session.product_card_sets.append({"turn_count": 2, "products": [_mug_product()]})
        r = client.get("/chat")
        assert "Ceramic Coffee Mug" in r.text or "chat-product-card" in r.text

    def test_chat_log_no_stale_cards_after_reset(self, client):
        client.get("/")
        sess = _sess(client)
        sess.ctx.session.product_card_sets.append({"turn_count": 1, "products": [_shoe_product()]})
        client.post("/chat/reset")

        r = client.get("/chat")
        assert r.status_code == 200
        # After reset, no product cards should appear (conversation is empty)
        # The page may show the empty-state hero instead
        assert "chat-product-card" not in r.text or "Demo Running Shoes" not in r.text

    def test_click_confirmation_styled_not_italic_arrow(self, client):
        """[via UI click] messages must not render as grey italic arrows."""
        client.get("/")
        sess = _sess(client)
        # Add a click note to the conversation
        sess.ctx.session.conversation.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "[via UI click] added Demo Running Shoes × 1 (at 2026-01-01T00:00:00+00:00)",
                    }
                ],
            }
        )
        r = client.get("/chat")
        assert r.status_code == 200
        # Old grey italic arrow rendering should be gone
        assert (
            "↳ [via UI click]" not in r.text
        ), "Click notes must not render as '↳ [via UI click]' grey italic text"
        # New styled confirmation should appear
        assert "Added" in r.text or "added" in r.text


class TestIntermediaryThoughtsNotSurfaced:
    """An assistant turn that ALSO contains tool_use blocks is the model's
    intermediate reasoning emitted alongside a tool call. It is never
    streamed live (only the final text-only reply is pushed to SSE), so it
    must NOT surface as a chat bubble on page reload.

    Covers all three merchants — the leak was reported generically, so
    every product category's discovery flow must be clean.
    """

    def _seed_thinking_then_reply(self, sess, thought, product, reply):
        """Mimic a real discovery run's conversation shape:
          1. user message
          2. assistant 'thinking' turn: text + tool_use (search)
          3. user tool_result
          4. assistant final reply (text only)
        and the linked card set on the final turn.
        """
        conv = sess.ctx.session.conversation
        conv.append({"role": "user", "content": [{"type": "text", "text": "find something"}]})
        conv.append(
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": thought},
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "call_discovery_agent",
                        "input": {},
                    },
                ],
            }
        )
        conv.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_1",
                        "content": _discovery_json(product),
                    },
                ],
            }
        )
        conv.append({"role": "assistant", "content": [{"type": "text", "text": reply}]})
        sess.ctx.session.product_card_sets.append({"turn_count": len(conv), "products": [product]})

    @pytest.mark.parametrize(
        "product,thought,reply",
        [
            (
                _shoe_product(),
                "Let me search Athletic Co for running shoes.",
                "Here are three running shoe options.",
            ),
            (
                _headphone_product(),
                "I'll look up noise-cancelling headphones now.",
                "These headphones match your budget.",
            ),
            (
                _mug_product(),
                "Searching Coffee Bar's mug selection.",
                "Found a ceramic mug for you.",
            ),
        ],
    )
    def test_thinking_turn_text_not_rendered(self, client, product, thought, reply):
        client.get("/")
        sess = _sess(client)
        self._seed_thinking_then_reply(sess, thought, product, reply)

        r = client.get("/chat")
        assert r.status_code == 200
        # The intermediate "thinking" text must NOT appear as a bubble.
        assert thought not in r.text, f"intermediary model thought leaked onto reload: {thought!r}"
        # The real final reply MUST still appear.
        assert reply in r.text
        # And the cards (linked to the final reply turn) must still render.
        assert "chat-product-card" in r.text or product["name"] in r.text

    def test_chat_history_global_drops_tool_use_turns(self, client):
        """chat_history() must omit assistant turns containing tool_use."""
        client.get("/")
        sess = _sess(client)
        self._seed_thinking_then_reply(
            sess,
            "Internal reasoning here.",
            _shoe_product(),
            "Final visible reply.",
        )
        # Reach into the registered Jinja global to assert directly.
        app = client.app
        hist = app.state.templates.env.globals["chat_history"]

        # Build a minimal request-like object carrying the session cookie.
        class _Req:
            def __init__(self, cookies):
                self.cookies = cookies

        cookies = {"ac_session": client.cookies.get("ac_session")}
        rendered = hist(_Req(cookies))
        texts = [t["text"] for t in rendered]
        assert "Internal reasoning here." not in texts
        assert "Final visible reply." in texts


class TestCardsRenderBeforeSummaryText:
    """The live SSE stream inserts product cards ABOVE the summary text
    (the `products` placeholder is appended before the `text` bubble in
    _chat_sse.html). The server-rendered reload must keep that same order,
    otherwise the summary flips from below the cards (live) to above them
    (reload) — the reordering bug the user reported.

    Verified for all three merchants.
    """

    @pytest.mark.parametrize(
        "product,summary",
        [
            (_shoe_product(), "All three running shoes are in stock."),
            (_headphone_product(), "Both headphone options ship free."),
            (_mug_product(), "This mug is the only one under $20."),
        ],
    )
    def test_card_html_precedes_summary_text(self, client, product, summary):
        client.get("/")
        sess = _sess(client)
        sess.ctx.session.conversation.extend(
            [
                {"role": "user", "content": [{"type": "text", "text": "find it"}]},
                {"role": "assistant", "content": [{"type": "text", "text": summary}]},
            ]
        )
        sess.ctx.session.product_card_sets.append({"turn_count": 2, "products": [product]})

        r = client.get("/chat")
        assert r.status_code == 200
        body = r.text
        # Both the card and the summary must be present...
        card_marker = product["name"]
        assert card_marker in body
        assert summary in body
        # ...and the card must come BEFORE the summary text in the markup.
        assert body.index(card_marker) < body.index(summary), (
            "product cards must render above the summary text to match the " "live SSE order"
        )
