"""Regressions for two bugs that kept coming back AND were not caught by the
earlier suite (the user's complaint: "if it passes, you are testing the wrong
things").

BUG A — "my own chat message doesn't show until I flip to another page and
back." After the empty→active transition went IN PLACE (no reload), the user
bubble was drawn only from the SSE ``user`` echo. That echo travels through a
single-consumer ``asyncio.Queue`` and the connection-takeover machinery, so a
timing slip silently dropped it; the message reappeared only when navigating
re-rendered it from server history. THE FIX: the user bubble is rendered
OPTIMISTICALLY on ``htmx:beforeRequest`` (before the POST, independent of any
server round-trip), and the SSE ``user`` echo is SWALLOWED so it can never
duplicate or be relied upon.

BUG B — "Add to cart shows a duplicate notification and the badge says 2 for a
1-item cart until I open the cart page." Two causes: (1) the chat ``click``
branch popped a toast IN ADDITION to the in-log confirmation bubble (the
"duplicate notification"); (2) the badge was bumped RELATIVELY (drift-prone),
so it could read 2 while the cart truly held 1 until a navigation forced a
resync. THE FIX: the ``click`` branch no longer toasts (single in-log
confirmation), and the cart toggle sets the badge to the server's ABSOLUTE
``item_count`` from the /cart response — true by construction, never drifting.

These tests pin BOTH the client wiring (the rendered ``_chat_sse.html``) and
the server contract (/cart/add returns an authoritative absolute ``item_count``)
across all three demo merchants.

Sorts alphabetically BEFORE ``test_user_journeys.py`` (``c`` < ``u``), so per
CLAUDE.md this file must NOT use ``asyncio.run()``. It uses none — the
TestClient drives the app synchronously.
"""

from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from web.app import create_app

# The chat SSE wiring source. We assert against the template SOURCE for
# client-wiring regressions so the relative-bump helper that legitimately
# lives in _toast.html (window.__bumpCart's definition) can't mask a
# regression in THIS file's cart toggle.
_CHAT_SSE_SRC = (
    pathlib.Path(__file__).resolve().parent.parent / "web" / "templates" / "_chat_sse.html"
).read_text()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "demo.json"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = create_app()
    with TestClient(app) as c:
        yield c


# ─── BUG A — client wiring: optimistic user bubble, swallowed SSE echo ────────


class TestUserBubbleRenderedOptimistically:
    def test_empty_chat_wires_optimistic_user_bubble(self, client):
        """The empty hero must draw the user bubble the instant they submit —
        on htmx:beforeRequest, BEFORE the POST — independent of the SSE echo."""
        html = client.get("/chat").text
        assert 'form.addEventListener("htmx:beforeRequest"' in html, (
            "the user bubble must be drawn optimistically on submit, not from "
            "the fragile SSE echo (BUG A)"
        )
        # The optimistic handler reads the typed text and appends a user bubble,
        # and reveals the in-place active log so the bubble has a visible home.
        assert 'append("user", text, { force: true })' in html
        assert "window.__chatRevealActive" in html

    def test_sse_user_echo_is_swallowed_not_rendered(self, client):
        """The SSE ``user`` echo must NEVER draw a bubble — it is a guaranteed
        duplicate of the optimistic (or server-rendered) bubble. If the
        dispatcher re-appended from ``d.text`` we'd be back to relying on the
        echo's timing, the exact bug."""
        html = client.get("/chat").text
        assert "SWALLOW the server's user echo" in html, (
            "the dispatcher must explicitly swallow the user echo"
        )
        # The dispatcher must NOT append a user bubble from the echo payload.
        assert 'append("user", d.text' not in html, (
            "SSE user echo must not render a bubble (BUG A regression guard)"
        )

    def test_active_chat_also_wires_optimistic_bubble(self, client):
        """An already-active conversation streams new turns too, so the
        optimistic handler must be present there as well."""
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
        html = client.get("/chat").text
        assert 'form.addEventListener("htmx:beforeRequest"' in html
        assert 'append("user", text, { force: true })' in html


# ─── BUG B — client wiring: absolute badge, single notification ───────────────


