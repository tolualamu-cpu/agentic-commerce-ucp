"""Phase 9d — structured click SSE events and styled chat confirmations.

When a user adds/removes/updates a cart item, the SSE click event now carries
structured data (action, product_id, name, image_url) so the UI can render a
styled confirmation instead of grey backend noise.

Covers all three merchants and all action types (add, remove, update).
Sorts after test_user_journeys (w > u) — asyncio.run() is safe.
"""

from __future__ import annotations

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


def _drain_click_events(sess) -> list[dict]:
    events = []
    while not sess.sse_queue.empty():
        try:
            evt = sess.sse_queue.get_nowait()
            if evt.get("type") == "click":
                events.append(evt)
        except Exception:
            break
    return events


# ─── Structured click event fields ──────────────────────────────────────────


class TestAddToCartClickEventStructure:
    """All three merchants produce structured click events on add-to-cart."""

    def _get_click_event_for_add(self, client, merchant, product_id):
        client.get("/")
        # Post FIRST — this creates sse_queue lazily inside the background
        # thread's event loop (so the main thread doesn't need a running loop)
        client.post(f"/cart/add/{merchant}/{product_id}")
        # Now retrieve the session after the queue was created in the bg thread
        sess = _sess(client)
        return _drain_click_events(sess)

    def test_add_shoe_event_has_action_field(self, client):
        events = self._get_click_event_for_add(client, "athletic-co.myshopify.com", "ath_001")
        assert events, "Expected at least one click event after add-to-cart"
        assert events[0]["data"]["action"] == "added"

    def test_add_headphone_event_has_action_field(self, client):
        events = self._get_click_event_for_add(client, "audio-hub.myshopify.com", "aud_002")
        assert events
        assert events[0]["data"]["action"] == "added"

    def test_add_mug_event_has_action_field(self, client):
        events = self._get_click_event_for_add(client, "coffee-bar.myshopify.com", "cof_001")
        assert events
        assert events[0]["data"]["action"] == "added"

    def test_click_event_has_product_id(self, client):
        events = self._get_click_event_for_add(client, "athletic-co.myshopify.com", "ath_001")
        assert "product_id" in events[0]["data"]
        assert events[0]["data"]["product_id"] == "ath_001"

    def test_click_event_has_name(self, client):
        events = self._get_click_event_for_add(client, "athletic-co.myshopify.com", "ath_001")
        assert "name" in events[0]["data"]
        assert "Running Shoes" in events[0]["data"]["name"]

    def test_click_event_has_image_url(self, client):
        events = self._get_click_event_for_add(client, "athletic-co.myshopify.com", "ath_001")
        assert "image_url" in events[0]["data"]
        assert events[0]["data"]["image_url"].startswith("https://")

    def test_click_event_image_url_is_unsplash(self, client):
        events = self._get_click_event_for_add(client, "audio-hub.myshopify.com", "aud_002")
        assert "unsplash.com" in events[0]["data"]["image_url"]

    def test_click_event_has_merchant_domain(self, client):
        events = self._get_click_event_for_add(client, "coffee-bar.myshopify.com", "cof_001")
        assert events[0]["data"]["merchant_domain"] == "coffee-bar.myshopify.com"

    def test_click_event_has_quantity(self, client):
        events = self._get_click_event_for_add(client, "athletic-co.myshopify.com", "ath_001")
        assert "quantity" in events[0]["data"]
        assert events[0]["data"]["quantity"] >= 1

    def test_click_name_has_no_timestamp(self, client):
        """The name field must be a clean product name, no ISO timestamp."""
        events = self._get_click_event_for_add(client, "athletic-co.myshopify.com", "ath_001")
        name = events[0]["data"]["name"]
        assert "(at " not in name, "name must not contain a timestamp"
        assert "[via UI click]" not in name, "name must not contain backend prefix"

    def test_second_add_produces_updated_action(self, client):
        """Adding same product twice: first = 'added', second = 'updated'."""
        client.get("/")
        client.post("/cart/add/athletic-co.myshopify.com/ath_001")
        sess = _sess(client)
        # Drain first event (queue already created in bg thread)
        _drain_click_events(sess)
        client.post("/cart/add/athletic-co.myshopify.com/ath_001")
        events = _drain_click_events(sess)
        assert events
        assert events[0]["data"]["action"] == "updated"


# ─── Remove from cart click event ────────────────────────────────────────────


