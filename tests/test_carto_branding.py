"""Carto branding tests.

Verifies:
- Template title blocks reference "Carto" not "Agentic Commerce".
- `chat_history()` filters out `[at confirmation gate]` turns from the
  rendered log (they are internal orchestrator context, not user-visible).
- `mandate_info()` Jinja global returns correct mandate and spend data.
- Footer copy updated to new product-simulation wording.

Asyncio note: file sorts before test_user_journeys.py, so async work uses
asyncio.get_event_loop().run_until_complete() throughout.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "web" / "templates"


# ─── Template copy checks ─────────────────────────────────────────────────────


def test_base_html_title_is_carto():
    text = (TEMPLATE_DIR / "base.html").read_text()
    assert "Carto" in text
    assert "Agentic Commerce" not in text


def test_base_html_brand_span_is_carto():
    text = (TEMPLATE_DIR / "base.html").read_text()
    assert ">Carto<" in text
    assert "Agentic&nbsp;Commerce" not in text


def test_chat_html_title_is_carto():
    text = (TEMPLATE_DIR / "chat.html").read_text()
    assert "Chat · Carto" in text
    assert "Agentic Commerce" not in text


def test_hero_copy_updated():
    text = (TEMPLATE_DIR / "_hero.html").read_text()
    assert "Carto finds, compares, and buys from your favorite brands" in text
    assert "Carto can find, compare, and buy" not in text
    assert "I'll find, compare, and buy it." not in text
    assert "Describe what you want." not in text


def test_footer_copy_updated():
    text = (TEMPLATE_DIR / "base.html").read_text()
    assert "Product simulation with demo storefronts" in text
    assert "No real purchases are made" in text
    assert "No real money moves" not in text


def test_navbar_contains_ai_personal_shopper_tagline():
    text = (TEMPLATE_DIR / "base.html").read_text()
    assert "Your AI Personal Shopper" in text or "Your AI personal shopper" in text.lower()


def test_hero_does_not_contain_duplicate_tagline():
    text = (TEMPLATE_DIR / "_hero.html").read_text()
    assert "Your AI personal shopper" not in text
    assert "Your AI Personal Shopper" not in text


def test_chat_does_not_contain_duplicate_tagline():
    text = (TEMPLATE_DIR / "chat.html").read_text()
    assert "Your AI personal shopper" not in text
    assert "Your AI Personal Shopper" not in text


# ─── chat_history() filtering of gate Q&A turns ──────────────────────────────


@pytest.fixture
def web_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "demo.json"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from web.app import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


def _get_session(client):
    from web import session as session_mod

    sid_raw = client.cookies.get("ac_session")
    sid = session_mod._serializer.loads(sid_raw)
    return session_mod.get_session_by_id(sid)


def test_chat_history_hides_at_confirmation_gate_turns(web_client):
    """Gate Q&A context turns must not appear in the rendered chat log."""
    web_client.get("/")
    sess = _get_session(web_client)

    sess.ctx.session.conversation.extend(
        [
            {"role": "user", "content": "buy shoes"},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Proceeding to checkout."}],
            },
            # These are gate Q&A turns that should NOT be rendered
            {
                "role": "user",
                "content": [{"type": "text", "text": "[at confirmation gate] add 1 more"}],
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Updated quantity from 1 to 2."}],
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Your order has been placed."}],
            },
        ]
    )

    # Verify the Jinja global filters them out of the returned history list
    from web.app import create_app

    app = create_app()
    history_fn = app.state.templates.env.globals["chat_history"]

    class FakeRequest:
        cookies = web_client.cookies

    history = history_fn(FakeRequest())
    texts = [t["text"] for t in history]

    # Gate Q&A turn must be absent from the history list
    assert not any(
        t.startswith("[at confirmation gate]") for t in texts
    ), f"Gate Q&A turn leaked into chat_history: {texts}"
    # "add 1 more" embedded inside the gate prefix must also be absent
    assert not any(
        "add 1 more" in t for t in texts
    ), f"Gate Q&A content leaked into chat_history: {texts}"

    # Normal turns must still be present
    assert any("buy shoes" in t for t in texts)
    assert any("Proceeding to checkout" in t for t in texts)
    assert any("Your order has been placed" in t for t in texts)


def test_chat_history_preserves_non_gate_user_turns(web_client):
    """Regular user messages must remain visible after the gate filter."""
    web_client.get("/")
    sess = _get_session(web_client)

    sess.ctx.session.conversation.extend(
        [
            {"role": "user", "content": "show me wireless headphones"},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Here are the top options."}],
            },
        ]
    )

    r = web_client.get("/chat")
    assert r.status_code == 200
    assert "show me wireless headphones" in r.text
    assert "Here are the top options" in r.text


def test_chat_history_filters_only_gate_prefix(web_client):
    """Only the '[at confirmation gate]' prefix triggers filtering — regular
    messages are unaffected."""
    web_client.get("/")
    sess = _get_session(web_client)

    sess.ctx.session.conversation.extend(
        [
            {"role": "user", "content": "[at confirmation gate] confirm"},
            {"role": "user", "content": "just a normal message"},
        ]
    )

    from web.app import create_app

    app = create_app()
    history_fn = app.state.templates.env.globals["chat_history"]

    class FakeRequest:
        cookies = web_client.cookies

    texts = [t["text"] for t in history_fn(FakeRequest())]
    assert not any(t.startswith("[at confirmation gate]") for t in texts)
    assert any("just a normal message" in t for t in texts)


# ─── mandate_info() global ────────────────────────────────────────────────────


def test_mandate_info_returns_mandate_and_spend(web_client):
    """mandate_info() on a session with an active mandate returns usable data."""
    from decimal import Decimal

    web_client.get("/")
    sess = _get_session(web_client)

    sess.ctx.ap2.create_mandate(
        user_id="user_1",
        max_amount=Decimal("500"),
        daily_cap=Decimal("1000"),
        monthly_cap=Decimal("5000"),
        payment_method_id="pm_test",
    )

    from web.app import create_app

    app = create_app()
    jinja_env = app.state.templates.env
    mandate_info = jinja_env.globals["mandate_info"]

    class FakeRequest:
        cookies = web_client.cookies

    info = mandate_info(FakeRequest())
    assert "mandate" in info
    assert "spent_today" in info
    assert info["mandate"].daily_cap == Decimal("1000")
    assert info["spent_today"] == "0"


def test_mandate_info_returns_empty_dict_without_session(web_client):
    """mandate_info() returns {} gracefully when the request has no session."""
    from web.app import create_app

    app = create_app()
    jinja_env = app.state.templates.env
    mandate_info = jinja_env.globals["mandate_info"]

    class FakeRequest:
        cookies = {}

    info = mandate_info(FakeRequest())
    assert info == {}


# ─── Navbar spend widget visible on all pages ─────────────────────────────────


@pytest.mark.parametrize("path", ["/", "/chat", "/orders", "/mandate", "/profile", "/audit"])
def test_page_renders_without_error(web_client, path):
    """Every main page must render successfully (200) without crashing on the
    mandate_info global when no mandate exists."""
    r = web_client.get(path)
    assert r.status_code == 200, f"GET {path} returned {r.status_code}"
