"""Pins the chat page's DOM uniqueness invariants.

Critical invariants:
  - Exactly one rendered `id="chat-log"` at any time (either the active-state
    element or the empty-state placeholder, never both).
  - Exactly one rendered `id="chat-status"`.
  - Exactly one rendered `id="chat-form"`.

If a future template change accidentally renders BOTH branches of the
{% if _has_messages %}/{% else %} block, the duplicate IDs would cause
`document.getElementById("chat-log")` to return the wrong element (the
first one in DOM order), which is exactly the failure mode where products
fragments get injected into a hidden element and never become visible.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from web import session as session_mod  # noqa: F401
from web.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "demo.json"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = create_app()
    with TestClient(app) as c:
        yield c


def _count_attr(html: str, attr: str) -> int:
    """Count occurrences of an HTML attribute=value pair, robust to quote
    style and surrounding whitespace."""
    import re

    pattern = re.compile(re.escape(attr))
    return len(pattern.findall(html))


class TestChatDomUniqueness:
    def test_empty_state_renders_single_chat_log(self, client):
        """On a fresh session, the empty hero state should render exactly ONE
        element with id='chat-log' (the hidden placeholder that
        __chatRevealActive un-hides on first submit)."""
        r = client.get("/chat")
        assert r.status_code == 200
        assert _count_attr(r.text, 'id="chat-log"') == 1, (
            "chat.html must render exactly ONE id='chat-log' element. "
            "If both {% if %}/{% else %} branches render, "
            "document.getElementById('chat-log') returns the first one and "
            "fragments may inject into a hidden element."
        )

    def test_empty_state_renders_single_chat_status(self, client):
        r = client.get("/chat")
        assert _count_attr(r.text, 'id="chat-status"') == 1

    def test_empty_state_renders_single_chat_form(self, client):
        r = client.get("/chat")
        assert _count_attr(r.text, 'id="chat-form"') == 1

    def test_active_state_renders_single_chat_log(self, client):
        """After the user submits a message, the page enters active state and
        again must render exactly ONE id='chat-log'."""
        client.get("/")  # establish session
        # Add a turn to history so chat.html renders the active branch.
        sid_raw = client.cookies.get("ac_session")
        sid = session_mod._serializer.loads(sid_raw)
        sess = session_mod.get_session_by_id(sid)
        sess.ctx.session.conversation.append({"role": "user", "text": "find me coffee mugs"})
        sess.ctx.session.conversation.append(
            {"role": "assistant", "text": "Here are some options."}
        )
        r = client.get("/chat")
        assert r.status_code == 200
        assert _count_attr(r.text, 'id="chat-log"') == 1, (
            "Active-state chat.html must render exactly ONE id='chat-log'."
        )

    def test_active_state_renders_single_chat_status(self, client):
        client.get("/")
        sid_raw = client.cookies.get("ac_session")
        sid = session_mod._serializer.loads(sid_raw)
        sess = session_mod.get_session_by_id(sid)
        sess.ctx.session.conversation.append({"role": "user", "text": "hi"})
        sess.ctx.session.conversation.append({"role": "assistant", "text": "hello"})
        r = client.get("/chat")
        assert _count_attr(r.text, 'id="chat-status"') == 1
