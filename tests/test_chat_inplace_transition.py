"""The PERMANENT fix for "the chat doesn't populate unless I flip to a
different page and back."

ROOT CAUSE (why it kept coming back): the empty ``/chat`` hero used to
``window.location.href = '/chat'`` after a successful POST. A full reload
tears down the page's single ``EventSource('/chat/stream')`` connection
(owned by ``_toast.html``) and opens a brand-new one. During the unload
gap there is a window where the OLD, dying connection is still the active
server-side consumer of the single-consumer ``sse_queue`` — so when the
orchestrator's ``user``/``products``/``text``/``done`` burst arrives it can
be drained by the connection that's about to vanish. Those events are NOT
re-queued, so the freshly-loaded page connects too late and gets nothing.
The reply was still persisted to ``conversation``, so navigating away and
back re-rendered it from server history — which is exactly the "flip to
another page and back" workaround the user kept hitting.

THE FIX (``web/templates/_chat_input.html`` + ``chat.html``): the empty
state transitions to the active state IN PLACE via
``window.__chatRevealActive()`` — it un-hides ``#chat-log``, drops the hero
headline + chips, and stops the vertical centering. There is NO reload, so
the SINGLE EventSource opened on page load stays alive the entire time and
remains the sole, active consumer of the burst. No reload ⇒ no second
connection ⇒ no dropped-burst race.

These tests pin BOTH halves:
  * the client wiring (template): the empty form transitions in place and
    NEVER full-reloads, the reveal helper does the right structural work,
    and the page opens exactly ONE ``/chat/stream`` connection;
  * the server contract the in-place design relies on: a single, never-
    superseded connection receives the full orchestrator burst IN ORDER
    (cards before summary), across every merchant + a cross-merchant
    basket, per the project testing rule.

ASYNC RULE: this file sorts alphabetically BEFORE ``test_user_journeys.py``
(``c`` < ``u``), so per CLAUDE.md it uses
``asyncio.get_event_loop().run_until_complete()`` and NEVER ``asyncio.run()``.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from config.catalogue import MERCHANTS
from web.app import create_app
from web.routers.chat import _KEEPALIVE, _session_sse_events
from web.session import WebSession


# ─── Fixtures / helpers ───────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "demo.json"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = create_app()
    with TestClient(app) as c:
        yield c


RELOAD_SNIPPET = "window.location.href = '/chat'"
REVEAL_FN = "window.__chatRevealActive"


def _run(coro):
    """Drive a coroutine without closing the loop (CLAUDE.md async rule)."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_session() -> WebSession:
    return WebSession(
        session_id="sess_inplace",
        db=None,
        ctx=None,
        orchestrator=None,
        mandate_id="m_test",
        gate_provider=None,
    )


async def _never_disconnected() -> bool:
    return False


async def _collect(agen):
    """Drain the SSE generator, stopping at the first idle keepalive."""
    out: list = []
    async for item in agen:
        if item is _KEEPALIVE:
            break
        out.append(item)
    return out


def _merchant_product(domain: str) -> dict:
    return dict(MERCHANTS[domain][0])


def _burst(domains: list[str]) -> list[dict]:
    """The orchestrator's real emit order: user echo → product cards →
    summary text → done."""
    return [
        {"type": "user", "data": {"text": "find me something"}},
        {
            "type": "products",
            "data": {"products": [_merchant_product(d) for d in domains]},
        },
        {"type": "text", "data": {"delta": "Here are some solid options."}},
        {"type": "done", "data": {}},
    ]


ATHLETIC = "athletic-co.myshopify.com"
AUDIO = "audio-hub.myshopify.com"
COFFEE = "coffee-bar.myshopify.com"


# ─── Client wiring: empty state transitions in place, never reloads ───────────


