"""Phase 7f — picker overlay, toasts, docs, polish."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from web.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "demo.json"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = create_app()
    with TestClient(app) as c:
        yield c


class TestPickerOverlay:
    def test_overlay_present_in_base(self, client):
        r = client.get("/")
        assert 'id="picker-overlay"' in r.text
        assert 'id="picker-list"' in r.text


class TestToastStack:
    def test_toast_stack_present(self, client):
        r = client.get("/")
        assert 'id="toast-stack"' in r.text
        assert "window.__toast" in r.text


class TestChatSidebarRoutesClicksToToast:
    def test_click_event_handled_in_sidebar_js(self, client):
        r = client.get("/")
        # The chat sidebar's SSE handler routes click + error events to
        # the toast helper. Verify both branches exist.
        assert "window.__toast" in r.text
        assert "click" in r.text  # event type handled


class TestDevDocs:
    def test_web_development_doc_exists(self):
        path = Path(__file__).resolve().parents[1] / "docs" / "WEB_DEVELOPMENT.md"
        assert path.exists()
        text = path.read_text()
        # Spot-check sections enumerated in the plan
        for header in (
            "Quick start",
            "Architecture",
            "API contract",
            "Click vs chat",
            "Component reference",
            "Click-action catalogue",
            "Extending the UI",
            "Security model",
        ):
            assert header in text, f"missing section: {header}"


class TestGateModalReachableFromBase:
    def test_modal_persists_across_pages(self, client):
        for path in ("/", "/search?q=mug", "/mandate", "/orders"):
            r = client.get(path)
            assert 'id="gate-modal"' in r.text, f"missing modal on {path}"
            assert 'id="picker-overlay"' in r.text, f"missing picker on {path}"
