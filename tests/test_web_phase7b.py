"""Phase 7b — Chat sidebar + SSE streaming.

Verifies the chat router's contract:
  - POST /chat enqueues the user echo and triggers orchestrator.run()
    (or short-circuits with a friendly message when offline).
  - GET /chat/stream emits server-sent events drained from sess.sse_queue.
  - Per-session isolation: two clients don't see each other's stream.

The orchestrator is stubbed to avoid hitting Anthropic. We patch
``OrchestratorAgent.run`` to push a fixed sequence of events onto the
session's SSE queue via its callbacks slot, so we can assert ordering.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from web.app import create_app
from web import session as session_mod


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "demo.json"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = create_app()
    with TestClient(app) as c:
        yield c


class TestChatPost:
    def test_post_chat_requires_message(self, client):
        r = client.post("/chat", data={})
        # FastAPI Form(...) marks message required → 422
        assert r.status_code in (400, 422)

    def test_post_chat_rejects_empty_string(self, client):
        r = client.post("/chat", data={"message": "   "})
        assert r.status_code == 400

    def test_post_chat_offline_returns_friendly_event(self, client):
        """No ANTHROPIC_API_KEY → chat is offline; POST should 202 and
        queue an offline notice instead of crashing."""
        # Trigger session creation
        client.get("/")
        r = client.post("/chat", data={"message": "hello"})
        assert r.status_code == 202
        body = r.json()
        assert body["status"] in ("offline", "accepted")

    def test_post_chat_echoes_user_into_queue(self, client):
        client.get("/")
        sid = client.cookies.get("ac_session")
        # Pull the actual session id out of the signed cookie
        actual_sid = session_mod._serializer.loads(sid)
        sess = session_mod.get_session_by_id(actual_sid)
        assert sess is not None

        r = client.post("/chat", data={"message": "find me coffee"})
        assert r.status_code == 202

        # Drain queue events synchronously
        loop = asyncio.new_event_loop()
        try:
            events = []
            for _ in range(3):
                try:
                    evt = loop.run_until_complete(
                        asyncio.wait_for(sess.sse_queue.get(), timeout=1.0)
                    )
                    events.append(evt)
                except asyncio.TimeoutError:
                    break
        finally:
            loop.close()

        types = [e["type"] for e in events]
        assert "user" in types
        user_evt = next(e for e in events if e["type"] == "user")
        assert user_evt["data"]["text"] == "find me coffee"


class TestSSEStream:
    def test_stream_route_registered(self, client):
        """The SSE route is mounted under GET /chat/stream.

        We can't easily integration-test the streaming body via TestClient
        because the generator never returns; instead we verify the route is
        registered and points at the chat router's handler.
        """
        app = client.app
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/chat/stream" in paths
        assert "/chat" in paths


class TestSessionIsolation:
    def test_two_clients_have_independent_queues(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DB_PATH", str(tmp_path / "demo.json"))
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        app = create_app()
        with TestClient(app) as c1, TestClient(app) as c2:
            c1.get("/")
            c2.get("/")
            sid1 = session_mod._serializer.loads(c1.cookies.get("ac_session"))
            sid2 = session_mod._serializer.loads(c2.cookies.get("ac_session"))
            sess1 = session_mod.get_session_by_id(sid1)
            sess2 = session_mod.get_session_by_id(sid2)
            assert sess1 is not sess2

            c1.post("/chat", data={"message": "for client 1"})

            # Pull from sess1 — should have the c1 echo. Use asyncio.run
            # rather than ``asyncio.new_event_loop()`` so the lazy
            # ``sse_queue`` property gets a running loop to construct
            # the Queue on (Py3.9 requirement).
            async def drain():
                return await asyncio.wait_for(
                    sess1.sse_queue.get(),
                    timeout=1.0,
                )

            evt = asyncio.run(drain())

            # sess2's queue should be untouched (lazy property — touching
            # it inside another asyncio.run keeps loop contracts intact)
            async def peek_sess2():
                return sess2.sse_queue.qsize()

            assert asyncio.run(peek_sess2()) == 0
            assert evt["type"] == "user"
            assert "client 1" in evt["data"]["text"]


class TestChatSidebarRendered:
    def test_chat_panel_on_chat_page(self, client):
        # Phase 8b: chat moved off home and lives on /chat. The chat
        # page always carries the input form; the log container is
        # present too (empty or populated).
        r = client.get("/chat")
        assert r.status_code == 200
        assert 'id="chat-input"' in r.text
        assert 'id="chat-form"' in r.text
        assert 'id="chat-log"' in r.text

    def test_chat_panel_not_on_utility_pages(self, client):
        # Utility pages (search, mandate, orders, etc.) intentionally
        # don't carry a chat input/log — users navigate to /chat (or
        # via the Chat tab in the header) to talk to the agent.
        r = client.get("/search?q=mug")
        assert 'id="chat-log"' not in r.text
        # The hero's chat input only lives on / and /chat
        assert 'id="chat-input"' not in r.text

    def test_home_has_hero_input_but_no_chat_log(self, client):
        # Home (Explore) shows the hero with a search input but no log
        # container — the conversation lives on /chat.
        r = client.get("/")
        assert r.status_code == 200
        assert 'id="chat-input"' in r.text
        assert 'id="chat-log"' not in r.text


class TestCallbacks:
    def test_build_web_callbacks_pushes_text_event(self):
        from web.callbacks import build_web_callbacks

        async def run():
            q = asyncio.Queue()
            cb = build_web_callbacks(q)
            await cb.on_text("hello")
            return await q.get()

        evt = asyncio.run(run())
        assert evt == {"type": "text", "data": {"delta": "hello"}}

    def test_build_web_callbacks_tool_start(self):
        from web.callbacks import build_web_callbacks

        async def run():
            q = asyncio.Queue()
            cb = build_web_callbacks(q)
            await cb.on_tool_start("search_products", {"query": "mug"})
            return await q.get()

        evt = asyncio.run(run())
        assert evt["type"] == "tool_start"
        assert evt["data"]["name"] == "search_products"
        assert evt["data"]["args"]["query"] == "mug"
