"""Browser e2e — variant picker modal (Phase 1, task 1.9 / 1.10 / task #18).

Clicking "Add to cart" on a product card for a product WITH variants must
open ``#variant-picker-overlay`` (``window.__openVariantPicker``), render one
control per ``option_names`` dimension (chips for "Color", a `<select>` for
everything else — Size, Material, Width, Capacity, etc. — generalising
beyond color per the standing rule), keep ``#vp-add`` disabled until a full
match is found, show "Out of stock" for an unavailable combination, and on
submit POST to ``/cart/add/{merchant}/{product_id}`` with the resolved
``variant_id``, then close the modal and bump the cart badge.

A product with NO variants must add directly via its existing
``.cart-toggle-btn`` / form-post path — no modal involved.

Cards are injected live via the same SSE ``products`` event mechanism used by
``test_browser_chat_cards_appear_without_reload.py`` (no real network calls;
mirrors the convention there). Covers a single-dimension demo product
(Size), a two-dimension demo product (Size + Color, including an
out-of-stock combination), and a Kith-shaped multi-member-family product
(Size + Material) — generalising the "variant" concept beyond color across
both demo and live merchants per CLAUDE.md rule 3 / the standing
generalisation rule.

NOTE: as of this writing, ``tests/browser`` fails to collect with
"fixture 'page' not found" — the ``page``/``live_server``/``sse_emit``
fixtures referenced here (and by every other file in ``tests/browser``)
could not be located anywhere in this repo. This is a pre-existing,
out-of-scope environment issue (see CLAUDE.md / prior phase notes). This
file is written to the same conventions as the rest of ``tests/browser`` so
it collects and passes once that environment issue is resolved.
"""

from __future__ import annotations


def _reveal_chat(page, base_url: str) -> None:
    page.goto(f"{base_url}/chat")
    page.wait_for_selector("#chat-form")
    page.evaluate("window.__chatRevealActive && window.__chatRevealActive()")


# ── Fixture products ────────────────────────────────────────────────────
#
# Shapes mirror the SSE `products` event payload consumed by
# `_chat_product_card.html` (see `_KITH_SHIRT` in
# test_browser_chat_cards_appear_without_reload.py), extended with
# `option_names` / `variants` per ProductResult / ProductVariant (1.1).

# Demo merchant, single-dimension variants (Size only) — mirrors ath_001.
_DEMO_SIZE_PRODUCT = {
    "merchant_domain": "athletic-co.myshopify.com",
    "product_id": "ath_001",
    "merchant": "Athletic Co",
    "name": "Demo Running Shoes",
    "price": "129.99",
    "currency": "USD",
    "rating": 4.5,
    "review_count": 120,
    "description": "Lightweight everyday trainer.",
    "in_stock": True,
    "images": [
        "https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=800&q=80",
    ],
    "url": None,
    "source_protocol": "shopify_mcp",
    "option_names": ["Size"],
    "variants": [
        {
            "variant_id": "ath_001-8",
            "sku": "ATH-001-8",
            "options": {"Size": "8"},
            "price": None,
            "in_stock": True,
            "image": None,
        },
        {
            "variant_id": "ath_001-9",
            "sku": "ATH-001-9",
            "options": {"Size": "9"},
            "price": None,
            "in_stock": True,
            "image": None,
        },
        {
            "variant_id": "ath_001-10",
            "sku": "ATH-001-10",
            "options": {"Size": "10"},
            "price": None,
            "in_stock": True,
            "image": None,
        },
    ],
}

