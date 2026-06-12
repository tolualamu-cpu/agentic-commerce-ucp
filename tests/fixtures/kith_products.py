"""Shared 20+ product Kith fixture set (Phase 1, task 1.10 cross-cutting
requirement #1).

A single, reusable set of **21** Kith product listings shaped exactly like a
real `/products.json` response (same shape as
``tests/test_live_shopify_transport.py::SAMPLE_SHOPIFY_PRODUCTS``), spanning
footwear, tops, outerwear, bottoms, and accessories. Reused by:

  - ``tests/test_live_shopify_transport.py``
  - ``tests/test_kith_merchant_journeys.py``
  - ``tests/test_chat_variant_flow.py``
  - ``tests/test_product_grouping.py`` / ``test_product_grouping_integration.py``
  - ``tests/test_discovery_query_variants.py``
  - Phase 3 collections tests

Coverage matrix:
  - **No-variant (single SKU)**: Camp Cap, both Tonal Beanies, both Treats
    Mugs, Shearling Bomber - Suede.
  - **Single-dimension variants (Size only)**: both 990v6 colorways, Dunlin
    Sneaker, Track Spike, both Crewnecks, both Mock Neck Sweaters, both
    Coaches Jackets, Nylon Track Jacket, Sweatpant, Denim Jean.
  - **Multi-dimension variants (Size + Color)**: Pocket Tee, Cargo Short.
  - **Multi-member families spanning >1 splitting dimension**:
      - Color: 990v6 (Grey/Navy), Crewneck (Black/White), Coaches Jacket
        (Olive/Black), Tonal Beanie (Charcoal/Grey).
      - Material: Mock Neck Sweater (Wool/Cotton).
      - Capacity: Treats Mug (12oz/16oz).
  - **Standalone listings with a vocabulary suffix but no sibling**
    (family-of-1 pass-through with a stripped dimension): Shearling Bomber -
    Suede (Material), Denim Jean - Black (Color).
  - **Out-of-stock**: Track Spike (every variant unavailable); Crewneck -
    White's "M" variant is unavailable.
  - **Price-spread / price-override**: Nylon Track Jacket (XL costs more
    than S/M/L); Mock Neck Sweater Wool ($198) vs Cotton ($168) — diverging
    member prices once grouped into one family; Treats Mug 12oz ($18) vs
    16oz ($22).

Pure data module — no asyncio, no I/O. Sorts before
``test_user_journeys.py`` but doesn't matter since nothing here touches the
event loop.
"""

from __future__ import annotations


def _variant(
    variant_id: int,
    title: str,
    price: str,
    *,
    available: bool = True,
    option1: str | None = None,
    option2: str | None = None,
    sku: str | None = None,
) -> dict:
    v = {
        "id": variant_id,
        "title": title,
        "price": price,
        "available": available,
    }
    if option1 is not None:
        v["option1"] = option1
    if option2 is not None:
        v["option2"] = option2
    if sku is not None:
        v["sku"] = sku
    return v


def _images(product_id: int) -> list[dict]:
    return [
        {"id": product_id * 10 + 1, "src": f"https://cdn.shopify.com/kith/{product_id}-front.jpg"},
        {"id": product_id * 10 + 2, "src": f"https://cdn.shopify.com/kith/{product_id}-back.jpg"},
    ]


def _product(
    product_id: int,
    title: str,
    handle: str,
    *,
    product_type: str,
    tags: list[str],
    variants: list[dict],
    options: list[dict] | None = None,
    body_html: str = "<p>Premium Kith product.</p>",
) -> dict:
    product: dict = {
        "id": product_id,
        "title": title,
        "handle": handle,
        "body_html": body_html,
        "vendor": "Kith",
        "product_type": product_type,
        "tags": tags,
        "variants": variants,
        "images": _images(product_id),
    }
    if options is not None:
        product["options"] = options
    return product


def _size_variants(
    base_id: int, sizes: list[str], price: str | dict[str, str], *, available: bool = True
) -> list[dict]:
    """Build Size-only variants. ``price`` is either a flat string applied to
    every size, or a dict mapping size -> price (for price-spread products)."""
    out = []
    for i, size in enumerate(sizes):
        p = price[size] if isinstance(price, dict) else price
        out.append(
            _variant(
                base_id + i, size, p, available=available, option1=size, sku=f"K{base_id}-{size}"
            )
        )
    return out


