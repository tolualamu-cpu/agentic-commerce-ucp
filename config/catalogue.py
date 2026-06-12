"""Seed catalogues for the three demo merchants.

Each list of dicts is fed directly to ``StubShopifyTransport(seed_products=[...])``.
The fields match Shopify-MCP-style shape (id, title, vendor, available, etc.)
and the adapter normalises them to UCP-vocabulary ``ProductResult`` on the way out.

Catalogue design satisfies the diversity checklist from the plan:
  - ≤ $30  (soft gate)         → Coffee Bar mugs ($14, $18)
  - $100-$500 (explicit gate)  → Athletic Co shoes, Audio Hub mid-tier
  - > $500 (full summary)      → Audio Hub Premium ($649)
  - OUT_OF_STOCK risk flag     → Trail Runner Pro at Athletic Co
  - Cross-merchant overlap     → "wireless earbuds" appears at Audio Hub
                                  AND a "athletic earbuds" at Athletic Co
"""

from __future__ import annotations


# Every image URL is globally unique across the entire catalogue.
# Uses images.unsplash.com/photo-{id} direct CDN format.
ATHLETIC_CO = [
    {
        "id": "ath_001",
        "title": "Demo Running Shoes",
        "price": "129.99",
        "currency": "USD",
        "vendor": "Athletic Co",
        "available": True,
        "rating": 4.5,
        "review_count": 240,
        "description": "Lightweight road running shoes. Cushioned midsole.",
        "attributes": {"category": "running", "type": "shoes"},
        "images": [
            "https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=800&q=80",
            "https://images.unsplash.com/photo-1606107557195-0e29a4b5b4aa?w=800&q=80",
        ],
        "options": ["Size"],
        "variants": [
            {
                "id": f"ath_001-{size}",
                "title": str(size),
                "price": "129.99",
                "available": True,
                "sku": f"ATH-001-{size}",
                "option1": str(size),
                "option2": None,
            }
            for size in (8, 9, 10, 11, 12)
        ],
    },
    {
        "id": "ath_002",
        "title": "Trail Runner Pro",
        "price": "189.00",
        "currency": "USD",
        "vendor": "Athletic Co",
        "available": False,
        "rating": 4.7,
        "review_count": 142,
        "description": "Aggressive lugs, waterproof upper. Currently sold out.",
        "attributes": {"category": "running", "type": "shoes"},
        "images": [
            "https://images.unsplash.com/photo-1582898967731-b5834427fd66?w=800&q=80",
            "https://images.unsplash.com/photo-1460353581641-37baddab0fa2?w=800&q=80",
        ],
        "options": ["Size"],
        "variants": [
            {
                "id": f"ath_002-{size}",
                "title": str(size),
                "price": "189.00",
                "available": False,
                "sku": f"ATH-002-{size}",
                "option1": str(size),
                "option2": None,
            }
            for size in (8, 9, 10, 11, 12)
        ],
    },
    {
        "id": "ath_003",
        "title": "Performance Running Shorts",
        "price": "39.00",
        "currency": "USD",
        "vendor": "Athletic Co",
        "available": True,
        "rating": 4.3,
        "review_count": 88,
        "description": "Moisture-wicking. Built-in liner.",
        "attributes": {"category": "running", "type": "apparel"},
        "images": [
            "https://images.unsplash.com/photo-1602190420103-683df5093e86?w=800&q=80",
            "https://images.unsplash.com/photo-1539794830467-1f1755804d13?w=800&q=80",
        ],
        "options": ["Size", "Color"],
        "variants": [
            {
                "id": f"ath_003-{size}-{color}",
                "title": f"{size} / {color}",
                "price": "39.00",
                "available": True,
                "sku": f"PRS-{size}-{color[:3].upper()}",
                "option1": size,
                "option2": color,
            }
            for size in ("S", "M", "L", "XL")
            for color in ("Black", "Navy")
        ],
    },
    {
        "id": "ath_004",
        "title": "Lightweight Training T-Shirt",
        "price": "24.99",
        "currency": "USD",
        "vendor": "Athletic Co",
        "available": True,
        "rating": 4.2,
        "review_count": 156,
        "description": "Breathable mesh fabric. Quick-dry.",
        "attributes": {"category": "apparel", "type": "shirt"},
        "images": [
            "https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?w=800&q=80",
            "https://images.unsplash.com/photo-1583744946564-b52ac1c389c8?w=800&q=80",
        ],
        "options": ["Size", "Color"],
        "variants": [
            {
                "id": f"ath_004-{size}-{color}",
                "title": f"{size} / {color}",
                "price": "24.99",
                "available": True,
                "sku": f"LTT-{size}-{color[:3].upper()}",
                "option1": size,
                "option2": color,
            }
            for size in ("S", "M", "L", "XL", "XXL")
            for color in ("Black", "White", "Navy")
        ],
    },
    {
        "id": "ath_005",
        "title": "Athletic Wireless Earbuds",
        "price": "79.00",
        "currency": "USD",
        "vendor": "Athletic Co",
        "available": True,
        "rating": 4.0,
        "review_count": 61,
        "description": "Sweat-resistant earbuds for workouts. 6h battery.",
        "attributes": {"category": "electronics", "type": "earbuds"},
        "images": [
            "https://images.unsplash.com/photo-1590658268037-6bf12165a8df?w=800&q=80",
            "https://images.unsplash.com/photo-1598331668826-20cecc596b86?w=800&q=80",
        ],
    },
    {
        "id": "ath_006",
        "title": "Premium Running Shoes",
        "price": "179.00",
        "currency": "USD",
        "vendor": "Athletic Co",
        "available": True,
        "rating": 4.6,
        "review_count": 320,
        "description": "Carbon-plate racing shoe. Premium materials, race-day fit.",
        "attributes": {"category": "running", "type": "shoes"},
        "images": [
            "https://images.unsplash.com/photo-1562183241-b937e95585b6?w=800&q=80",
            "https://images.unsplash.com/photo-1595950653106-6c9ebd614d3a?w=800&q=80",
        ],
        "options": ["Size"],
        "variants": [
            {
                "id": f"ath_006-{size}",
                "title": str(size),
                "price": "179.00",
                "available": True,
                "sku": f"ATH-006-{size}",
                "option1": str(size),
                "option2": None,
            }
            for size in (8, 9, 10, 11, 12, 13)
        ],
    },
    {
        "id": "ath_007",
        "title": "Stability Running Shoes",
        "price": "159.00",
        "currency": "USD",
        "vendor": "Athletic Co",
        "available": True,
        "rating": 4.4,
        "review_count": 240,
        "description": "Medial post for overpronators. All-day comfort.",
        "attributes": {"category": "running", "type": "shoes"},
        "images": [
            "https://images.unsplash.com/photo-1600185365483-26d7a4cc7519?w=800&q=80",
            "https://images.unsplash.com/photo-1491553895911-0055eca6402d?w=800&q=80",
        ],
        "options": ["Size", "Width"],
        "variants": [
            {
                "id": f"ath_007-{size}-{width}",
                "title": f"{size} / {width}",
                "price": "159.00",
                "available": True,
                "sku": f"ATH-007-{size}-{width[:1]}",
                "option1": str(size),
                "option2": width,
            }
            for size in (8, 9, 10, 11, 12)
            for width in ("Standard", "Wide")
        ],
    },
]