# Demo merchant, two-dimension variants (Size + Color) — mirrors ath_003,
# with ONE out-of-stock combination (Size L / Color Navy) to exercise the
# "Out of stock" disabled state.
_DEMO_SIZE_COLOR_PRODUCT = {
    "merchant_domain": "athletic-co.myshopify.com",
    "product_id": "ath_003",
    "merchant": "Athletic Co",
    "name": "Performance Running Shorts",
    "price": "45.00",
    "currency": "USD",
    "rating": 4.2,
    "review_count": 58,
    "description": "Breathable running shorts with zip pocket.",
    "in_stock": True,
    "images": [
        "https://images.unsplash.com/photo-1556906781-9a412961c28c?w=800&q=80",
    ],
    "url": None,
    "source_protocol": "shopify_mcp",
    "option_names": ["Size", "Color"],
    "variants": [
        {
            "variant_id": "ath_003-m-black",
            "sku": "ATH-003-M-BLK",
            "options": {"Size": "M", "Color": "Black"},
            "price": None,
            "in_stock": True,
            "image": None,
        },
        {
            "variant_id": "ath_003-m-navy",
            "sku": "ATH-003-M-NVY",
            "options": {"Size": "M", "Color": "Navy"},
            "price": None,
            "in_stock": True,
            "image": None,
        },
        {
            "variant_id": "ath_003-l-black",
            "sku": "ATH-003-L-BLK",
            "options": {"Size": "L", "Color": "Black"},
            "price": None,
            "in_stock": True,
            "image": None,
        },
        {
            "variant_id": "ath_003-l-navy",
            "sku": "ATH-003-L-NVY",
            "options": {"Size": "L", "Color": "Navy"},
            "price": None,
            "in_stock": False,
            "image": None,
        },
    ],
}

# Demo merchant, no variants at all — mirrors ath_005 (Athletic Wireless
# Earbuds). Must use the plain `.cart-toggle-btn` path (no picker).
_DEMO_NO_VARIANT_PRODUCT = {
    "merchant_domain": "athletic-co.myshopify.com",
    "product_id": "ath_005",
    "merchant": "Athletic Co",
    "name": "Athletic Wireless Earbuds",
    "price": "79.00",
    "currency": "USD",
    "rating": 4.0,
    "review_count": 30,
    "description": "Sweat-resistant true wireless earbuds.",
    "in_stock": True,
    "images": [
        "https://images.unsplash.com/photo-1590658268037-6bf12165a8df?w=800&q=80",
    ],
    "url": None,
    "source_protocol": "shopify_mcp",
    "option_names": [],
    "variants": [],
}

# Live merchant (Kith), multi-member-family product synthesized by
# `group_into_families` — Size + Material (generalising beyond color, per
# the standing rule), modeled on the Mock Neck Sweater family
# (KITH_MOCKNECK_WOOL / KITH_MOCKNECK_COTTON) from
# tests/fixtures/kith_products.py. One Material x Size combo
# (Cotton / L) is unavailable.
_KITH_FAMILY_PRODUCT = {
    "merchant_domain": "kith.com",
    "product_id": "400008",
    "merchant": "Kith",
    "name": "Kith Mock Neck Sweater",
    "price": "168.00",
    "currency": "USD",
    "rating": 4.6,
    "review_count": 41,
    "description": "Ribbed mock neck sweater.",
    "in_stock": True,
    "images": [
        "https://cdn.shopify.com/kith/400008-front.jpg",
        "https://cdn.shopify.com/kith/400008-back.jpg",
    ],
    "url": "https://kith.com/products/kith-mock-neck-sweater",
    "source_protocol": "shopify_storefront",
    "option_names": ["Size", "Material"],
    "variants": [
        {
            "variant_id": "400008:60150",
            "sku": "KITH-MOCK-WOOL-S",
            "options": {"Size": "S", "Material": "Wool"},
            "price": "198.00",
            "in_stock": True,
            "image": None,
        },
        {
            "variant_id": "400008:60151",
            "sku": "KITH-MOCK-WOOL-M",
            "options": {"Size": "M", "Material": "Wool"},
            "price": "198.00",
            "in_stock": True,
            "image": None,
        },
        {
            "variant_id": "400008:60152",
            "sku": "KITH-MOCK-WOOL-L",
            "options": {"Size": "L", "Material": "Wool"},
            "price": "198.00",
            "in_stock": True,
            "image": None,
        },
        {
            "variant_id": "400009:60160",
            "sku": "KITH-MOCK-COTTON-S",
            "options": {"Size": "S", "Material": "Cotton"},
            "price": None,
            "in_stock": True,
            "image": None,
        },
        {
            "variant_id": "400009:60161",
            "sku": "KITH-MOCK-COTTON-M",
            "options": {"Size": "M", "Material": "Cotton"},
            "price": None,
            "in_stock": True,
            "image": None,
        },
        {
            "variant_id": "400009:60162",
            "sku": "KITH-MOCK-COTTON-L",
            "options": {"Size": "L", "Material": "Cotton"},
            "price": None,
            "in_stock": False,
            "image": None,
        },
    ],
}