def _single_sku_variant(base_id: int, price: str, *, available: bool = True) -> list[dict]:
    return [
        _variant(
            base_id,
            "Default Title",
            price,
            available=available,
            option1="Default Title",
            sku=f"K{base_id}",
        )
    ]


# ─── Footwear ─────────────────────────────────────────────────────────────

KITH_990V6_GREY = _product(
    400001,
    "Kith x New Balance 990v6 - Grey",
    "knb990v6-grey",
    product_type="Footwear",
    tags=["sneaker", "footwear", "new-balance"],
    options=[{"name": "Size"}],
    variants=_size_variants(60001, ["8", "9", "10", "11", "12"], "200.00"),
)

KITH_990V6_NAVY = _product(
    400002,
    "Kith x New Balance 990v6 - Navy",
    "knb990v6-navy",
    product_type="Footwear",
    tags=["sneaker", "footwear", "new-balance"],
    options=[{"name": "Size"}],
    variants=_size_variants(60010, ["8", "9", "10", "11", "12"], "200.00"),
)

KITH_DUNLIN_SUEDE_SNEAKER = _product(
    400003,
    "Kith Dunlin Suede Sneaker",
    "kith-dunlin-suede",
    product_type="Footwear",
    tags=["sneaker", "footwear"],
    options=[{"name": "Size"}],
    variants=_size_variants(60020, ["8", "9", "10", "11"], "130.00"),
)

KITH_TRACK_SPIKE = _product(
    400004,
    "Kith Track Spike",
    "kith-track-spike",
    product_type="Footwear",
    tags=["sneaker", "footwear", "running"],
    options=[{"name": "Size"}],
    variants=_size_variants(60030, ["8", "9", "10", "11"], "140.00", available=False),
)


# ─── Tops ────────────────────────────────────────────────────────────────

KITH_CREWNECK_BLACK = _product(
    400005,
    "Kith Logo Crewneck - Black",
    "khmg030009-001",
    product_type="Crewnecks",
    tags=["crewneck", "kith"],
    options=[{"name": "Size"}],
    variants=_size_variants(60040, ["S", "M", "L"], "155.00"),
)

KITH_CREWNECK_WHITE = _product(
    400006,
    "Kith Logo Crewneck - White",
    "khmg030009-101",
    product_type="Crewnecks",
    tags=["crewneck", "kith"],
    options=[{"name": "Size"}],
    variants=[
        _variant(60050, "S", "155.00", available=True, option1="S", sku="K60050-S"),
        _variant(60051, "M", "155.00", available=False, option1="M", sku="K60051-M"),
    ],
)

KITH_POCKET_TEE = _product(
    400007,
    "Kith Pocket Tee",
    "kith-pocket-tee",
    product_type="Short Sleeve Tees",
    tags=["tee", "kith"],
    options=[{"name": "Size"}, {"name": "Color"}],
    variants=[
        _variant(60060, "S / Black", "65.00", option1="S", option2="Black", sku="K60060-S-BLK"),
        _variant(60061, "M / Black", "65.00", option1="M", option2="Black", sku="K60061-M-BLK"),
        _variant(60062, "S / White", "65.00", option1="S", option2="White", sku="K60062-S-WHT"),
        _variant(60063, "M / White", "65.00", option1="M", option2="White", sku="K60063-M-WHT"),
    ],
)

KITH_MOCKNECK_WOOL = _product(
    400008,
    "Kith Mock Neck Sweater - Wool",
    "kith-mockneck-wool",
    product_type="Sweaters",
    tags=["sweater", "kith"],
    options=[{"name": "Size"}],
    variants=_size_variants(60070, ["S", "M", "L"], "198.00"),
)

KITH_MOCKNECK_COTTON = _product(
    400009,
    "Kith Mock Neck Sweater - Cotton",
    "kith-mockneck-cotton",
    product_type="Sweaters",
    tags=["sweater", "kith"],
    options=[{"name": "Size"}],
    variants=_size_variants(60080, ["S", "M", "L"], "168.00"),
)


