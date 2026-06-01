"""Phase 8b — Explore (/) and Chat (/chat) split into separate pages.

This file exercises the new GET /chat route added in Phase 8b. The
underlying agent stack and POST /chat / SSE / WebSocket protocols are
unchanged; we just verify the routing split renders correctly and the
conversation history surfaces server-side on the chat page.
"""

import pytest
from fastapi.testclient import TestClient

from web import session as session_mod
from web.app import create_app


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


class TestChatPageRoute:
    def test_chat_page_renders_200(self, client):
        # GET /chat returns 200 with the input form and log container
        # in the HTML — empty conversation is still a valid page.
        r = client.get("/chat")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert 'id="chat-form"' in r.text
        assert 'id="chat-input"' in r.text
        assert 'id="chat-log"' in r.text

    def test_chat_page_renders_conversation_history(self, client):
        # Seed a user turn directly into session.conversation; reloading
        # /chat must surface it as a server-rendered bubble.
        client.get("/")  # establish session
        sess = _sess(client)
        sess.ctx.session.conversation.append(
            {
                "role": "user",
                "content": [{"type": "text", "text": "find me coffee mugs"}],
            }
        )
        sess.ctx.session.conversation.append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "Here are three ceramic mugs from coffee-bar.",
                    }
                ],
            }
        )
        r = client.get("/chat")
        assert r.status_code == 200
        assert "find me coffee mugs" in r.text
        assert "ceramic mugs" in r.text

    def test_chat_page_offers_chips_when_empty(self, client):
        # Empty-state chat page mirrors the Explore hero: chips visible
        # so the user can kick off a conversation.
        r = client.get("/chat")
        # At least one of the canned chip prompts is in the rendered HTML
        assert "Find me running shoes under $200" in r.text


class TestHomeNoLongerCarriesChatLog:
    def test_home_renders_hero_but_not_log(self, client):
        # Home is now Explore — discovery surface only. The chat-log
        # container belongs on /chat, not /.
        r = client.get("/")
        assert 'id="chat-log"' not in r.text
        # But the hero input still lives here (it's what kicks off
        # navigation to /chat)
        assert 'id="chat-input"' in r.text

    def test_home_hero_navigates_to_chat_on_submit(self, client):
        # The hero form should be wired to navigate to /chat after the
        # POST completes. We check the template's hx-on attribute as
        # a proxy — the rendered HTML must reference /chat in the
        # navigation hook so a real browser follows through.
        r = client.get("/")
        assert (
            "window.location.href = '/chat'" in r.text or 'window.location.href = "/chat"' in r.text
        )


class TestHeaderTabs:
    def test_header_has_explore_and_chat_tabs(self, client):
        # Both top-level tabs must be present on every page.
        for path in ("/", "/chat", "/mandate", "/orders"):
            r = client.get(path)
            assert r.status_code == 200, f"{path} should render"
            assert 'href="/"' in r.text, f"Explore link missing on {path}"
            assert 'href="/chat"' in r.text, f"Chat link missing on {path}"