# ── Single-dimension (Size) variant product ────────────────────────────


class TestSingleDimensionVariantPicker:
    def test_add_to_cart_opens_modal(self, page, live_server, sse_emit):
        base_url = live_server
        _reveal_chat(page, base_url)

        sse_emit(
            page,
            base_url,
            [
                {"type": "products", "data": {"products": [_DEMO_SIZE_PRODUCT]}},
                {"type": "done", "data": {}},
            ],
        )

        button = page.wait_for_selector(".chat-product-card button[onclick*='__openVariantPicker']")
        button.click()

        overlay = page.wait_for_selector("#variant-picker-overlay:not(.hidden)", timeout=4000)
        assert overlay is not None

        name = page.evaluate("document.getElementById('vp-name').textContent")
        assert "Demo Running Shoes" in name

        # Exactly one option group rendered, labeled "Size".
        labels = page.evaluate(
            "Array.from(document.querySelectorAll('#vp-options p')).map(p => p.textContent)"
        )
        assert labels == ["Size"]

        # Non-color dimension renders as a <select>, not chips.
        select_count = page.evaluate("document.querySelectorAll('#vp-options select').length")
        assert select_count == 1

    def test_add_button_disabled_until_size_chosen(self, page, live_server, sse_emit):
        base_url = live_server
        _reveal_chat(page, base_url)

        sse_emit(
            page,
            base_url,
            [
                {"type": "products", "data": {"products": [_DEMO_SIZE_PRODUCT]}},
                {"type": "done", "data": {}},
            ],
        )

        page.wait_for_selector(".chat-product-card button[onclick*='__openVariantPicker']").click()
        page.wait_for_selector("#variant-picker-overlay:not(.hidden)")

        add_btn = page.query_selector("#vp-add")
        assert add_btn.is_disabled()
        assert add_btn.text_content() == "Choose options"

        page.select_option("#vp-options select[data-dimension='Size']", "9")

        add_btn = page.query_selector("#vp-add")
        assert not add_btn.is_disabled()
        assert add_btn.text_content() == "Add to cart"

    def test_submit_adds_to_cart_and_closes_modal(self, page, live_server, sse_emit):
        base_url = live_server
        _reveal_chat(page, base_url)

        sse_emit(
            page,
            base_url,
            [
                {"type": "products", "data": {"products": [_DEMO_SIZE_PRODUCT]}},
                {"type": "done", "data": {}},
            ],
        )

        page.wait_for_selector(".chat-product-card button[onclick*='__openVariantPicker']").click()
        page.wait_for_selector("#variant-picker-overlay:not(.hidden)")
        page.select_option("#vp-options select[data-dimension='Size']", "9")

        page.click("#vp-add")

        page.wait_for_selector("#variant-picker-overlay.hidden", state="attached", timeout=8000)

        badge = page.wait_for_selector("#cart-badge", timeout=8000)
        assert badge.text_content().strip() not in ("", "0")

    def test_close_button_dismisses_modal_without_adding(self, page, live_server, sse_emit):
        base_url = live_server
        _reveal_chat(page, base_url)

        sse_emit(
            page,
            base_url,
            [
                {"type": "products", "data": {"products": [_DEMO_SIZE_PRODUCT]}},
                {"type": "done", "data": {}},
            ],
        )

        page.wait_for_selector(".chat-product-card button[onclick*='__openVariantPicker']").click()
        page.wait_for_selector("#variant-picker-overlay:not(.hidden)")

        page.click("#vp-close")
        page.wait_for_selector("#variant-picker-overlay.hidden", state="attached", timeout=4000)


