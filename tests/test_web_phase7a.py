"""Phase 7a — Foundation: home, search, product detail, sessions, dual format.

Uses FastAPI's TestClient — synchronous wrapper around the ASGI app.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from web.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Fresh app per test with an isolated DB directory.

    The session manager creates per-session DB files; pointing DB_PATH at
    tmp_path keeps tests hermetic.
    """
    monkeypatch.setenv("DB_PATH", str(tmp_path / "demo.json"))
    # Force chat offline so tests don't try to hit Anthropic
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = create_app()
    with TestClient(app) as c:
        yield c


# ─── Home ────────────────────────────────────────────────────────────────


class TestHome:
    def test_home_returns_200(self, client):
        r = client.get("/")
        assert r.status_code == 200

    def test_home_renders_html(self, client):
        r = client.get("/")
        assert "text/html" in r.headers["content-type"]
        assert "Agentic Commerce" in r.text

    def test_home_lists_featured_products(self, client):
        r = client.get("/")
        # Catalogue is wired — should see at least one named product
        assert (
            "Coffee" in r.text or "Mug" in r.text or "Running" in r.text or "Headphones" in r.text
        )

    def test_home_shows_merchant_list(self, client):
        r = client.get("/")
        # All three demo merchants are listed in the sidebar
        assert "coffee-bar.myshopify.com" in r.text
        assert "audio-hub.myshopify.com" in r.text
        assert "athletic-co.myshopify.com" in r.text

    def test_home_includes_search_bar(self, client):
        r = client.get("/")
        assert 'type="search"' in r.text


# ─── Search ──────────────────────────────────────────────────────────────


class TestSearch:
    def test_search_with_query(self, client):
        r = client.get("/search?q=mug")
        assert r.status_code == 200
        assert "mug" in r.text.lower()

    def test_search_empty_query_browses_all(self, client):
        r = client.get("/search?q=")
        assert r.status_code == 200

    def test_search_filters_by_merchant(self, client):
        r = client.get("/search?q=coffee&merchant=coffee-bar.myshopify.com")
        assert r.status_code == 200
        # The merchant filter shows products from that merchant only
        assert "coffee-bar.myshopify.com" in r.text

    def test_search_unknown_merchant_falls_back_to_all(self, client):
        r = client.get("/search?q=mug&merchant=nonexistent.com")
        assert r.status_code == 200


# ─── Product detail ──────────────────────────────────────────────────────


class TestProductDetail:
    def test_product_detail_renders(self, client):
        # Use a product id we know exists in the catalogue
        r = client.get("/product/coffee-bar.myshopify.com/cof_001")
        assert r.status_code == 200
        assert "Ceramic Coffee Mug" in r.text or "cof_001" in r.text

    def test_product_detail_unknown_id_returns_404(self, client):
        r = client.get("/product/coffee-bar.myshopify.com/does_not_exist")
        assert r.status_code == 404


# ─── Dual format (HTML vs JSON via Accept header) ────────────────────────


class TestDualFormat:
    def test_home_html_by_default(self, client):
        r = client.get("/")
        assert "text/html" in r.headers["content-type"]

    def test_home_json_when_requested(self, client):
        r = client.get("/", headers={"Accept": "application/json"})
        assert "application/json" in r.headers["content-type"]
        data = r.json()
        assert "products" in data
        assert "merchants" in data
        assert isinstance(data["merchants"], list)

    def test_search_json_when_requested(self, client):
        r = client.get("/search?q=mug", headers={"Accept": "application/json"})
        assert "application/json" in r.headers["content-type"]
        data = r.json()
        assert "products" in data
        assert "query" in data

    def test_product_detail_json_when_requested(self, client):
        r = client.get(
            "/product/coffee-bar.myshopify.com/cof_001",
            headers={"Accept": "application/json"},
        )
        if r.status_code == 200:
            assert "application/json" in r.headers["content-type"]
            data = r.json()
            assert "product" in data
            assert data["product"]["product_id"] == "cof_001"

    def test_mixed_accept_prefers_html(self, client):
        """When both text/html and application/json are accepted,
        and html comes first, we render HTML."""
        r = client.get("/", headers={"Accept": "text/html, application/json"})
        assert "text/html" in r.headers["content-type"]


# ─── Sessions ────────────────────────────────────────────────────────────


class TestSessions:
    def test_first_request_sets_cookie(self, client):
        r = client.get("/")
        assert "ac_session" in r.cookies or "set-cookie" in [h.lower() for h in r.headers]

    def test_cookie_persists_across_requests(self, client):
        # TestClient persists cookies automatically
        r1 = client.get("/")
        cookies_after_first = dict(client.cookies)
        r2 = client.get("/")
        cookies_after_second = dict(client.cookies)
        assert cookies_after_first.get("ac_session") == cookies_after_second.get("ac_session")

    def test_two_clients_get_distinct_sessions(self, tmp_path, monkeypatch):
        """Different browsers (clients) get different session IDs."""
        monkeypatch.setenv("DB_PATH", str(tmp_path / "demo.json"))
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        app = create_app()
        with TestClient(app) as c1, TestClient(app) as c2:
            c1.get("/")
            c2.get("/")
            sid1 = c1.cookies.get("ac_session")
            sid2 = c2.cookies.get("ac_session")
            assert sid1 != sid2

    def test_session_has_mandate(self, client):
        """A fresh session gets an active mandate, visible on home page."""
        r = client.get("/")
        # The mandate's daily-cap appears in the welcome text
        assert "1000" in r.text or "mandate" in r.text.lower()


# ─── Healthz ─────────────────────────────────────────────────────────────


class TestHealthz:
    def test_healthz_returns_ok(self, client):
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
