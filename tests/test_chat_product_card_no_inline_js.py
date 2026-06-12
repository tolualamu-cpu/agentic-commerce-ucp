"""Pins the regression where inline `onmouseenter`/`onmouseleave` JS handlers
in `_chat_product_card.html` caused the live chat fragment injection to fail
silently (the bug where the agent claimed it showed cards but none rendered).

Live injection path:
    SSE `products` event → fetch POST /chat/products-fragment → returned HTML
    is injected via `placeholder.innerHTML = html` in _chat_sse.html. Any
    inline JS in that HTML is a fragility class — CSP, browser extensions,
    parser edge cases, nested-quote bugs. CSS classes are 100% injection-safe.

This file asserts the rendered fragment HTML contains ZERO inline event
handlers and includes the expected CSS class for hover. If anyone re-introduces
inline JS in a chat fragment template, these tests fail immediately.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from web import session as session_mod  # noqa: F401  (used by client fixture)
from web.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "demo.json"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = create_app()
    with TestClient(app) as c:
        yield c


# Real-merchant-shaped product with external `url` (the path that previously
# rendered inline JS handlers). Demo merchants have url=None so they never
# triggered the inline-JS code path — the bug was Kith-only and untested.
_PRODUCT_WITH_URL = {
    "product_id": "8286509301888",
    "name": "Test Live Merchant Shirt",
    "description": "Test description",
    "price": "75.00",
    "currency": "USD",
    "merchant": "Kith",
    "merchant_domain": "kith.com",
    "rating": None,
    "review_count": None,
    "in_stock": True,
    "images": [
        "https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=800&q=80",
    ],
    "attributes": {"category": "shirt"},
    "source_protocol": "shopify_storefront",
    "confidence_score": 1.0,
    "shipping_estimate": None,
    "shipping_cost": None,
    "url": "https://kith.com/products/test-shirt",
}


class TestChatFragmentNoInlineJs:
    def test_fragment_has_no_onmouseenter(self, client):
        """The bug: `onmouseenter="this.style.background='#2563eb'"` in chat
        fragment HTML was suspect — under innerHTML injection in some browser
        configs it produced cards that never appeared. CSS :hover is safer."""
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_PRODUCT_WITH_URL]})
        assert r.status_code == 200
        assert "onmouseenter" not in r.text, (
            "Chat fragment must NOT contain inline onmouseenter handlers — "
            "use class='ucp-buy-badge' (CSS :hover) instead."
        )

    def test_fragment_has_no_onmouseleave(self, client):
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_PRODUCT_WITH_URL]})
        assert "onmouseleave" not in r.text, (
            "Chat fragment must NOT contain inline onmouseleave handlers."
        )

    def test_fragment_uses_ucp_buy_badge_class(self, client):
        """The replacement: the Buy-on link uses class='ucp-buy-badge'
        defined once in base.html. CSS :hover handles the colour change."""
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_PRODUCT_WITH_URL]})
        assert "ucp-buy-badge" in r.text, (
            "Buy-on link must carry class='ucp-buy-badge' so hover styling "
            "comes from CSS, not inline JS."
        )

    def test_fragment_html_parses_cleanly(self, client):
        """The rendered HTML must parse without errors so `innerHTML = html`
        injection in _chat_sse.html produces a valid DOM tree."""
        from html.parser import HTMLParser

        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_PRODUCT_WITH_URL]})
        parser = HTMLParser()
        # If parsing throws, the test fails — guarantees innerHTML-safe output.
        parser.feed(r.text)
        parser.close()

    def test_fragment_renders_card_element(self, client):
        """Sanity: the HTML actually contains the card. If this passes but
        the user still sees no cards live, the bug is downstream of the
        fragment endpoint (in the SSE pipeline or browser injection step)."""
        client.get("/")
        r = client.post("/chat/products-fragment", json={"products": [_PRODUCT_WITH_URL]})
        assert "chat-product-card" in r.text
        assert "Test Live Merchant Shirt" in r.text
        assert "Buy on Kith" in r.text


class TestAllChatTemplatesNoInlineJs:
    """A scan over ALL chat-related template surfaces to guarantee no
    inline event handlers exist in any of them — defensive against future
    regressions of the same class."""

    def test_no_inline_handlers_in_chat_product_card_template(self):
        from pathlib import Path

        template_path = (
            Path(__file__).parent.parent / "web" / "templates" / "_chat_product_card.html"
        )
        content = template_path.read_text()
        # Match actual HTML attributes (with `=`), NOT comments mentioning them.
        assert "onmouseenter=" not in content, (
            "_chat_product_card.html must not contain inline onmouseenter."
        )
        assert "onmouseleave=" not in content, (
            "_chat_product_card.html must not contain inline onmouseleave."
        )

    def test_no_inline_handlers_in_explore_product_card_template(self):
        from pathlib import Path

        template_path = Path(__file__).parent.parent / "web" / "templates" / "_product_card.html"
        content = template_path.read_text()
        assert "onmouseenter=" not in content
        assert "onmouseleave=" not in content

    def test_no_inline_handlers_in_cart_drawer_template(self):
        from pathlib import Path

        template_path = Path(__file__).parent.parent / "web" / "templates" / "_cart_drawer.html"
        content = template_path.read_text()
        assert "onmouseenter=" not in content
        assert "onmouseleave=" not in content

    def test_no_inline_handlers_in_product_detail_template(self):
        from pathlib import Path

        template_path = Path(__file__).parent.parent / "web" / "templates" / "product_detail.html"
        content = template_path.read_text()
        assert "onmouseenter=" not in content
        assert "onmouseleave=" not in content
