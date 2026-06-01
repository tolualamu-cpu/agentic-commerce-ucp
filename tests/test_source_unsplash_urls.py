"""Verify that catalogue image URLs use the images.unsplash.com format.

Each URL should be in the form:
  https://images.unsplash.com/photo-{id}?w=800&q=80

Tests also verify uniqueness and coverage.

Sorts before test_user_journeys ('s' < 'u') — uses get_event_loop().
"""

from __future__ import annotations

from collections import Counter

import pytest

from config.catalogue import MERCHANTS


def _all_images():
    return [
        (domain, p["id"], url)
        for domain, products in MERCHANTS.items()
        for p in products
        for url in p.get("images", [])
    ]


class TestUnsplashUrlFormat:
    def test_all_images_use_unsplash_domain(self):
        """Every image URL must be from unsplash.com."""
        records = _all_images()
        non_unsplash = [
            f"{domain}/{pid}: {url}" for domain, pid, url in records if "unsplash.com" not in url
        ]
        assert not non_unsplash, "Non-Unsplash URLs found:\n" + "\n".join(non_unsplash)

    def test_all_images_start_with_https(self):
        records = _all_images()
        non_https = [
            f"{domain}/{pid}: {url}"
            for domain, pid, url in records
            if not url.startswith("https://")
        ]
        assert not non_https, "Non-HTTPS URLs:\n" + "\n".join(non_https)

    def test_all_48_urls_globally_unique(self):
        """All 48 image URLs (24 products × 2) must be globally unique."""
        records = _all_images()
        counts = Counter(url for _, _, url in records)
        dups = {url: c for url, c in counts.items() if c > 1}
        if dups:
            detail = []
            for dup_url, count in dups.items():
                offenders = [f"{d}/{pid}" for d, pid, u in records if u == dup_url]
                detail.append(f"  {dup_url} (x{count}) — {', '.join(offenders)}")
            pytest.fail("Duplicate image URLs found:\n" + "\n".join(detail))

    def test_no_duplicate_primary_image_urls(self):
        primaries = [
            (domain, p["id"], p["images"][0])
            for domain, products in MERCHANTS.items()
            for p in products
            if p.get("images")
        ]
        counts = Counter(url for _, _, url in primaries)
        dups = {url: c for url, c in counts.items() if c > 1}
        if dups:
            detail = []
            for dup_url, count in dups.items():
                offenders = [f"{d}/{pid}" for d, pid, u in primaries if u == dup_url]
                detail.append(f"  {dup_url} — used by: {', '.join(offenders)}")
            pytest.fail("Duplicate primary image URLs:\n" + "\n".join(detail))

    def test_total_image_count(self):
        assert len(_all_images()) == 48