class TestRemoveFromCartClickEvent:
    def test_remove_shoe_event_action_is_removed(self, client):
        client.get("/")
        client.post("/cart/add/athletic-co.myshopify.com/ath_001")
        sess = _sess(client)
        _drain_click_events(sess)
        client.post("/cart/remove/athletic-co.myshopify.com/ath_001")
        events = _drain_click_events(sess)
        assert events
        assert events[0]["data"]["action"] == "removed"

    def test_remove_headphone_event_has_name(self, client):
        client.get("/")
        client.post("/cart/add/audio-hub.myshopify.com/aud_002")
        sess = _sess(client)
        _drain_click_events(sess)
        client.post("/cart/remove/audio-hub.myshopify.com/aud_002")
        events = _drain_click_events(sess)
        assert events
        assert "name" in events[0]["data"]

    def test_remove_event_has_image_url(self, client):
        client.get("/")
        client.post("/cart/add/coffee-bar.myshopify.com/cof_001")
        sess = _sess(client)
        _drain_click_events(sess)
        client.post("/cart/remove/coffee-bar.myshopify.com/cof_001")
        events = _drain_click_events(sess)
        assert events
        # image_url may be empty string (if not in stored item) but key must exist
        assert "image_url" in events[0]["data"]


# ─── Update quantity click event ─────────────────────────────────────────────


class TestUpdateQuantityClickEvent:
    def test_update_quantity_produces_updated_action(self, client):
        client.get("/")
        client.post("/cart/add/athletic-co.myshopify.com/ath_001")
        sess = _sess(client)
        _drain_click_events(sess)
        client.post("/cart/quantity/athletic-co.myshopify.com/ath_001", data={"quantity": 3})
        events = _drain_click_events(sess)
        assert events
        assert events[0]["data"]["action"] == "updated"

    def test_quantity_zero_produces_removed_action(self, client):
        client.get("/")
        client.post("/cart/add/audio-hub.myshopify.com/aud_002")
        sess = _sess(client)
        _drain_click_events(sess)
        client.post("/cart/quantity/audio-hub.myshopify.com/aud_002", data={"quantity": 0})
        events = _drain_click_events(sess)
        assert events
        assert events[0]["data"]["action"] == "removed"


# ─── Server-rendered history: no grey arrows ─────────────────────────────────


class TestClickConfirmationRendering:
    def _add_click_note(self, sess, text):
        sess.ctx.session.conversation.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"[via UI click] {text} (at 2026-01-01T00:00:00+00:00)",
                    }
                ],
            }
        )

    def test_no_grey_arrow_for_add_shoe(self, client):
        client.get("/")
        self._add_click_note(_sess(client), "added Demo Running Shoes × 1")
        r = client.get("/chat")
        assert "↳ [via UI click]" not in r.text

    def test_no_grey_arrow_for_add_headphone(self, client):
        client.get("/")
        self._add_click_note(_sess(client), "added Noise-Cancelling Headphones × 1")
        r = client.get("/chat")
        assert "↳ [via UI click]" not in r.text

    def test_no_grey_arrow_for_remove_mug(self, client):
        client.get("/")
        self._add_click_note(_sess(client), "removed Ceramic Coffee Mug")
        r = client.get("/chat")
        assert "↳ [via UI click]" not in r.text

    def test_styled_div_present_for_add_action(self, client):
        """Click note should render as a styled confirmation div (bg-slate-50)."""
        client.get("/")
        self._add_click_note(_sess(client), "added Demo Running Shoes × 1")
        r = client.get("/chat")
        assert "bg-slate-50" in r.text, (
            "Styled cart confirmation (bg-slate-50) must appear in rendered HTML"
        )

    def test_action_text_capitalised(self, client):
        """The action text must be capitalised in the styled div (not grey italic)."""
        client.get("/")
        self._add_click_note(_sess(client), "added Premium Running Shoes × 2")
        r = client.get("/chat")
        assert r.status_code == 200
        assert "bg-slate-50" in r.text

    def test_timestamp_not_shown_in_visible_html(self, client):
        """The styled confirmation must be present and the old arrow rendering gone.
        Note: the timestamp may appear in the JS _serverHistory dedup variable
        but must not appear as part of a visible rendered element."""
        client.get("/")
        self._add_click_note(_sess(client), "added Demo Running Shoes × 1")
        r = client.get("/chat")
        # The styled confirmation (bg-slate-50) must be rendered
        assert "bg-slate-50" in r.text, "New styled cart confirmation (bg-slate-50) must be present"
        # The old ↳ arrow rendering must be gone
        assert "↳ [via UI click]" not in r.text, (
            "Old ↳ [via UI click] arrow must not appear in rendered HTML"
        )

    def test_via_ui_click_prefix_not_in_rendered_html_element(self, client):
        """[via UI click] prefix must not appear in HTML element content.
        The prefix may appear in the JS _serverHistory variable (dedup logic)
        but must NOT appear in a visible HTML tag like <div> or <span>."""
        client.get("/")
        self._add_click_note(_sess(client), "added Demo Running Shoes × 1")
        r = client.get("/chat")
        # Check the old grey italic div is gone — that's where [via UI click] showed
        assert "↳ [via UI click]" not in r.text, (
            "The ↳ [via UI click] arrow rendering must not appear in the HTML"
        )
