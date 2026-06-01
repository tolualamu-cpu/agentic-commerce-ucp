"""Phase 8c — Reset chat + sticky-bottom input.

Tests the new POST /chat/reset endpoint and the reset-button render
contract. Scroll behaviour is JS-only and not unit-testable here;
covered by the manual smoke checklist in the Phase 8c plan.
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


def _seed_conversation(sess) -> None:
    sess.ctx.session.conversation.extend(
        [
            {
                "role": "user",
                "content": [{"type": "text", "text": "find me coffee mugs"}],
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Here are three options."}],
            },
        ]
    )


class TestResetClearsConversation:
    def test_reset_empties_conversation(self, client):
        client.get("/")  # establish session
        sess = _sess(client)
        _seed_conversation(sess)
        assert len(sess.ctx.session.conversation) == 2

        r = client.post("/chat/reset", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/chat"

        assert sess.ctx.session.conversation == []


class TestResetClearsDiscoveryCacheAndOpenSessions:
    def test_reset_clears_secondary_session_state(self, client):
        client.get("/")
        sess = _sess(client)
        sess.ctx.session.last_discovered_products.extend(
            [{"product_id": "ath_001", "name": "Mug", "price": "12"}]
        )
        sess.ctx.session.open_checkout_sessions["athletic-co.myshopify.com"] = "cart_abc123"

        client.post("/chat/reset", follow_redirects=False)

        assert sess.ctx.session.last_discovered_products == []
        assert sess.ctx.session.open_checkout_sessions == {}


class TestResetPreservesLongLivedState:
    def test_reset_keeps_click_basket_mandate_and_audit(self, client):
        client.get("/")
        sess = _sess(client)

        # Seed click_basket via the existing add-to-cart route
        r1 = client.post("/cart/add/coffee-bar.myshopify.com/cof_001")
        assert r1.status_code == 200
        cart_before = dict(sess.click_basket)
        assert cart_before, "expected click_basket to have items before reset"

        # Capture mandate + prior audit entries
        mandate_before = sess.mandate_id
        audit_before = len(sess.ctx.db.audit_log.all())

        # Seed conversation so reset has something to clear
        _seed_conversation(sess)

        client.post("/chat/reset", follow_redirects=False)

        # click_basket survives
        assert sess.click_basket == cart_before, "click_basket should not be cleared by /chat/reset"

        # Mandate is unchanged and still active
        assert sess.mandate_id == mandate_before
        m = sess.ctx.ap2.get_mandate(sess.mandate_id)
        assert m is not None
        from datetime import datetime, timezone

        assert m.is_active(datetime.now(timezone.utc)).value == "active"

        # Audit log keeps prior entries AND gains a reset_chat entry
        audit_after = sess.ctx.db.audit_log.all()
        assert len(audit_after) > audit_before, "reset should append an audit entry"
        reset_entries = [r for r in audit_after if r.get("tool") == "reset_chat"]
        assert len(reset_entries) == 1
        assert reset_entries[0]["agent"] == "WebUI"


class TestResetRedirect:
    def test_reset_redirects_to_chat(self, client):
        client.get("/")
        r = client.post("/chat/reset", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/chat"

    def test_reset_followed_renders_empty_chat(self, client):
        client.get("/")
        sess = _sess(client)
        _seed_conversation(sess)

        r = client.post("/chat/reset")  # follow_redirects=True (default)
        assert r.status_code == 200
        # Empty-state markers: hero headline + suggestion chips,
        # no rendered bubble text
        assert "What are you shopping for?" in r.text
        assert "find me coffee mugs" not in r.text


class TestResetButtonVisibility:
    def test_reset_button_visible_when_conversation_exists(self, client):
        client.get("/")
        sess = _sess(client)
        _seed_conversation(sess)

        r = client.get("/chat")
        assert r.status_code == 200
        assert 'action="/chat/reset"' in r.text
        assert 'aria-label="Restart conversation"' in r.text

    def test_reset_button_absent_when_chat_empty(self, client):
        r = client.get("/chat")
        assert r.status_code == 200
        # Empty state: no reset button — nothing to reset
        assert 'action="/chat/reset"' not in r.text