class TestCartBadgeAbsoluteAndSingleNotification:
    def test_card_toggle_uses_absolute_item_count(self, client):
        """The chat card cart toggle must set the badge to the server's
        ABSOLUTE item_count, never bump it relatively (which drifted to 2)."""
        assert "window.__setCartBadge(data.item_count)" in _CHAT_SSE_SRC, (
            "cart toggle must use the authoritative absolute count"
        )
        # The chat toggle must not CALL the relative bumper. (Its definition
        # legitimately lives in _toast.html; we assert against this template's
        # source so that definition can't mask a regression here.)
        assert "__bumpCart(" not in _CHAT_SSE_SRC, (
            "relative badge bumping drifts (badge=2 for a 1-item cart) — BUG B regression guard"
        )

    def test_click_branch_emits_no_duplicate_toast(self, client):
        """The chat ``click`` branch must show ONLY the in-log confirmation
        bubble — not also a toast (the "duplicate notification")."""
        assert 'window.__toast(label + ": " + name' not in _CHAT_SSE_SRC, (
            "the click branch must not also pop a toast (duplicate notification)"
        )
        # The single source of feedback — the in-log bubble — is still built.
        assert '"Added to cart"' in _CHAT_SSE_SRC


# ─── BUG B — server contract: /cart/add returns an absolute item_count ────────


MERCHANT_PRODUCTS = [
    pytest.param("athletic-co.myshopify.com", "ath_001", "ath_003", id="athletic-co"),
    pytest.param("audio-hub.myshopify.com", "aud_001", "aud_003", id="audio-hub"),
    pytest.param("coffee-bar.myshopify.com", "cof_001", "cof_003", id="coffee-bar"),
]


class TestCartAddReturnsAbsoluteItemCount:
    @pytest.mark.parametrize("merchant,pid_a,pid_b", MERCHANT_PRODUCTS)
    def test_single_add_reports_one(self, client, merchant, pid_a, pid_b):
        """A single add must report an absolute item_count of exactly 1 — the
        number the badge will display. (Never 2 for one click.)"""
        client.get("/")
        r = client.post(
            f"/cart/add/{merchant}/{pid_a}",
            headers={"Accept": "application/json"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["item_count"] == 1, f"single add must report 1, got {body['item_count']}"

    @pytest.mark.parametrize("merchant,pid_a,pid_b", MERCHANT_PRODUCTS)
    def test_readd_same_product_reports_truthful_total(self, client, merchant, pid_a, pid_b):
        """Re-adding the same product bumps quantity to 2 — and item_count
        must TRUTHFULLY report 2 units (the badge equals the cart's reality),
        not a drifted client-side guess."""
        client.get("/")
        client.post(f"/cart/add/{merchant}/{pid_a}", headers={"Accept": "application/json"})
        r = client.post(f"/cart/add/{merchant}/{pid_a}", headers={"Accept": "application/json"})
        assert r.json()["item_count"] == 2

    @pytest.mark.parametrize("merchant,pid_a,pid_b", MERCHANT_PRODUCTS)
    def test_two_distinct_products_report_two(self, client, merchant, pid_a, pid_b):
        """Two distinct products → item_count 2 (absolute, from the server)."""
        client.get("/")
        client.post(f"/cart/add/{merchant}/{pid_a}", headers={"Accept": "application/json"})
        r = client.post(f"/cart/add/{merchant}/{pid_b}", headers={"Accept": "application/json"})
        assert r.json()["item_count"] == 2

    @pytest.mark.parametrize("merchant,pid_a,pid_b", MERCHANT_PRODUCTS)
    def test_remove_reports_decremented_absolute_count(self, client, merchant, pid_a, pid_b):
        """Removing a line drops the absolute count back — the badge can never
        be left stale at the higher value."""
        client.get("/")
        client.post(f"/cart/add/{merchant}/{pid_a}", headers={"Accept": "application/json"})
        client.post(f"/cart/add/{merchant}/{pid_b}", headers={"Accept": "application/json"})
        r = client.post(f"/cart/remove/{merchant}/{pid_a}", headers={"Accept": "application/json"})
        assert r.json()["item_count"] == 1

    def test_cross_merchant_basket_counts_all_units(self, client):
        """A cross-merchant basket's item_count sums units across all stores —
        the badge reflects the true global total (project testing rule:
        exercise cross-merchant baskets, not one category)."""
        client.get("/")
        client.post(
            "/cart/add/athletic-co.myshopify.com/ath_001",
            headers={"Accept": "application/json"},
        )
        client.post(
            "/cart/add/audio-hub.myshopify.com/aud_001",
            headers={"Accept": "application/json"},
        )
        r = client.post(
            "/cart/add/coffee-bar.myshopify.com/cof_001",
            headers={"Accept": "application/json"},
        )
        assert r.json()["item_count"] == 3
