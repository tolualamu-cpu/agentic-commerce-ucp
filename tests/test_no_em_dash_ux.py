"""Tests: no em-dashes (or en-dashes) ever reach the UX.

The product owner's rule: em-dashes (—) and en-dashes (–) must never appear
in any user-visible surface — pages, empty states, modals/pop-ups, button
labels, placeholders. Code comments (Jinja {# #}, JS //, CSS) are
developer-facing and out of scope, so we strip <script>/<style> blocks and
HTML comments before scanning.

This file is the standing guard: any new template copy that introduces a
long dash will fail here. It walks every GET surface across all three
merchants (product detail) plus populated cart/order states so dynamically
rendered copy is covered too.

Sorts BEFORE test_user_journeys.py (n < u): synchronous TestClient only,
no asyncio.run() — the event loop is never created or closed here.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from web import session as session_mod
from web.app import create_app

EM_DASH = "—"  # —
EN_DASH = "–"  # –

# Strip developer-facing / non-visible regions before scanning.
_SCRIPT = re.compile(r"<script\b[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
_STYLE = re.compile(r"<style\b[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _visible(html: str) -> str:
    html = _SCRIPT.sub("", html)
    html = _STYLE.sub("", html)
    html = _HTML_COMMENT.sub("", html)
    return html


def _assert_no_dashes(html: str, where: str):
    visible = _visible(html)
    assert EM_DASH not in visible, f"em-dash (—) found in visible UX: {where}"
    assert EN_DASH not in visible, f"en-dash (–) found in visible UX: {where}"


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


def _add(client, merchant, product_id, qty=1):
    return client.post(f"/cart/add/{merchant}/{product_id}", data={"quantity": qty})


def _seed_order(sess, order_id, merchant, items, amount="42.00", status="confirmed"):
    row = {
        "order_id": order_id,
        "merchant_domain": merchant,
        "amount": amount,
        "status": status,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "items": items,
        "currency": "USD",
        "mandate_id": "m_test",
        "payment_intent_id": None,
        "tracking_number": None,
        "estimated_delivery": None,
    }
    sess.ctx.db.orders.insert(row)
    return row


ATH = "athletic-co.myshopify.com"
AUD = "audio-hub.myshopify.com"
COF = "coffee-bar.myshopify.com"

STATIC_GET_PAGES = [
    "/",
    "/chat",
    "/cart",
    "/orders",
    "/profile",
    "/mandate",
    "/audit",
    "/search",
]


# ─── Static surfaces ────────────────────────────────────────────────────────


class TestStaticPagesNoDashes:
    @pytest.mark.parametrize("path", STATIC_GET_PAGES)
    def test_page_visible_text_has_no_dashes(self, client, path):
        client.get("/")
        r = client.get(path)
        assert r.status_code == 200
        _assert_no_dashes(r.text, path)

    @pytest.mark.parametrize(
        "merchant,pid",
        [
            (ATH, "ath_001"),
            (AUD, "aud_001"),
            (COF, "cof_001"),
        ],
    )
    def test_product_detail_no_dashes(self, client, merchant, pid):
        client.get("/")
        r = client.get(f"/product/{merchant}/{pid}")
        assert r.status_code == 200
        _assert_no_dashes(r.text, f"/product/{merchant}/{pid}")

    def test_search_results_no_dashes(self, client):
        client.get("/")
        r = client.get("/search?q=coffee")
        assert r.status_code == 200
        _assert_no_dashes(r.text, "/search?q=coffee")


# ─── Populated / stateful surfaces ──────────────────────────────────────────


class TestPopulatedSurfacesNoDashes:
    def test_cart_with_cross_merchant_items_no_dashes(self, client):
        client.get("/")
        _add(client, ATH, "ath_001", 1)
        _add(client, AUD, "aud_001", 1)
        _add(client, COF, "cof_001", 2)
        r = client.get("/cart")
        _assert_no_dashes(r.text, "/cart (populated)")

    def test_cart_drawer_fragment_no_dashes(self, client):
        client.get("/")
        _add(client, COF, "cof_001", 1)
        # HX-Request → bare _cart_drawer.html partial (the empty + populated
        # copy both live here).
        r = client.get("/cart", headers={"HX-Request": "true"})
        _assert_no_dashes(r.text, "_cart_drawer partial")

    def test_empty_cart_drawer_fragment_no_dashes(self, client):
        client.get("/")
        r = client.get("/cart", headers={"HX-Request": "true"})
        _assert_no_dashes(r.text, "_cart_drawer partial (empty)")

    @pytest.mark.parametrize(
        "merchant,pid,name",
        [
            (ATH, "ath_006", "Premium Running Shoes"),
            (AUD, "aud_002", "Noise-Cancelling Headphones"),
            (COF, "cof_003", "Single-Origin Coffee Beans"),
        ],
    )
    def test_order_detail_no_dashes(self, client, merchant, pid, name):
        client.get("/")
        sess = _sess(client)
        item = {
            "product_id": pid,
            "name": name,
            "price": "42.00",
            "quantity": 1,
            "merchant_domain": merchant,
            "attributes": {},
            "item_id": None,
        }
        _seed_order(sess, f"ord_{pid}", merchant, [item])
        r = client.get(f"/orders/{pid and 'ord_' + pid}")
        assert r.status_code == 200
        _assert_no_dashes(r.text, f"order detail {merchant}")


# ─── Gate modal copy (the pop-up) ───────────────────────────────────────────


class TestGateModalCopy:
    def test_gate_placeholder_uses_new_copy(self, client):
        # The gate modal is included via base.html on every page.
        r = client.get("/")
        assert "Confirm order or ask a question" in r.text

    def test_old_gate_placeholder_removed(self, client):
        r = client.get("/")
        assert "Ask a question or 'remove 1'" not in r.text
        assert "remove 1'" not in r.text