# ─── Outerwear ───────────────────────────────────────────────────────────

KITH_COACHES_JACKET_OLIVE = _product(
    400010,
    "Kith Coaches Jacket - Olive",
    "kith-coaches-olive",
    product_type="Outerwear",
    tags=["jacket", "outerwear", "kith"],
    options=[{"name": "Size"}],
    variants=_size_variants(60090, ["S", "M", "L", "XL"], "248.00"),
)

KITH_COACHES_JACKET_BLACK = _product(
    400011,
    "Kith Coaches Jacket - Black",
    "kith-coaches-black",
    product_type="Outerwear",
    tags=["jacket", "outerwear", "kith"],
    options=[{"name": "Size"}],
    variants=_size_variants(60100, ["S", "M", "L", "XL"], "248.00"),
)

KITH_NYLON_TRACK_JACKET = _product(
    400012,
    "Kith Nylon Track Jacket",
    "kith-nylon-track-jacket",
    product_type="Outerwear",
    tags=["jacket", "outerwear", "kith"],
    options=[{"name": "Size"}],
    variants=_size_variants(
        60110,
        ["S", "M", "L", "XL"],
        {"S": "128.00", "M": "128.00", "L": "128.00", "XL": "148.00"},
    ),
)

KITH_SHEARLING_BOMBER_SUEDE = _product(
    400013,
    "Kith Shearling Bomber - Suede",
    "kith-shearling-bomber-suede",
    product_type="Outerwear",
    tags=["jacket", "outerwear", "kith"],
    variants=_single_sku_variant(60120, "598.00"),
)


# ─── Bottoms ─────────────────────────────────────────────────────────────

KITH_SWEATPANT = _product(
    400014,
    "Kith Sweatpant",
    "kith-sweatpant",
    product_type="Bottoms",
    tags=["sweatpant", "bottoms", "kith"],
    options=[{"name": "Size"}],
    variants=_size_variants(60130, ["S", "M", "L", "XL"], "110.00"),
)

KITH_DENIM_JEAN_BLACK = _product(
    400015,
    "Kith Denim Jean - Black",
    "kith-denim-jean-black",
    product_type="Bottoms",
    tags=["denim", "bottoms", "kith"],
    options=[{"name": "Size"}],
    variants=_size_variants(60140, ["30", "32", "34"], "195.00"),
)

KITH_CARGO_SHORT = _product(
    400016,
    "Kith Cargo Short",
    "kith-cargo-short",
    product_type="Bottoms",
    tags=["short", "bottoms", "kith"],
    options=[{"name": "Size"}, {"name": "Color"}],
    variants=[
        _variant(60150, "S / Khaki", "118.00", option1="S", option2="Khaki", sku="K60150-S-KHK"),
        _variant(60151, "M / Khaki", "118.00", option1="M", option2="Khaki", sku="K60151-M-KHK"),
        _variant(60152, "S / Olive", "118.00", option1="S", option2="Olive", sku="K60152-S-OLV"),
        _variant(60153, "M / Olive", "118.00", option1="M", option2="Olive", sku="K60153-M-OLV"),
    ],
)


# ─── Accessories ─────────────────────────────────────────────────────────

KITH_CAMP_CAP = _product(
    400017,
    "Kith Camp Cap",
    "kith-camp-cap",
    product_type="Accessories",
    tags=["hat", "accessories", "kith"],
    variants=_single_sku_variant(60160, "48.00"),
)

KITH_TONAL_BEANIE_CHARCOAL = _product(
    400018,
    "Kith Tonal Beanie - Charcoal",
    "kith-tonal-beanie-charcoal",
    product_type="Accessories",
    tags=["beanie", "accessories", "kith"],
    variants=_single_sku_variant(60170, "38.00"),
)

KITH_TONAL_BEANIE_GREY = _product(
    400019,
    "Kith Tonal Beanie - Grey",
    "kith-tonal-beanie-grey",
    product_type="Accessories",
    tags=["beanie", "accessories", "kith"],
    variants=_single_sku_variant(60180, "38.00"),
)

