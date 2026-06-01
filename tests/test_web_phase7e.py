"""Phase 7e — account, mandate, orders, audit pages."""

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


def _sess(client):
    sid = session_mod._serializer.loads(client.cookies.get("ac_session"))
    return session_mod.get_session_by_id(sid)


# ─── Profile ─────────────────────────────────────────────────────────────


class TestProfile:
    def test_renders_html(self, client):
        r = client.get("/profile")
        assert r.status_code == 200
        assert "Profile" in r.text

    def test_redacts_payment_method(self, client):
        # The profile view goes through agent_safe_view which strips
        # payment_method_id. We assert the raw id is not in the response.
        client.get("/")
        sess = _sess(client)
        pm = sess.ctx.user.payment_method_id
        assert pm  # sanity
        r = client.get("/profile")
        assert pm not in r.text

    def test_json_response(self, client):
        r = client.get("/profile", headers={"Accept": "application/json"})
        assert "application/json" in r.headers["content-type"]
        data = r.json()
        assert "user" in data
        assert "payment_method_id" not in data["user"]


# ─── Mandate ─────────────────────────────────────────────────────────────


class TestMandate:
    def test_page_shows_caps(self, client):
        r = client.get("/mandate")
        assert r.status_code == 200
        # The seeded mandate's caps appear in the rendered text
        assert "500" in r.text  # per_transaction_cap
        assert "1000" in r.text  # daily_cap
        assert "5000" in r.text  # monthly_cap

    def test_json_shape(self, client):
        r = client.get("/mandate", headers={"Accept": "application/json"})
        data = r.json()
        assert data["mandate"]["status"] == "active"
        assert "per_transaction_cap" in data["mandate"]

    def test_revoke_flips_status(self, client):
        client.get("/")
        r = client.post("/mandate/revoke", headers={"Accept": "application/json"})
        assert r.status_code == 200
        assert r.json()["status"] == "revoked"
        # Subsequent GET reflects revoked status
        r2 = client.get("/mandate", headers={"Accept": "application/json"})
        assert r2.json()["mandate"]["status"] == "revoked"

    def test_revoke_redirects_html(self, client):
        client.get("/")
        r = client.post("/mandate/revoke", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/mandate"


# ─── Orders ──────────────────────────────────────────────────────────────


class TestOrders:
    def test_empty_orders_renders(self, client):
        r = client.get("/orders")
        assert r.status_code == 200
        assert "No orders yet" in r.text or "Orders" in r.text

    def test_unknown_order_returns_404(self, client):
        r = client.get("/orders/no_such_order")
        assert r.status_code == 404

    def test_orders_seeded_renders(self, client):
        client.get("/")  # establish session
        sess = _sess(client)
        # Seed an order directly into this session's DB
        sess.ctx.db.orders.insert(
            {
                "order_id": "ord_test_1",
                "merchant_domain": "coffee-bar.myshopify.com",
                "amount": "12.00",
                "status": "fulfilled",
                "created_at": "2026-05-15T00:00:00Z",
                "items": [{"name": "Mug", "quantity": 1, "line_total": "12.00"}],
            }
        )
        r = client.get("/orders")
        assert "ord_test_1" in r.text
        # Order detail also renders
        r2 = client.get("/orders/ord_test_1")
        assert r2.status_code == 200
        assert "Mug" in r2.text

    def test_initiate_return_from_button(self, client):
        client.get("/")
        sess = _sess(client)
        sess.ctx.db.orders.insert(
            {
                "order_id": "ord_ret_1",
                "merchant_domain": "coffee-bar.myshopify.com",
                "amount": "12.00",
                "status": "fulfilled",
                "items": [{"name": "Mug", "quantity": 1}],
                "created_at": "2026-05-15T00:00:00Z",
            }
        )
        r = client.post(
            "/orders/ord_ret_1/return",
            data={"reason": "changed_mind"},
            headers={"Accept": "application/json"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["accepted"] is True
        assert body["status"] == "submitted"


# ─── Audit ───────────────────────────────────────────────────────────────


class TestAudit:
    def test_audit_page_renders(self, client):
        r = client.get("/audit")
        assert r.status_code == 200
        assert "Audit log" in r.text

    def test_audit_shows_entries(self, client):
        client.get("/")
        sess = _sess(client)
        sess.ctx.db.audit_log.insert(
            {
                "agent": "TestAgent",
                "tool": "test_tool",
                "action": "ran something",
                "mandate_id": None,
                "args": {},
                "timestamp": "2026-05-15T12:00:00Z",
            }
        )
        r = client.get("/audit")
        assert "TestAgent" in r.text
        assert "test_tool" in r.text


# ─── Header links present ────────────────────────────────────────────────


class TestHeaderLinks:
    def test_header_has_account_links(self, client):
        r = client.get("/")
        for href in ("/profile", "/mandate", "/orders", "/audit"):
            assert href in r.text