# ── Two-dimension (Size + Color) variant product ───────────────────────


class TestTwoDimensionVariantPicker:
    def test_both_dimensions_render_with_color_as_chips(self, page, live_server, sse_emit):
        base_url = live_server
        _reveal_chat(page, base_url)

        sse_emit(
            page,
            base_url,
            [
                {"type": "products", "data": {"products": [_DEMO_SIZE_COLOR_PRODUCT]}},
                {"type": "done", "data": {}},
            ],
        )

        page.wait_for_selector(".chat-product-card button[onclick*='__openVariantPicker']").click()
        page.wait_for_selector("#variant-picker-overlay:not(.hidden)")

        labels = page.evaluate(
            "Array.from(document.querySelectorAll('#vp-options p')).map(p => p.textContent)"
        )
        assert set(labels) == {"Size", "Color"}

        # Color renders as chip buttons, Size as a <select>.
        color_chips = page.evaluate(
            "document.querySelectorAll(\"#vp-options button[data-dimension='Color']\").length"
        )
        assert color_chips == 2  # Black, Navy

        size_select = page.query_selector("#vp-options select[data-dimension='Size']")
        assert size_select is not None

    def test_add_button_requires_both_dimensions(self, page, live_server, sse_emit):
        base_url = live_server
        _reveal_chat(page, base_url)

        sse_emit(
            page,
            base_url,
            [
                {"type": "products", "data": {"products": [_DEMO_SIZE_COLOR_PRODUCT]}},
                {"type": "done", "data": {}},
            ],
        )

        page.wait_for_selector(".chat-product-card button[onclick*='__openVariantPicker']").click()
        page.wait_for_selector("#variant-picker-overlay:not(.hidden)")

        add_btn = page.query_selector("#vp-add")
        assert add_btn.is_disabled()

        # Choose only Size — still disabled.
        page.select_option("#vp-options select[data-dimension='Size']", "M")
        add_btn = page.query_selector("#vp-add")
        assert add_btn.is_disabled()

        # Choose Color too — now enabled.
        page.click("#vp-options button[data-dimension='Color'][data-value='Black']")
        add_btn = page.query_selector("#vp-add")
        assert not add_btn.is_disabled()
        assert add_btn.text_content() == "Add to cart"

    def test_out_of_stock_combination_disables_add(self, page, live_server, sse_emit):
        base_url = live_server
        _reveal_chat(page, base_url)

        sse_emit(
            page,
            base_url,
            [
                {"type": "products", "data": {"products": [_DEMO_SIZE_COLOR_PRODUCT]}},
                {"type": "done", "data": {}},
            ],
        )

        page.wait_for_selector(".chat-product-card button[onclick*='__openVariantPicker']").click()
        page.wait_for_selector("#variant-picker-overlay:not(.hidden)")

        # Size L / Color Navy is the out-of-stock combination.
        page.select_option("#vp-options select[data-dimension='Size']", "L")
        page.click("#vp-options button[data-dimension='Color'][data-value='Navy']")

        add_btn = page.query_selector("#vp-add")
        assert add_btn.is_disabled()
        assert add_btn.text_content() == "Out of stock"

        status = page.query_selector("#vp-status")
        assert "hidden" not in (status.get_attribute("class") or "")
        assert "out of stock" in status.text_content().lower()


# ── Kith (live merchant) family product, Size + Material ───────────────