AUDIO_HUB = [
    {
        "id": "aud_001",
        "title": "Demo Wireless Headphones",
        "price": "89.00",
        "currency": "USD",
        "vendor": "Audio Hub",
        "available": True,
        "rating": 4.1,
        "review_count": 412,
        "description": "Entry-level over-ear wireless. 20h battery.",
        "attributes": {"category": "electronics", "type": "headphones"},
        "images": [
            "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=800&q=80",
            "https://images.unsplash.com/photo-1524678606370-a47ad25cb82a?w=800&q=80",
        ],
        "options": ["Color"],
        "variants": [
            {
                "id": f"aud_001-{color}",
                "title": color,
                "price": "89.00",
                "available": True,
                "sku": f"AUD-001-{color[:3].upper()}",
                "option1": color,
                "option2": None,
            }
            for color in ("Black", "White", "Blue")
        ],
    },
    {
        "id": "aud_002",
        "title": "Noise-Cancelling Headphones",
        "price": "249.00",
        "currency": "USD",
        "vendor": "Audio Hub",
        "available": True,
        "rating": 4.6,
        "review_count": 1820,
        "description": "Active noise cancelling. 30h battery. Bluetooth 5.3.",
        "attributes": {"category": "electronics", "type": "headphones"},
        "images": [
            "https://images.unsplash.com/photo-1576082712237-eb1335ce23a3?w=800&q=80",
            "https://images.unsplash.com/photo-1546435770-a3e426bf472b?w=800&q=80",
        ],
        "options": ["Color"],
        "variants": [
            {
                "id": f"aud_002-{color}",
                "title": color,
                "price": "249.00",
                "available": True,
                "sku": f"AUD-002-{color[:3].upper()}",
                "option1": color,
                "option2": None,
            }
            for color in ("Black", "Silver")
        ],
    },
    {
        "id": "aud_003",
        "title": "Premium Studio Headphones",
        "price": "649.00",
        "currency": "USD",
        "vendor": "Audio Hub",
        "available": True,
        "rating": 4.8,
        "review_count": 312,
        "description": "Reference-grade studio monitors. Wired + wireless.",
        "attributes": {"category": "electronics", "type": "headphones"},
        "images": [
            "https://images.unsplash.com/photo-1583394838336-acd977736f90?w=800&q=80",
            "https://images.unsplash.com/photo-1487215078519-e21cc028cb29?w=800&q=80",
        ],
    },
    {
        "id": "aud_004",
        "title": "Wireless Earbuds Compact",
        "price": "129.00",
        "currency": "USD",
        "vendor": "Audio Hub",
        "available": True,
        "rating": 4.4,
        "review_count": 740,
        "description": "Tiny case. ANC. Multi-device pairing.",
        "attributes": {"category": "electronics", "type": "earbuds"},
        "images": [
            "https://images.unsplash.com/photo-1756902368926-eb9e5e9d2a69?w=800&q=80",
            "https://images.unsplash.com/photo-1606220945770-b5b6c2c55bf1?w=800&q=80",
        ],
        "options": ["Color"],
        "variants": [
            {
                "id": f"aud_004-{color}",
                "title": color,
                "price": "129.00",
                "available": True,
                "sku": f"AUD-004-{color[:3].upper()}",
                "option1": color,
                "option2": None,
            }
            for color in ("Black", "White")
        ],
    },
    {
        "id": "aud_005",
        "title": "Bluetooth Speaker Mini",
        "price": "59.00",
        "currency": "USD",
        "vendor": "Audio Hub",
        "available": True,
        "rating": 4.3,
        "review_count": 1102,
        "description": "Portable speaker. 12h battery. IPX7.",
        "attributes": {"category": "electronics", "type": "speaker"},
        "images": [
            "https://images.unsplash.com/photo-1608043152269-423dbba4e7e1?w=800&q=80",
            "https://images.unsplash.com/photo-1545454675-3531b543be5d?w=800&q=80",
        ],
        "options": ["Color"],
        "variants": [
            {
                "id": f"aud_005-{color}",
                "title": color,
                "price": "59.00",
                "available": True,
                "sku": f"AUD-005-{color[:3].upper()}",
                "option1": color,
                "option2": None,
            }
            for color in ("Black", "Red", "Teal")
        ],
    },
    {
        "id": "aud_006",
        "title": "Studio Headphones Mid-Range",
        "price": "179.00",
        "currency": "USD",
        "vendor": "Audio Hub",
        "available": True,
        "rating": 4.4,
        "review_count": 612,
        "description": "Over-ear, wired. Detachable cable. Studio-grade drivers.",
        "attributes": {"category": "electronics", "type": "headphones"},
        "images": [
            "https://images.unsplash.com/photo-1420161900862-9a86fa1f5c79?w=800&q=80",
            "https://images.unsplash.com/photo-1493723843671-1d655e66ac1c?w=800&q=80",
        ],
    },
    {
        "id": "aud_007",
        "title": "Sport Wireless Earbuds",
        "price": "99.00",
        "currency": "USD",
        "vendor": "Audio Hub",
        "available": True,
        "rating": 4.2,
        "review_count": 540,
        "description": "Sweat-resistant. Secure-fit ear hooks. 8h battery.",
        "attributes": {"category": "electronics", "type": "earbuds"},
        "images": [
            "https://images.unsplash.com/photo-1758521960921-7d5eab7cf50a?w=800&q=80",
            "https://images.unsplash.com/photo-1525825691042-e14d9042fc70?w=800&q=80",
        ],
        "options": ["Color"],
        "variants": [
            {
                "id": f"aud_007-{color}",
                "title": color,
                "price": "99.00",
                "available": True,
                "sku": f"AUD-007-{color[:3].upper()}",
                "option1": color,
                "option2": None,
            }
            for color in ("Black", "Orange")
        ],
    },
]


