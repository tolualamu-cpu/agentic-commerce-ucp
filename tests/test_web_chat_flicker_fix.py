"""Chat "flicker" fix — empty-state reload race on POST /chat.

Root cause (the bug the user kept hitting): the empty /chat hero hides the
chat-log and, after a successful POST, navigates to /chat so the page
re-renders in the active (streaming) state. But the orchestrator runs in a
BACKGROUND task and only appends the user turn once ``BaseAgent.run``
executes inside it — AFTER the handler returned 202 and the browser already
navigated. The GET /chat therefore raced an empty ``session.conversation``,
re-rendered the EMPTY hero, and the streamed reply landed in a hidden
#chat-log (invisible). The user saw nothing, re-submitted, and the page
flickered through repeated full-page reloads.

The fix: ``POST /chat`` now persists the user turn into
``session.conversation`` SYNCHRONOUSLY, before returning. The
post-navigation GET /chat then sees a non-empty history and renders the
ACTIVE state (user bubble + visible log), so the SSE reply streams into a
visible log. ``BaseAgent.run`` guards against re-appending that identical
last turn so the model's context is not duplicated.

These tests run in OFFLINE mode (no ANTHROPIC_API_KEY) — exactly the path
that pre-append fixes deterministically without a live model. The unit
tests for the double-append guard drive a real ``OrchestratorAgent`` with a
scripted ``FakeAnthropicClient``.

Sorts alphabetically after test_user_journeys.py (w > u), so asyncio.run()
is safe here.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from agents.orchestrator import OrchestratorAgent
from cli.confirmation import AutoConfirmProvider
from tests.fake_anthropic import FakeAnthropicClient, text_response
from web import session as session_mod
from web.app import create_app

# The old full-page-navigation snippet. The empty state NO LONGER carries it:
# it now transitions in place via __chatRevealActive so the single
# /chat/stream EventSource stays alive (the dropped-burst race fix). Neither
# the empty hero nor the active state may carry this snippet anymore.
RELOAD_SNIPPET = "window.location.href = '/chat'"
REVEAL_SNIPPET = "window.__chatRevealActive"


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


def _last_user_text(sess) -> str | None:
    for turn in reversed(sess.ctx.session.conversation):
        if turn.get("role") == "user":
            content = turn.get("content")
            if isinstance(content, str):
                return content
    return None


# Diverse shopper entry messages spanning all three demo merchants + price
# tiers, per the project testing rule (never test against one category).
MERCHANT_MESSAGES = [
    pytest.param("find me running shoes", id="athletic-co-shoes"),
    pytest.param("show me wireless headphones", id="audio-hub-headphones"),
    pytest.param("I need a ceramic coffee mug", id="coffee-bar-mug"),
    pytest.param("compare the cheapest headphones across stores", id="cross-merchant"),
    pytest.param("find a studio monitor speaker over $500", id="high-price-tier"),
]


# ─── Empty-state hero (pre-submit) ───────────────────────────────────────────


class TestEmptyHeroBeforeSubmit:
    def test_fresh_chat_renders_empty_hero(self, client):
        """A brand-new session with no conversation renders the centered
        hero, which transitions IN PLACE on submit (no full reload) so the
        single /chat/stream EventSource keeps streaming the reply."""
        r = client.get("/chat")
        assert r.status_code == 200
        assert REVEAL_SNIPPET in r.text, "empty hero must transition in place"
        assert RELOAD_SNIPPET not in r.text, (
            "empty hero must NOT full-reload (that dropped the SSE burst)"
        )
        # Hero starts with the chat-log hidden; __chatRevealActive un-hides it.
        assert 'id="chat-log" class="hidden"' in r.text


# ─── The race fix: POST persists the user turn synchronously ─────────────────


class TestPostPersistsUserTurnSynchronously:
    @pytest.mark.parametrize("message", MERCHANT_MESSAGES)
    def test_post_chat_appends_user_turn_immediately(self, client, message):
        """The user turn must be in session.conversation the instant POST
        returns — NOT later, inside the background orchestrator task."""
        client.get("/")  # establish session
        sess = _sess(client)
        assert sess.ctx.session.conversation == [], "precondition: empty history"

        r = client.post("/chat", data={"message": message})
        assert r.status_code == 202
        # Synchronously persisted — this is what closes the navigation race.
        assert _last_user_text(sess) == message
        # Stored as a plain-string user turn (what chat_history renders as a bubble).
        assert sess.ctx.session.conversation[-1] == {"role": "user", "content": message}

    @pytest.mark.parametrize("message", MERCHANT_MESSAGES)
    def test_post_then_get_renders_active_state_no_reload(self, client, message):
        """After POST, GET /chat (the page the browser navigates to) renders
        the ACTIVE state: the user bubble is visible and the form no longer
        navigates. This is the post-fix behaviour that stops the flicker —
        the navigated-to page is correct instead of the empty hero again."""
        client.get("/")
        client.post("/chat", data={"message": message})

        r = client.get("/chat")
        assert r.status_code == 200
        # Active state: the user's message is rendered as a server-side bubble.
        assert message in r.text
        # Active state: NO full-page navigation handler (SSE streams the reply).
        assert RELOAD_SNIPPET not in r.text, (
            "post-submit page must be active state, not the reloading hero"
        )
        # The log container is no longer the hidden hero placeholder.
        assert 'id="chat-log" class="hidden"' not in r.text

    def test_empty_message_does_not_persist(self, client):
        """A blank submit is rejected and never pollutes the conversation."""
        client.get("/")
        sess = _sess(client)
        r = client.post("/chat", data={"message": "   "})
        assert r.status_code == 400
        assert sess.ctx.session.conversation == []


# ─── Gate path must NOT pre-append (its messages are routed, not new turns) ──


class TestGatePathDoesNotPreAppend:
    def test_message_during_active_gate_is_not_appended_as_new_turn(self, client):
        """When a confirmation gate is awaiting input, a chat message is
        routed to the gate inbox — it must NOT be appended as a fresh user
        turn (that would corrupt the in-flight conversation)."""
        client.get("/")
        sess = _sess(client)
        # Simulate an open gate awaiting the user's confirm/cancel.
        sess.gate_provider.awaiting_input = True

        before = list(sess.ctx.session.conversation)
        r = client.post("/chat", data={"message": "confirm"})
        # Routed to the gate (202), not started as a new orchestrator run.
        assert r.status_code == 202
        assert r.json().get("status") == "routed_to_gate"
        # Conversation is unchanged by the gate-routed message.
        assert sess.ctx.session.conversation == before


# ─── Unit: BaseAgent.run double-append guard ─────────────────────────────────


def _orchestrator(responses):
    return OrchestratorAgent(
        client=FakeAnthropicClient(responses),
        confirmation=AutoConfirmProvider(),
        mandate_id="m_test",
    )


class TestRunDoubleAppendGuard:
    def test_pre_seeded_user_turn_is_not_duplicated(self, multi_merchant_ctx):
        """If the web layer already appended the user turn (as POST /chat
        now does), run() must NOT append it a second time — otherwise the
        model context and the chat log would show the message twice."""
        ctx = multi_merchant_ctx
        # Simulate the synchronous pre-append done by POST /chat.
        ctx.session.conversation.append({"role": "user", "content": "find me shoes"})

        orch = _orchestrator([text_response("Here are some options.")])
        asyncio.run(orch.run(ctx, "find me shoes"))

        user_turns = [
            t
            for t in ctx.session.conversation
            if t.get("role") == "user" and t.get("content") == "find me shoes"
        ]
        assert len(user_turns) == 1, f"user turn duplicated: {ctx.session.conversation}"

    def test_new_message_after_assistant_reply_is_appended(self, multi_merchant_ctx):
        """The guard must only suppress an identical *last* turn. A genuinely
        new message after a prior assistant reply is appended normally."""
        ctx = multi_merchant_ctx
        ctx.session.conversation.extend(
            [
                {"role": "user", "content": "find me shoes"},
                {"role": "assistant", "content": [{"type": "text", "text": "Here you go."}]},
            ]
        )

        orch = _orchestrator([text_response("Sure, here are mugs.")])
        asyncio.run(orch.run(ctx, "now find me a mug"))

        assert any(
            t.get("role") == "user" and t.get("content") == "now find me a mug"
            for t in ctx.session.conversation
        ), "a new, different user message must still be appended"

    def test_repeated_identical_message_in_separate_turns_is_appended(self, multi_merchant_ctx):
        """Sending the same text twice in DIFFERENT turns is legitimate: the
        second submit's history ends in an assistant reply, so the guard does
        not fire and the repeat is recorded."""
        ctx = multi_merchant_ctx

        orch1 = _orchestrator([text_response("First answer.")])
        asyncio.run(orch1.run(ctx, "find me shoes"))

        orch2 = _orchestrator([text_response("Second answer.")])
        asyncio.run(orch2.run(ctx, "find me shoes"))

        user_turns = [
            t
            for t in ctx.session.conversation
            if t.get("role") == "user" and t.get("content") == "find me shoes"
        ]
        assert len(user_turns) == 2, "two separate identical submits must both be recorded"