KITH_TREATS_MUG_12OZ = _product(
    400020,
    "Kith Treats Mug - 12oz",
    "kith-treats-mug-12oz",
    product_type="Accessories",
    tags=["mug", "accessories", "kith", "home"],
    variants=_single_sku_variant(60190, "18.00"),
)

KITH_TREATS_MUG_16OZ = _product(
    400021,
    "Kith Treats Mug - 16oz",
    "kith-treats-mug-16oz",
    product_type="Accessories",
    tags=["mug", "accessories", "kith", "home"],
    variants=_single_sku_variant(60200, "22.00"),
)


# ─── Aggregate fixture set ─────────────────────────────────────────────────

ALL_KITH_PRODUCTS: list[dict] = [
    KITH_990V6_GREY,
    KITH_990V6_NAVY,
    KITH_DUNLIN_SUEDE_SNEAKER,
    KITH_TRACK_SPIKE,
    KITH_CREWNECK_BLACK,
    KITH_CREWNECK_WHITE,
    KITH_POCKET_TEE,
    KITH_MOCKNECK_WOOL,
    KITH_MOCKNECK_COTTON,
    KITH_COACHES_JACKET_OLIVE,
    KITH_COACHES_JACKET_BLACK,
    KITH_NYLON_TRACK_JACKET,
    KITH_SHEARLING_BOMBER_SUEDE,
    KITH_SWEATPANT,
    KITH_DENIM_JEAN_BLACK,
    KITH_CARGO_SHORT,
    KITH_CAMP_CAP,
    KITH_TONAL_BEANIE_CHARCOAL,
    KITH_TONAL_BEANIE_GREY,
    KITH_TREATS_MUG_12OZ,
    KITH_TREATS_MUG_16OZ,
]

assert len(ALL_KITH_PRODUCTS) == 21

# Mock `/products.json` response payload.
KITH_FIXTURE_PRODUCTS: dict = {"products": ALL_KITH_PRODUCTS}

# String product ids as returned by `_shopify_product_to_dict` (str(id)).
ALL_KITH_PRODUCT_IDS: list[str] = [str(p["id"]) for p in ALL_KITH_PRODUCTS]

# product_type -> list of product ids (spans footwear/tops/outerwear/
# bottoms/accessories per the cross-cutting requirement).
KITH_PRODUCT_TYPES: dict[str, list[str]] = {}
for _p in ALL_KITH_PRODUCTS:
    KITH_PRODUCT_TYPES.setdefault(_p["product_type"], []).append(str(_p["id"]))

# No-variant (single-SKU) product ids.
KITH_NO_VARIANT_IDS: list[str] = [
    str(KITH_SHEARLING_BOMBER_SUEDE["id"]),
    str(KITH_CAMP_CAP["id"]),
    str(KITH_TONAL_BEANIE_CHARCOAL["id"]),
    str(KITH_TONAL_BEANIE_GREY["id"]),
    str(KITH_TREATS_MUG_12OZ["id"]),
    str(KITH_TREATS_MUG_16OZ["id"]),
]

# Single-dimension (Size-only) variant product ids.
KITH_SINGLE_DIM_IDS: list[str] = [
    str(KITH_990V6_GREY["id"]),
    str(KITH_990V6_NAVY["id"]),
    str(KITH_DUNLIN_SUEDE_SNEAKER["id"]),
    str(KITH_TRACK_SPIKE["id"]),
    str(KITH_CREWNECK_BLACK["id"]),
    str(KITH_CREWNECK_WHITE["id"]),
    str(KITH_MOCKNECK_WOOL["id"]),
    str(KITH_MOCKNECK_COTTON["id"]),
    str(KITH_COACHES_JACKET_OLIVE["id"]),
    str(KITH_COACHES_JACKET_BLACK["id"]),
    str(KITH_NYLON_TRACK_JACKET["id"]),
    str(KITH_SWEATPANT["id"]),
    str(KITH_DENIM_JEAN_BLACK["id"]),
]

# Multi-dimension (Size + Color) variant product ids.
KITH_MULTI_DIM_IDS: list[str] = [
    str(KITH_POCKET_TEE["id"]),
    str(KITH_CARGO_SHORT["id"]),
]