class TestKithFamilyVariantPicker:
    def test_kith_family_modal_shows_size_and_material(self, page, live_server, sse_emit):
        base_url = live_server
        _reveal_chat(page, base_url)

        sse_emit(
            page,
            base_url,
            [
                {"type": "products", "data": {"products": [_KITH_FAMILY_PRODUCT]}},
                {"type": "done", "data": {}},
            ],
        )

        page.wait_for_selector(".chat-product-card button[onclick*='__openVariantPicker']").click()
        page.wait_for_selector("#variant-picker-overlay:not(.hidden)")

        labels = page.evaluate(
            "Array.from(document.querySelectorAll('#vp-options p')).map(p => p.textContent)"
        )
        assert set(labels) == {"Size", "Material"}

        # "Material" is not a color dimension -> renders as <select>, not chips.
        material_select = page.query_selector("#vp-options select[data-dimension='Material']")
        assert material_select is not None
        material_buttons = page.evaluate(
            "document.querySelectorAll(\"#vp-options button[data-dimension='Material']\").length"
        )
        assert material_buttons == 0

    def test_kith_family_full_match_enables_add(self, page, live_server, sse_emit):
        """Selecting a full Size+Material combination on a Kith FAMILY product
        (synthesized ``{member_id}:{member_variant_id}`` variant ids spanning
        sibling listings) enables the "Add to cart" button.

        NOTE: the actual server-side add of a family-synthesized variant id
        (price-override resolution + cart line) is covered by the unit test
        ``tests/test_kith_merchant_journeys.py::test_add_family_synthesized_variant_id_succeeds``,
        which seeds ``ctx.session.product_families`` the way
        ``_group_discovered_products`` does at runtime. The browser harness
        injects the card directly via SSE (no discovery pass), so the family
        cache is intentionally NOT populated here and the server add cannot
        resolve the synthesized id offline. The full browser
        modal→fetch→close→badge lifecycle is proven by
        ``TestSingleDimensionVariantPicker::test_submit_adds_to_cart_and_closes_modal``
        and the PDP submit tests, so this case asserts only the family-shape
        variant-matching that IS observable client-side.
        """
        base_url = live_server
        _reveal_chat(page, base_url)

        sse_emit(
            page,
            base_url,
            [
                {"type": "products", "data": {"products": [_KITH_FAMILY_PRODUCT]}},
                {"type": "done", "data": {}},
            ],
        )

        page.wait_for_selector(".chat-product-card button[onclick*='__openVariantPicker']").click()
        page.wait_for_selector("#variant-picker-overlay:not(.hidden)")

        add_btn = page.query_selector("#vp-add")
        # Before any selection the button is disabled ("Choose options").
        assert add_btn.is_disabled()

        # Wool variants carry a $198 price override vs. the $168 base price.
        # Selecting a full Size+Material combination resolves to a synthesized
        # family variant and enables the add button.
        page.select_option("#vp-options select[data-dimension='Material']", "Wool")
        page.select_option("#vp-options select[data-dimension='Size']", "M")

        add_btn = page.query_selector("#vp-add")
        assert not add_btn.is_disabled()
        assert add_btn.text_content().strip() == "Add to cart"


# ── Product detail page (PDP) inline variant controls ──────────────────
#
# Phase 1 bugfix addendum (2026-06-10), Bug 1/2: `_variant_picker_controls.html`
# is `{% include %}`d ABOVE the add-to-cart `<form>` in product_detail.html,
# so `#pdp-variant-id` / `#pdp-add-btn` do not exist in the DOM yet when the
# script first runs. The fix looks them up lazily inside `render()` instead
# of caching `getElementById` results once at the top -- otherwise the
# button stays permanently disabled ("Select options") even after a full
# selection. This covers the PDP path (previously only the modal was
# covered above).