COFFEE_BAR = [
    {
        "id": "cof_001",
        "title": "Ceramic Coffee Mug",
        "price": "14.00",
        "currency": "USD",
        "vendor": "Coffee Bar",
        "available": True,
        "rating": 4.4,
        "review_count": 89,
        "description": "12oz ceramic mug. Dishwasher safe.",
        "attributes": {"category": "lifestyle", "type": "mug"},
        "images": [
            "https://images.unsplash.com/photo-1509042239860-f550ce710b93?w=800&q=80",
            "https://images.unsplash.com/photo-1572119865084-43c285814d63?w=800&q=80",
        ],
    },
    {
        "id": "cof_002",
        "title": "Travel Coffee Tumbler",
        "price": "28.00",
        "currency": "USD",
        "vendor": "Coffee Bar",
        "available": True,
        "rating": 4.6,
        "review_count": 312,
        "description": "Vacuum-insulated 16oz. Keeps coffee hot 6h.",
        "attributes": {"category": "lifestyle", "type": "tumbler"},
        "images": [
            "https://images.unsplash.com/photo-1530138295342-c7c921529ee9?w=800&q=80",
            "https://images.unsplash.com/photo-1588793076577-4c2b666452d3?w=800&q=80",
        ],
        "options": ["Size"],
        "variants": [
            {
                "id": "cof_002-16oz",
                "title": "16oz",
                "price": "28.00",
                "available": True,
                "sku": "COF-002-16",
                "option1": "16oz",
                "option2": None,
            },
            {
                "id": "cof_002-20oz",
                "title": "20oz",
                "price": "32.00",
                "available": True,
                "sku": "COF-002-20",
                "option1": "20oz",
                "option2": None,
            },
        ],
    },
    {
        "id": "cof_003",
        "title": "Single-Origin Coffee Beans (Ethiopia)",
        "price": "18.00",
        "currency": "USD",
        "vendor": "Coffee Bar",
        "available": True,
        "rating": 4.7,
        "review_count": 624,
        "description": "Light roast Ethiopian Yirgacheffe. 12oz whole bean.",
        "attributes": {"category": "lifestyle", "type": "coffee"},
        "images": [
            "https://images.unsplash.com/photo-1447933601403-0c6688de566e?w=800&q=80",
            "https://images.unsplash.com/photo-1511537190424-bbbab87ac5eb?w=800&q=80",
        ],
        "options": ["Size"],
        "variants": [
            {
                "id": "cof_003-12oz",
                "title": "12oz",
                "price": "18.00",
                "available": True,
                "sku": "COF-003-12",
                "option1": "12oz",
                "option2": None,
            },
            {
                "id": "cof_003-16oz",
                "title": "16oz",
                "price": "22.00",
                "available": True,
                "sku": "COF-003-16",
                "option1": "16oz",
                "option2": None,
            },
            {
                "id": "cof_003-2lb",
                "title": "2lb",
                "price": "28.00",
                "available": True,
                "sku": "COF-003-2LB",
                "option1": "2lb",
                "option2": None,
            },
        ],
    },
    {
        "id": "cof_004",
        "title": "Pour-Over Coffee Set",
        "price": "42.00",
        "currency": "USD",
        "vendor": "Coffee Bar",
        "available": True,
        "rating": 4.5,
        "review_count": 178,
        "description": "Glass dripper + carafe. 2-cup capacity.",
        "attributes": {"category": "lifestyle", "type": "brewing"},
        "images": [
            "https://images.unsplash.com/photo-1541469406036-71229832e06e?w=800&q=80",
            "https://images.unsplash.com/photo-1442512595331-e89e73853f31?w=800&q=80",
        ],
    },
    {
        "id": "cof_005",
        "title": "Decaf Coffee Beans",
        "price": "16.00",
        "currency": "USD",
        "vendor": "Coffee Bar",
        "available": True,
        "rating": 4.2,
        "review_count": 98,
        "description": "Swiss-water-process decaf. 12oz.",
        "attributes": {"category": "lifestyle", "type": "coffee"},
        "images": [
            "https://images.unsplash.com/photo-1611854779393-1b2da9d400fe?w=800&q=80",
            "https://images.unsplash.com/photo-1753837787691-84a06d715d24?w=800&q=80",
        ],
        "options": ["Size"],
        "variants": [
            {
                "id": "cof_005-12oz",
                "title": "12oz",
                "price": "16.00",
                "available": True,
                "sku": "COF-005-12",
                "option1": "12oz",
                "option2": None,
            },
            {
                "id": "cof_005-16oz",
                "title": "16oz",
                "price": "20.00",
                "available": True,
                "sku": "COF-005-16",
                "option1": "16oz",
                "option2": None,
            },
            {
                "id": "cof_005-2lb",
                "title": "2lb",
                "price": "26.00",
                "available": True,
                "sku": "COF-005-2LB",
                "option1": "2lb",
                "option2": None,
            },
        ],
    },
    {
        "id": "cof_006",
        "title": "Ceramic Coffee Mug (Large 16oz)",
        "price": "19.00",
        "currency": "USD",
        "vendor": "Coffee Bar",
        "available": True,
        "rating": 4.6,
        "review_count": 312,
        "description": "16oz wide-mouth ceramic mug. Microwave + dishwasher safe.",
        "attributes": {"category": "lifestyle", "type": "mug"},
        "images": [
            "https://images.unsplash.com/photo-1514432324607-a09d9b4aefdd?w=800&q=80",
            "https://images.unsplash.com/photo-1534040385115-33dcb3acba5b?w=800&q=80",
        ],
    },
    {
        "id": "cof_007",
        "title": "Stoneware Coffee Mug Set of 2",
        "price": "24.00",
        "currency": "USD",
        "vendor": "Coffee Bar",
        "available": True,
        "rating": 4.3,
        "review_count": 156,
        "description": "Set of two matching 12oz stoneware mugs. Hand-finished glaze.",
        "attributes": {"category": "lifestyle", "type": "mug"},
        "images": [
            "https://images.unsplash.com/photo-1520485521983-bfaa0bc6c80e?w=800&q=80",
            "https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=800&q=80",
        ],
    },
    {
        "id": "cof_008",
        "title": "Insulated Coffee Mug",
        "price": "22.00",
        "currency": "USD",
        "vendor": "Coffee Bar",
        "available": True,
        "rating": 4.5,
        "review_count": 198,
        "description": "Double-wall vacuum insulation. Keeps coffee hot for 4 hours.",
        "attributes": {"category": "lifestyle", "type": "mug"},
        "images": [
            "https://images.unsplash.com/photo-1604713055037-ef1ec567a47b?w=800&q=80",
            "https://images.unsplash.com/photo-1605539582747-ce302b9afca2?w=800&q=80",
        ],
    },
    {
        "id": "cof_009",
        "title": "Single-Origin Coffee Beans (Colombia)",
        "price": "17.00",
        "currency": "USD",
        "vendor": "Coffee Bar",
        "available": True,
        "rating": 4.5,
        "review_count": 412,
        "description": "Medium roast Colombian Huila. Notes of caramel and chocolate. 12oz.",
        "attributes": {"category": "lifestyle", "type": "coffee"},
        "images": [
            "https://images.unsplash.com/photo-1525088553748-01d6e210e00b?w=800&q=80",
            "https://images.unsplash.com/photo-1497515114629-f71d768fd07c?w=800&q=80",
        ],
        "options": ["Size"],
        "variants": [
            {
                "id": "cof_009-12oz",
                "title": "12oz",
                "price": "17.00",
                "available": True,
                "sku": "COF-009-12",
                "option1": "12oz",
                "option2": None,
            },
            {
                "id": "cof_009-16oz",
                "title": "16oz",
                "price": "21.00",
                "available": True,
                "sku": "COF-009-16",
                "option1": "16oz",
                "option2": None,
            },
            {
                "id": "cof_009-2lb",
                "title": "2lb",
                "price": "27.00",
                "available": True,
                "sku": "COF-009-2LB",
                "option1": "2lb",
                "option2": None,
            },
        ],
    },
    {
        "id": "cof_010",
        "title": "Single-Origin Coffee Beans (Kenya)",
        "price": "19.00",
        "currency": "USD",
        "vendor": "Coffee Bar",
        "available": True,
        "rating": 4.6,
        "review_count": 287,
        "description": "Light-medium roast Kenya AA. Bright acidity, blackcurrant notes. 12oz.",
        "attributes": {"category": "lifestyle", "type": "coffee"},
        "images": [
            "https://images.unsplash.com/photo-1422207109431-97544339f995?w=800&q=80",
            "https://images.unsplash.com/photo-1459755486867-b55449bb39ff?w=800&q=80",
        ],
        "options": ["Size"],
        "variants": [
            {
                "id": "cof_010-12oz",
                "title": "12oz",
                "price": "19.00",
                "available": True,
                "sku": "COF-010-12",
                "option1": "12oz",
                "option2": None,
            },
            {
                "id": "cof_010-16oz",
                "title": "16oz",
                "price": "23.00",
                "available": True,
                "sku": "COF-010-16",
                "option1": "16oz",
                "option2": None,
            },
            {
                "id": "cof_010-2lb",
                "title": "2lb",
                "price": "29.00",
                "available": True,
                "sku": "COF-010-2LB",
                "option1": "2lb",
                "option2": None,
            },
        ],
    },
]


