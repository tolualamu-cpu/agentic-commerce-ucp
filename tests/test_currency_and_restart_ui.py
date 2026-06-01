"""Tests: two-decimal currency formatting + the in-app restart dialog.

1. The daily-spend bar rendered "$193.3200" (raw Decimal) after a purchase.
   A `money` Jinja filter now formats any dollar figure to exactly two
   decimal places, and base.html pipes both the spent + cap figures
   through it.

2. The "Restart conversation" control used the browser-native confirm()
   (which shows an unbranded "127.0.0.1 says" chrome). It now opens an
   in-app dialog reading "Restart conversation? Carto preserves your cart
   and order history." with Cancel / OK; OK submits the real reset form.

Sorts BEFORE test_user_journeys.py (c < u): synchronous TestClient only,
no asyncio.run() — the event loop is never created or closed here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

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


def _money_filter():
    return create_app().state.templates.env.filters["money"]


def _seed_conversation(client):
    """Put the chat into its active (has-messages) state so the sticky
    input + Restart control render."""
    sess = _sess(client)
    sess.ctx.session.conversation.append({"role": "user", "content": "find me running shoes"})
    sess.ctx.session.conversation.append(
        {"role": "assistant", "content": "Here are a few options."}
    )


# ─── money filter ───────────────────────────────────────────────────────────


class TestMoneyFilter:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("193.3200", "193.32"),  # the reported bug
            ("1000", "1000.00"),
            ("0", "0.00"),
            (0, "0.00"),
            (22, "22.00"),
            ("22.5", "22.50"),
            (Decimal("129.99"), "129.99"),
            (Decimal("5"), "5.00"),
            ("193.325", "193.33"),  # ROUND_HALF_UP
            ("193.324", "193.32"),
        ],
    )
    def test_formats_two_decimals(self, value, expected):
        assert _money_filter()(value) == expected

    def test_unparseable_value_passes_through(self):
        # Never break a render on bad input.
        assert _money_filter()("not-a-number") == "not-a-number"
        assert _money_filter()(None) is None

    def test_filter_works_in_real_env(self):
        env = create_app().state.templates.env
        rendered = env.from_string("${{ v | money }}").render(v="193.3200")
        assert rendered == "$193.32"


# ─── spend bar end-to-end ───────────────────────────────────────────────────


class TestSpendBarRendering:
    def test_spend_bar_shows_two_decimals(self, client):
        # Default session auto-creates a mandate with daily_cap 1000 and
        # zero spend, so the bar renders deterministically. The first GET
        # only sets the session cookie (read from the *response*); the
        # second request carries it, so the spend bar actually renders.
        client.get("/")
        r = client.get("/")
        assert r.status_code == 200
        assert "daily Carto agentic limit" in r.text  # bar is present
        assert "$0.00" in r.text
        assert "$1000.00" in r.text
        # No over-precision / bare-integer dollar figures in the bar copy.
        assert "$1000 " not in r.text
        assert "$0 " not in r.text
        assert "0.0000" not in r.text

    def test_base_template_pipes_both_figures_through_money(self):
        from pathlib import Path
        from web.app import TEMPLATE_DIR

        src = Path(TEMPLATE_DIR, "base.html").read_text()
        assert "_mi.spent_today | money" in src
        assert "_mi.mandate.daily_cap | money" in src


# ─── restart confirmation dialog ────────────────────────────────────────────


class TestRestartConfirmDialog:
    def test_active_chat_renders_custom_dialog(self, client):
        client.get("/")
        _seed_conversation(client)
        r = client.get("/chat")
        assert r.status_code == 200
        assert 'id="restart-confirm"' in r.text
        assert "Restart conversation?" in r.text
        assert "Carto preserves your cart and order history." in r.text

    def test_no_native_confirm_used(self, client):
        client.get("/")
        _seed_conversation(client)
        r = client.get("/chat")
        # The browser-native confirm() (unbranded "127.0.0.1 says") is gone.
        assert "confirm(" not in r.text
        # And the old copy is gone.
        assert "Start a fresh conversation" not in r.text

    def test_reset_form_target_preserved(self, client):
        client.get("/")
        _seed_conversation(client)
        r = client.get("/chat")
        # OK submits this real form; the dialog only gates it.
        assert 'id="chat-reset-form"' in r.text
        assert 'action="/chat/reset"' in r.text
        assert "__openRestartConfirm" in r.text

    def test_reset_endpoint_still_works(self, client):
        client.get("/")
        _seed_conversation(client)
        # The dialog's OK does a native form POST → 303 redirect to /chat.
        r = client.post("/chat/reset", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/chat"

    def test_empty_chat_has_no_dialog(self, client):
        # With no messages the hero (empty) state shows; no Restart control
        # and therefore no dialog.
        client.get("/")
        r = client.get("/chat")
        assert 'id="restart-confirm"' not in r.text