class TestProductDetailPageVariantControls:
    def test_selecting_size_enables_add_to_cart_with_correct_price(
        self, page, live_server, sse_emit
    ):
        base_url = live_server

        # ath_001 (Demo Running Shoes) — single-dimension (Size) variant
        # product, $129.99 base price, no per-variant price override.
        page.goto(f"{base_url}/product/athletic-co.myshopify.com/ath_001")
        page.wait_for_selector("#product-variants")

        add_btn = page.query_selector("#pdp-add-btn")
        assert add_btn.is_disabled()
        assert add_btn.text_content().strip() == "Select options"

        # Pre-selection (single-value dimensions) doesn't apply here since
        # ath_001 has multiple sizes -- the <select> starts unselected.
        page.select_option("#pdp-variant-controls select", "9")

        add_btn = page.query_selector("#pdp-add-btn")
        assert not add_btn.is_disabled()
        assert add_btn.text_content().strip() == "Add to cart"

        variant_id = page.evaluate("document.getElementById('pdp-variant-id').value")
        assert variant_id == "ath_001-9"

        price_text = page.evaluate("document.getElementById('pdp-price').textContent")
        assert "129.99" in price_text

    def test_submit_adds_correct_variant_to_cart(self, page, live_server, sse_emit):
        base_url = live_server

        page.goto(f"{base_url}/product/athletic-co.myshopify.com/ath_001")
        page.wait_for_selector("#product-variants")

        page.select_option("#pdp-variant-controls select", "10")
        page.click("#pdp-add-btn")

        badge = page.wait_for_selector("#cart-badge", timeout=8000)
        assert badge.text_content().strip() not in ("", "0")

        cart_resp = page.request.get(f"{base_url}/cart", headers={"Accept": "application/json"})
        cart = cart_resp.json()
        line = next(l for l in cart["lines"] if l["product_id"] == "ath_001")
        assert line["variant_id"] == "ath_001-10"
        assert line["selected_options"] == {"Size": "10"}

    def test_no_variant_product_add_to_cart_works_unchanged(self, page, live_server, sse_emit):
        """Regression: a no-variant PDP (`#product-variants` absent /
        `product.variants` empty) must keep its "Add to cart" button
        enabled and unaffected by the lazy-lookup change."""
        base_url = live_server

        page.goto(f"{base_url}/product/athletic-co.myshopify.com/ath_005")

        add_btn = page.wait_for_selector("#pdp-add-btn")
        assert not add_btn.is_disabled()
        assert add_btn.text_content().strip() == "Add to cart"

        page.click("#pdp-add-btn")
        badge = page.wait_for_selector("#cart-badge", timeout=8000)
        assert badge.text_content().strip() not in ("", "0")


# ── No-variant product — direct add, no modal ───────────────────────────


class TestNoVariantProductDirectAdd:
    def test_no_variant_product_has_no_picker_button(self, page, live_server, sse_emit):
        base_url = live_server
        _reveal_chat(page, base_url)

        sse_emit(
            page,
            base_url,
            [
                {"type": "products", "data": {"products": [_DEMO_NO_VARIANT_PRODUCT]}},
                {"type": "done", "data": {}},
            ],
        )

        card = page.wait_for_selector(".chat-product-card")
        assert card is not None

        picker_buttons = page.evaluate(
            "document.querySelectorAll(\".chat-product-card button[onclick*='__openVariantPicker']\").length"
        )
        assert picker_buttons == 0

        toggle_buttons = page.evaluate(
            "document.querySelectorAll('.chat-product-card .cart-toggle-btn').length"
        )
        assert toggle_buttons == 1

    def test_clicking_add_to_cart_never_opens_variant_overlay(self, page, live_server, sse_emit):
        base_url = live_server
        _reveal_chat(page, base_url)

        sse_emit(
            page,
            base_url,
            [
                {"type": "products", "data": {"products": [_DEMO_NO_VARIANT_PRODUCT]}},
                {"type": "done", "data": {}},
            ],
        )

        page.wait_for_selector(".chat-product-card .cart-toggle-btn").click()

        # Give the overlay a moment to (not) appear.
        page.wait_for_timeout(500)
        overlay_hidden = page.evaluate(
            "(() => { const el = document.getElementById('variant-picker-overlay'); "
            "return !el || el.classList.contains('hidden'); })()"
        )
        assert overlay_hidden is True