# Out-of-stock products / variants.
KITH_ALL_UNAVAILABLE_IDS: list[str] = [str(KITH_TRACK_SPIKE["id"])]
KITH_PARTIAL_UNAVAILABLE_IDS: list[str] = [str(KITH_CREWNECK_WHITE["id"])]

# Price-spread / price-override product ids (within-product or across a
# multi-member family).
KITH_PRICE_SPREAD_IDS: list[str] = [
    str(KITH_NYLON_TRACK_JACKET["id"]),  # XL costs more than S/M/L
]

# Multi-member families: primary id -> {dimension, member ids, all member titles}
KITH_FAMILIES: dict[str, dict] = {
    str(KITH_990V6_GREY["id"]): {
        "dimension": "Color",
        "member_ids": [str(KITH_990V6_GREY["id"]), str(KITH_990V6_NAVY["id"])],
        "normalized_title": "Kith x New Balance 990v6",
    },
    str(KITH_CREWNECK_BLACK["id"]): {
        "dimension": "Color",
        "member_ids": [str(KITH_CREWNECK_BLACK["id"]), str(KITH_CREWNECK_WHITE["id"])],
        "normalized_title": "Kith Logo Crewneck",
    },
    str(KITH_COACHES_JACKET_OLIVE["id"]): {
        "dimension": "Color",
        "member_ids": [str(KITH_COACHES_JACKET_OLIVE["id"]), str(KITH_COACHES_JACKET_BLACK["id"])],
        "normalized_title": "Kith Coaches Jacket",
    },
    str(KITH_TONAL_BEANIE_CHARCOAL["id"]): {
        "dimension": "Color",
        "member_ids": [str(KITH_TONAL_BEANIE_CHARCOAL["id"]), str(KITH_TONAL_BEANIE_GREY["id"])],
        "normalized_title": "Kith Tonal Beanie",
    },
    str(KITH_MOCKNECK_WOOL["id"]): {
        "dimension": "Material",
        "member_ids": [str(KITH_MOCKNECK_WOOL["id"]), str(KITH_MOCKNECK_COTTON["id"])],
        "normalized_title": "Kith Mock Neck Sweater",
    },
    str(KITH_TREATS_MUG_12OZ["id"]): {
        "dimension": "Capacity",
        "member_ids": [str(KITH_TREATS_MUG_12OZ["id"]), str(KITH_TREATS_MUG_16OZ["id"])],
        "normalized_title": "Kith Treats Mug",
    },
}

# Standalone listings whose titles end in a recognized variant-vocabulary
# suffix but have NO sibling in this fixture set — family-of-1 pass-through
# even though a dimension would be stripped from the title.
KITH_STANDALONE_WITH_SUFFIX_IDS: list[str] = [
    str(KITH_SHEARLING_BOMBER_SUEDE["id"]),  # "... - Suede" (Material)
    str(KITH_DENIM_JEAN_BLACK["id"]),  # "... - Black" (Color)
]


def _mock_response(data: dict):
    """Build a ``MagicMock`` mimicking ``httpx.Response`` for ``data``."""
    from unittest.mock import MagicMock

    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = data
    return resp


def make_kith_transport(*, products: dict | None = None):
    """Build a ``LiveShopifyTransport`` against ``https://kith.com`` with a
    mocked HTTP client returning ``products`` (default:
    :data:`KITH_FIXTURE_PRODUCTS`). No real network calls."""
    from unittest.mock import AsyncMock

    from adapters.shopify_mcp import LiveShopifyTransport

    transport = LiveShopifyTransport("https://kith.com", max_pages=1, cache_ttl=9999)
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response(products or KITH_FIXTURE_PRODUCTS))
    transport._http = mock_client
    transport._owns_http = False
    return transport


def make_kith_adapter(*, products: dict | None = None):
    """Build a ``ShopifyMCPAdapter`` for ``kith.com`` backed by
    :func:`make_kith_transport` — drop-in for ``direct_adapters["kith.com"]``."""
    from adapters.shopify_mcp import ShopifyMCPAdapter

    return ShopifyMCPAdapter(
        "kith.com",
        make_kith_transport(products=products),
        source_protocol="shopify_storefront",
        merchant_display_name="Kith",
    )
