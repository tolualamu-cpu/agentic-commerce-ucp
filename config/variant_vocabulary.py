"""Variant-dimension vocabulary used by ``agents.product_grouping``.

Some merchants (notably Kith) model what a shopper considers "one product"
as several distinct Shopify listings that differ by a single attribute —
most commonly color, but the same pattern shows up for material, finish,
roast/flavor, and capacity/bag-size. ``group_into_families`` strips a
recognized trailing "<separator><value>" suffix from a product's title to
find its siblings; this module is the (append-only) table of recognized
suffix values and which variant dimension each belongs to.

Each entry maps a literal suffix VALUE (as it would appear in a title) to a
``(dimension_name, canonical_value)`` pair, e.g. ``"Black" -> ("Color",
"Black")`` or ``"Light Roast" -> ("Roast", "Light Roast")``. Longer values
are matched before shorter ones (see ``SUFFIX_SEPARATORS`` /
``group_into_families``) so "Light Roast" wins over a hypothetical "Roast"
entry.

New product types/dimensions should ADD entries here rather than touching
the grouping logic.
"""

from __future__ import annotations

# Separator patterns a merchant might use to suffix a variant value onto a
# base product title, e.g. "Kith Crewneck - Black", "Kith Crewneck (Black)".
# ``{value}`` is substituted with each VARIANT_VOCABULARY key.
SUFFIX_PATTERNS: list[str] = [
    " - {value}",
    " ({value})",
    " — {value}",
    " / {value}",
]

# value -> (dimension_name, canonical_value)
VARIANT_VOCABULARY: dict[str, tuple[str, str]] = {
    # ── Color ────────────────────────────────────────────────────────────
    "Black": ("Color", "Black"),
    "White": ("Color", "White"),
    "Off White": ("Color", "Off White"),
    "Navy": ("Color", "Navy"),
    "Blue": ("Color", "Blue"),
    "Sky Blue": ("Color", "Sky Blue"),
    "Red": ("Color", "Red"),
    "Maroon": ("Color", "Maroon"),
    "Burgundy": ("Color", "Burgundy"),
    "Green": ("Color", "Green"),
    "Olive": ("Color", "Olive"),
    "Sage": ("Color", "Sage"),
    "Yellow": ("Color", "Yellow"),
    "Orange": ("Color", "Orange"),
    "Brown": ("Color", "Brown"),
    "Tan": ("Color", "Tan"),
    "Khaki": ("Color", "Khaki"),
    "Beige": ("Color", "Beige"),
    "Cream": ("Color", "Cream"),
    "Grey": ("Color", "Grey"),
    "Gray": ("Color", "Gray"),
    "Charcoal": ("Color", "Charcoal"),
    "Silver": ("Color", "Silver"),
    "Gold": ("Color", "Gold"),
    "Pink": ("Color", "Pink"),
    "Purple": ("Color", "Purple"),
    "Teal": ("Color", "Teal"),
    "Multicolor": ("Color", "Multicolor"),
    "Clear Yellow": ("Color", "Clear Yellow"),
    "Light Grey": ("Color", "Light Grey"),
    "Dark Grey": ("Color", "Dark Grey"),
    "Light Blue": ("Color", "Light Blue"),
    "Dark Green": ("Color", "Dark Green"),
    # ── Material / Finish ───────────────────────────────────────────────
    "Leather": ("Material", "Leather"),
    "Suede": ("Material", "Suede"),
    "Canvas": ("Material", "Canvas"),
    "Nylon": ("Material", "Nylon"),
    "Mesh": ("Material", "Mesh"),
    "Knit": ("Material", "Knit"),
    "Denim": ("Material", "Denim"),
    "Corduroy": ("Material", "Corduroy"),
    "Fleece": ("Material", "Fleece"),
    "Wool": ("Material", "Wool"),
    "Cotton": ("Material", "Cotton"),
    "Linen": ("Material", "Linen"),
    "Matte": ("Finish", "Matte"),
    "Glossy": ("Finish", "Glossy"),
    "Satin": ("Finish", "Satin"),
    "Brushed": ("Finish", "Brushed"),
    # ── Roast / Flavor (food & beverage) ────────────────────────────────
    "Light Roast": ("Roast", "Light Roast"),
    "Medium Roast": ("Roast", "Medium Roast"),
    "Dark Roast": ("Roast", "Dark Roast"),
    "Medium-Dark Roast": ("Roast", "Medium-Dark Roast"),
    "Decaf": ("Roast", "Decaf"),
    "Vanilla": ("Flavor", "Vanilla"),
    "Hazelnut": ("Flavor", "Hazelnut"),
    "Caramel": ("Flavor", "Caramel"),
    "Original": ("Flavor", "Original"),
    # ── Capacity / Bag size ──────────────────────────────────────────────
    "12oz": ("Capacity", "12oz"),
    "16oz": ("Capacity", "16oz"),
    "20oz": ("Capacity", "20oz"),
    "1lb": ("Capacity", "1lb"),
    "2lb": ("Capacity", "2lb"),
    "5lb": ("Capacity", "5lb"),
    # ── Edition / Release ────────────────────────────────────────────────
    "Limited Edition": ("Edition", "Limited Edition"),
    "Anniversary Edition": ("Edition", "Anniversary Edition"),
}