MERCHANTS: dict[str, list[dict]] = {
    "athletic-co.myshopify.com": ATHLETIC_CO,
    "audio-hub.myshopify.com": AUDIO_HUB,
    "coffee-bar.myshopify.com": COFFEE_BAR,
}

# Storefront display names for the demo merchants. Keyed by merchant_domain.
# Used by the adapter to set ``ProductResult.merchant`` to the STOREFRONT name
# (never the brand/vendor). The "Buy on {{ merchant }}" badge across the UI
# reads this — for demo products the vendor field on each seed dict happens
# to equal the storefront name, but we never rely on that coincidence for
# real merchants like Kith where brand != storefront.
DEMO_MERCHANT_DISPLAY_NAMES: dict[str, str] = {
    "athletic-co.myshopify.com": "Athletic Co",
    "audio-hub.myshopify.com": "Audio Hub",
    "coffee-bar.myshopify.com": "Coffee Bar",
}


# ── Live merchants (real Shopify stores fetched at runtime) ──────────────
# Each entry maps a domain to its Shopify storefront URL. Products are
# fetched from {store_url}/products.json and cached in LiveShopifyTransport.
# These merchants coexist with the demo stubs above.

LIVE_MERCHANTS: dict[str, dict] = {
    "kith.com": {
        "store_url": "https://kith.com",
        "display_name": "Kith",
        "logo_url": "https://kith.com/cdn/shop/files/favicon3_32x32.png?v=1613503289",
        # Kith has 1000+ products across 20+ pages (50/page). Fetch 20 pages
        # so the agent sees the full catalog; cache_ttl keeps re-fetches rare.
        "max_pages": 20,
    },
}