class TestEmptyStateTransitionsInPlace:
    def test_empty_chat_uses_reveal_not_reload(self, client):
        """The regression guard: the empty form must call __chatRevealActive
        on submit and must NOT carry the full-reload navigation that dropped
        the SSE burst."""
        r = client.get("/chat")
        assert r.status_code == 200
        assert REVEAL_FN in r.text, "empty form must transition in place"
        assert RELOAD_SNIPPET not in r.text, (
            "empty form must NOT full-reload — that reopens the EventSource "
            "and lets the dying connection steal the orchestrator burst"
        )

    def test_reveal_helper_unhides_log_and_drops_hero(self, client):
        """__chatRevealActive must reveal the (initially hidden) chat-log
        with the active layout classes and hide the hero headline + chips so
        the streamed reply is visible in place."""
        r = client.get("/chat")
        html = r.text
        # The log starts hidden; the helper un-hides it.
        assert 'id="chat-log" class="hidden"' in html
        # Structural hooks the helper toggles must exist.
        assert 'id="chat-empty-hero"' in html
        assert 'id="chat-headline-wrap"' in html
        assert 'id="chat-suggestion-chips"' in html
        # The helper assigns the active log layout and removes the hidden state.
        assert 'log.className = "flex-1 flex flex-col gap-3 text-sm pb-4 pt-2"' in html
        assert 'log.removeAttribute("aria-hidden")' in html
        assert 'headline.classList.add("hidden")' in html
        assert 'chips.classList.add("hidden")' in html

    def test_page_opens_exactly_one_stream_connection(self, client):
        """The whole fix rests on there being a SINGLE EventSource to
        /chat/stream (owned by _toast.html). If the chat page opened its own
        second one we'd be back to splitting the burst across consumers."""
        r = client.get("/chat")
        assert r.text.count('new EventSource("/chat/stream")') == 1

    def test_active_state_has_neither_reveal_nor_reload(self, client):
        """Once a conversation exists the page renders the ACTIVE state, whose
        form streams into an already-visible log — it must NOT DEFINE the
        reveal helper (there is no hero to drop) and must NOT reload.

        Note: the shared _chat_sse.html optimistic-submit handler *references*
        ``window.__chatRevealActive`` defensively (``if (window.__chatRevealActive)``)
        so the empty hero can reveal itself in place. That guarded reference is
        a harmless no-op in the active state (the helper is never defined here),
        so the invariant we pin is the absence of the DEFINITION, not the call."""
        client.get("/")
        from web import session as session_mod

        sid = session_mod._serializer.loads(client.cookies.get("ac_session"))
        sess = session_mod.get_session_by_id(sid)
        sess.ctx.session.conversation.extend(
            [
                {"role": "user", "content": "find me mugs"},
                {"role": "assistant", "content": [{"type": "text", "text": "Three mugs."}]},
            ]
        )
        r = client.get("/chat")
        assert RELOAD_SNIPPET not in r.text
        # The reveal helper DEFINITION belongs to the empty hero only.
        assert f"{REVEAL_FN} = function" not in r.text


# ─── Server contract: one stable connection gets the full burst in order ──────


class TestSingleConnectionReceivesFullBurst:
    @pytest.mark.parametrize(
        "domains",
        [
            pytest.param([ATHLETIC], id="athletic-co-single"),
            pytest.param([AUDIO], id="audio-hub-single"),
            pytest.param([COFFEE], id="coffee-bar-single"),
            pytest.param([ATHLETIC, AUDIO, COFFEE], id="cross-merchant-basket"),
        ],
    )
    def test_one_connection_no_supersede_delivers_ordered_burst(self, domains):
        """The in-place transition keeps ONE connection alive across the
        whole turn (it is never superseded because no new connection opens).
        That single connection must receive the entire user→cards→text→done
        burst IN ORDER — the exact delivery the page now relies on instead of
        the reload-and-reconnect dance that dropped events."""

        async def _t():
            sess = _make_session()
            # Exactly one connection for the life of the turn — the invariant
            # the no-reload transition guarantees.
            _, sup = sess.new_stream_generation()
            burst = _burst(domains)
            for evt in burst:
                sess.sse_queue.put_nowait(evt)

            got = await _collect(_session_sse_events(sess, sup, _never_disconnected, timeout=0.05))
            assert got == burst, "single stable connection must get the full burst"
            # Cards-first ordering preserved (no text-before-cards flicker).
            types = [e["type"] for e in got]
            assert types.index("products") < types.index("text") < types.index("done")
            assert sess.sse_queue.empty()

        _run(_t())

    def test_connection_stays_active_until_done(self):
        """Sanity: with no newer connection opening (the in-place case), the
        connection's superseded future never resolves mid-burst, so nothing
        is left unconsumed."""

        async def _t():
            sess = _make_session()
            gen, sup = sess.new_stream_generation()
            assert gen == 1
            assert not sup.done(), "the sole connection is never superseded"
            for evt in _burst([AUDIO]):
                sess.sse_queue.put_nowait(evt)
            got = await _collect(_session_sse_events(sess, sup, _never_disconnected, timeout=0.05))
            assert got[0]["type"] == "user"
            assert got[-1]["type"] == "done"
            assert not sup.done(), "still active after a full burst (no takeover)"

        _run(_t())
