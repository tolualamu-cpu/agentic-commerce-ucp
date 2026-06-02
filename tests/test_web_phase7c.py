"""Phase 7c — Click-to-add, remove, quantity, clear, draft basket.

Click handlers maintain ``WebSession.click_basket`` and append a synthetic
``[via UI click]`` note to ``ctx.session.conversation`` so the orchestrator
sees the action on its next turn. The orchestrator's gate flow remains the
sole path to a real purchase.
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


def _get_session(client) -> "session_mod.WebSession":
    raw = client.cookies.get("ac_session")
    sid = session_mod._serializer.loads(raw)
    sess = session_mod.get_session_by_id(sid)
    assert sess is not None
    return sess


class TestAddToCart:
    def test_add_known_product_succeeds(self, client):
        client.get("/")  # establish session
        r = client.post("/cart/add/coffee-bar.myshopify.com/cof_001")
        assert r.status_code == 200
        sess = _get_session(client)
        items = sess.click_basket.get("coffee-bar.myshopify.com", [])
        assert len(items) == 1
        assert items[0]["product_id"] == "cof_001"
        assert items[0]["quantity"] == 1

    def test_add_unknown_product_returns_404(self, client):
        client.get("/")
        r = client.post("/cart/add/coffee-bar.myshopify.com/no_such_id")
        assert r.status_code == 404

    def test_add_same_product_twice_bumps_quantity(self, client):
        client.get("/")
        client.post("/cart/add/coffee-bar.myshopify.com/cof_001")
        client.post("/cart/add/coffee-bar.myshopify.com/cof_001")
        sess = _get_session(client)
        items = sess.click_basket["coffee-bar.myshopify.com"]
        assert len(items) == 1
        assert items[0]["quantity"] == 2

    def test_add_appends_synthetic_user_note(self, client):
        client.get("/")
        # Conversation may be empty initially
        client.post("/cart/add/coffee-bar.myshopify.com/cof_001")
        sess = _get_session(client)
        last = sess.ctx.session.conversation[-1]
        assert last["role"] == "user"
        text = last["content"][0]["text"]
        assert "[via UI click]" in text
        assert "added" in text.lower()

    def test_add_rejects_negative_quantity(self, client):
        client.get("/")
        r = client.post(
            "/cart/add/coffee-bar.myshopify.com/cof_001",
            data={"quantity": 0},
        )
        assert r.status_code == 400


class TestRemoveFromCart:
    def test_remove_existing_item(self, client):
        client.get("/")
        client.post("/cart/add/coffee-bar.myshopify.com/cof_001")
        r = client.post("/cart/remove/coffee-bar.myshopify.com/cof_001")
        assert r.status_code == 200
        sess = _get_session(client)
        assert sess.click_basket.get("coffee-bar.myshopify.com", []) == []

    def test_remove_missing_item_is_silent(self, client):
        client.get("/")
        r = client.post("/cart/remove/coffee-bar.myshopify.com/cof_001")
        assert r.status_code == 200  # no 404 — silent no-op


class TestChangeQuantity:
    def test_change_to_positive_updates_line_total(self, client):
        client.get("/")
        client.post("/cart/add/coffee-bar.myshopify.com/cof_001")
        r = client.post(
            "/cart/quantity/coffee-bar.myshopify.com/cof_001",
            data={"quantity": 3},
        )
        assert r.status_code == 200
        sess = _get_session(client)
        item = sess.click_basket["coffee-bar.myshopify.com"][0]
        assert item["quantity"] == 3
        # line_total = price * 3
        from decimal import Decimal

        assert Decimal(item["line_total"]) == Decimal(item["price"]) * 3

    def test_change_to_zero_removes_item(self, client):
        client.get("/")
        client.post("/cart/add/coffee-bar.myshopify.com/cof_001")
        r = client.post(
            "/cart/quantity/coffee-bar.myshopify.com/cof_001",
            data={"quantity": 0},
        )
        assert r.status_code == 200
        sess = _get_session(client)
        assert sess.click_basket.get("coffee-bar.myshopify.com", []) == []

    def test_change_quantity_unknown_product_404(self, client):
        client.get("/")
        r = client.post(
            "/cart/quantity/coffee-bar.myshopify.com/cof_001",
            data={"quantity": 2},
        )
        assert r.status_code == 404


class TestClearCart:
    def test_clear_empties_basket(self, client):
        client.get("/")
        client.post("/cart/add/coffee-bar.myshopify.com/cof_001")
        r = client.post("/cart/clear")
        assert r.status_code == 200
        sess = _get_session(client)
        assert sess.click_basket == {}


class TestViewCart:
    def test_view_renders_drawer(self, client):
        client.get("/")
        client.post("/cart/add/coffee-bar.myshopify.com/cof_001")
        r = client.get("/cart")
        assert r.status_code == 200
        assert "cart-drawer" in r.text
        assert "Ceramic Coffee Mug" in r.text or "cof_001" in r.text

    def test_view_json_returns_summary(self, client):
        client.get("/")
        client.post("/cart/add/coffee-bar.myshopify.com/cof_001")
        r = client.get("/cart", headers={"Accept": "application/json"})
        assert r.status_code == 200
        data = r.json()
        assert "lines" in data
        assert "subtotal" in data
        assert data["item_count"] == 1


class TestQuantityControlLiveUpdate:
    """Regression: on the /cart page, increasing an item's count must update
    the displayed quantity and totals.

    The drawer quantity control previously only re-posted on an explicit ↻
    click, and the /cart page header carried a *duplicate* item-count +
    subtotal summary OUTSIDE the swapped ``#cart-drawer`` — so it went stale
    after a quantity change. The fix: the quantity form auto-submits on
    ``change`` (typing/blur, spinner arrows, or the −/+ steppers), and the
    redundant outer summary was removed so the only count/subtotal shown
    live inside the drawer and always refresh on swap.
    """

    def _drawer_html(self, client):
        client.get("/")
        client.post("/cart/add/coffee-bar.myshopify.com/cof_001")
        return client.get("/cart").text

    def test_quantity_form_auto_submits_on_change(self, client):
        html = self._drawer_html(client)
        # The quantity form must fire on change (not only on the ↻ click) so
        # editing the number immediately re-renders quantity + totals.
        assert 'hx-trigger="submit, change"' in html

    def test_quantity_form_targets_drawer_with_outerhtml(self, client):
        html = self._drawer_html(client)
        assert 'hx-target="#cart-drawer"' in html
        assert 'hx-swap="outerHTML"' in html

    def test_stepper_buttons_present(self, client):
        html = self._drawer_html(client)
        assert 'aria-label="Increase quantity"' in html
        assert 'aria-label="Decrease quantity"' in html

    def test_no_stale_duplicate_summary_outside_drawer(self, client):
        """The cart page header must NOT carry its own count/subtotal line —
        that markup lived outside #cart-drawer and went stale on swap."""
        html = self._drawer_html(client)
        head, _, drawer = html.partition('id="cart-drawer"')
        # The page <h1> title lives before the drawer; the stale "· subtotal"
        # summary must not appear in that pre-drawer region.
        assert "Your cart" in head
        assert "subtotal" not in head.lower()

    def test_drawer_carries_live_count_and_subtotal(self, client):
        """Count + subtotal still render — inside the drawer, where they
        refresh together with the line items on every swap."""
        client.get("/")
        client.post("/cart/add/coffee-bar.myshopify.com/cof_001")
        # Bump to qty 3 via the same endpoint the auto-submit fires.
        r = client.post(
            "/cart/quantity/coffee-bar.myshopify.com/cof_001",
            data={"quantity": "3"},
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 200
        # Fresh drawer fragment must show the updated qty + totals.
        assert "× 3" in r.text or "&#215; 3" in r.text
        assert "42.00" in r.text  # 14.00 * 3 line_total + subtotal


class TestSessionIsolation:
    def test_two_clients_have_independent_carts(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DB_PATH", str(tmp_path / "demo.json"))
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        app = create_app()
        with TestClient(app) as c1, TestClient(app) as c2:
            c1.get("/")
            c2.get("/")
            c1.post("/cart/add/coffee-bar.myshopify.com/cof_001")
            r1 = c1.get("/cart", headers={"Accept": "application/json"})
            r2 = c2.get("/cart", headers={"Accept": "application/json"})
            assert r1.json()["item_count"] == 1
            assert r2.json()["item_count"] == 0
