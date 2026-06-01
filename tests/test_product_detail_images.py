"""Tests: product detail page renders images.

The product detail page (/product/{merchant}/{id}) previously showed no
image at all. These tests verify the gallery section was correctly added to
web/templates/product_detail.html.

Sorts after test_user_journeys (p < u is false — p < u alphabetically?
'product_detail' > 'post_refinement' but 'product_detail' < 'purchase',
and 'product_detail' < 'user_journeys' because 'p' < 'u', so this file
sorts BEFORE test_user_journeys. Use get_event_loop() accordingly.
"""

from __future__ import annotations


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


# Known products from the seeded catalogue (all have images now)
_ATH_001 = ("athletic-co.myshopify.com", "ath_001")
_AUD_002 = ("audio-hub.myshopify.com", "aud_002")
_COF_003 = ("coffee-bar.myshopify.com", "cof_003")


class TestProductDetailImages:
    def test_detail_page_loads(self, client):
        merchant, pid = _ATH_001
        r = client.get(f"/product/{merchant}/{pid}")
        assert r.status_code == 200

    def test_detail_page_has_img_tag(self, client):
        """Page must render at least one <img> for the primary product image."""
        merchant, pid = _ATH_001
        r = client.get(f"/product/{merchant}/{pid}")
        assert (
            "<img" in r.text
        ), "Product detail page must contain an <img> tag for the product image"

    def test_detail_page_references_unsplash(self, client):
        """Primary image URL should be from Unsplash (matches catalogue data)."""
        merchant, pid = _ATH_001
        r = client.get(f"/product/{merchant}/{pid}")
        assert "unsplash.com" in r.text, "Product detail page should reference Unsplash image URL"

    def test_detail_page_has_thumbnail_strip_for_multi_image(self, client):
        """Products with 2+ images should show a thumbnail strip."""
        merchant, pid = _ATH_001
        r = client.get(f"/product/{merchant}/{pid}")
        # Should have at least 2 img tags (main + thumbnail)
        img_count = r.text.count("<img")
        assert (
            img_count >= 2
        ), f"Expected at least 2 <img> tags for thumbnail strip; found {img_count}"

    def test_detail_page_has_detail_main_img_id(self, client):
        """The primary image must have id='detail-main-img' for JS switching."""
        merchant, pid = _ATH_001
        r = client.get(f"/product/{merchant}/{pid}")
        assert 'id="detail-main-img"' in r.text

    def test_detail_page_thumbnail_onclick_present(self, client):
        """Thumbnails must have onclick handler to swap the main image."""
        merchant, pid = _ATH_001
        r = client.get(f"/product/{merchant}/{pid}")
        assert "detail-main-img" in r.text
        assert "onclick" in r.text

    def test_audio_product_detail_has_image(self, client):
        """Audio Hub products must also render images on the detail page."""
        merchant, pid = _AUD_002
        r = client.get(f"/product/{merchant}/{pid}")
        assert r.status_code == 200
        assert "<img" in r.text
        assert "unsplash.com" in r.text

    def test_coffee_product_detail_has_image(self, client):
        """Coffee Bar products must also render images on the detail page."""
        merchant, pid = _COF_003
        r = client.get(f"/product/{merchant}/{pid}")
        assert r.status_code == 200
        assert "<img" in r.text

    def test_detail_page_still_shows_name_and_price(self, client):
        """Adding image gallery must not remove existing product info."""
        merchant, pid = _ATH_001
        r = client.get(f"/product/{merchant}/{pid}")
        assert "Demo Running Shoes" in r.text
        assert "129.99" in r.text
        assert "Add to cart" in r.text

    def test_detail_page_still_shows_description(self, client):
        """Description must still render after the gallery was inserted."""
        merchant, pid = _ATH_001
        r = client.get(f"/product/{merchant}/{pid}")
        assert "Cushioned midsole" in r.text
