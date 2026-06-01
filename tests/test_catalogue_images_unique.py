"""Tests that every product image URL in the catalogue is globally unique.

Uniqueness is a hard requirement: duplicate images cause the same photo to
appear on two different product cards, which breaks the visual identity of
each product and confuses users comparing items.

Sorts alphabetically after test_catalogue_images.py (unique > images) so
event-loop ordering is safe — uses no asyncio.
"""

from __future__ import annotations

from collections import Counter

import pytest

from config.catalogue import MERCHANTS


def _all_image_records():
    """Return a flat list of (domain, product_id, url) for every image
    in the entire catalogue."""
    records = []
    for domain, products in MERCHANTS.items():
        for p in products:
            for url in p.get("images", []):
                records.append((domain, p["id"], url))
    return records


class TestCatalogueImageUniqueness:
    def test_no_duplicate_image_urls_globally(self):
        """Every image URL in the catalogue must appear exactly once."""
        records = _all_image_records()
        url_counts = Counter(url for _, _, url in records)
        duplicates = {url: count for url, count in url_counts.items() if count > 1}

        if duplicates:
            detail_lines = []
            for dup_url, count in duplicates.items():
                offenders = [f"{domain}/{pid}" for domain, pid, url in records if url == dup_url]
                detail_lines.append(
                    f"  URL used {count}× → {dup_url}\n    products: {', '.join(offenders)}"
                )
            pytest.fail(
                f"{len(duplicates)} duplicate image URL(s) found:\n" + "\n".join(detail_lines)
            )

    def test_no_duplicate_primary_images(self):
        """The first (primary) image of each product must be unique."""
        primary_records = []
        for domain, products in MERCHANTS.items():
            for p in products:
                imgs = p.get("images", [])
                if imgs:
                    primary_records.append((domain, p["id"], imgs[0]))

        primary_counts = Counter(url for _, _, url in primary_records)
        duplicates = {url: c for url, c in primary_counts.items() if c > 1}

        if duplicates:
            detail_lines = []
            for dup_url, count in duplicates.items():
                offenders = [
                    f"{domain}/{pid}" for domain, pid, url in primary_records if url == dup_url
                ]
                detail_lines.append(
                    f"  Primary URL shared by {count} products → {dup_url}\n"
                    f"    products: {', '.join(offenders)}"
                )
            pytest.fail(
                f"{len(duplicates)} duplicate primary image(s):\n" + "\n".join(detail_lines)
            )

    def test_total_image_count(self):
        """Sanity: 24 products × 2 images each = 48 total image entries."""
        records = _all_image_records()
        assert len(records) == 48, (
            f"Expected 48 image entries (24 products × 2); got {len(records)}"
        )

    def test_every_product_has_at_least_two_images(self):
        """Each product must have a primary AND at least one secondary image."""
        issues = []
        for domain, products in MERCHANTS.items():
            for p in products:
                imgs = p.get("images", [])
                if len(imgs) < 2:
                    issues.append(f"{domain}/{p['id']} has only {len(imgs)} image(s)")
        assert not issues, "Products with fewer than 2 images:\n" + "\n".join(issues)

    def test_all_image_urls_are_unsplash(self):
        """All images must be from the Unsplash CDN (stable direct URLs)."""
        records = _all_image_records()
        non_unsplash = [
            f"{domain}/{pid}: {url}" for domain, pid, url in records if "unsplash.com" not in url
        ]
        assert not non_unsplash, "Non-Unsplash URLs found:\n" + "\n".join(non_unsplash)
