"""Product-family grouping (variant-as-separate-listing dedup).

Some merchants (notably Kith) model what a shopper considers "one product"
as multiple distinct Shopify listings (distinct ``product_id``/handle) that
differ only by ONE OR MORE variant dimensions — most commonly color, but the
same split pattern applies to material, finish, roast/flavor, capacity,
edition, etc. — each split listing carrying its OWN further variants (e.g.
each color-listing has its own Size variants).

``group_into_families`` collapses these split listings into a single
``ProductFamily`` per real-world product, synthesizing a combined
``option_names``/``variants`` matrix so the UI shows ONE card per family
(per the standing "one card per product family" rule) while still letting a
shopper pick "White, size M" even though "White" lives on a sibling Shopify
product.

Families of size 1 (the common case for demo merchants and most products)
pass through unchanged.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from config.variant_vocabulary import SUFFIX_PATTERNS, VARIANT_VOCABULARY
from models.product import ProductResult, ProductVariant


class ProductFamily(BaseModel):
    """A group of one or more ``ProductResult`` listings that represent the
    same real-world product, possibly split across one or more variant
    dimensions by the merchant's catalogue."""

    primary: ProductResult
    members: list[ProductResult] = Field(default_factory=list)
    option_names: list[str] = Field(default_factory=list)
    variants: list[ProductVariant] = Field(default_factory=list)


def _strip_dimension_suffixes(title: str) -> tuple[str, dict[str, str]]:
    """Strip every recognized trailing variant-dimension suffix from
    ``title``. Returns ``(normalized_title, {dimension_name: value, ...})``.

    Longer vocabulary values are tried before shorter ones (e.g. "Light
    Roast" before "Roast") so multi-word values aren't partially matched.
    Multiple dimensions can be stripped from one title (e.g. a listing
    split by both Material AND Color: "... - Suede - Black").
    """
    stripped: dict[str, str] = {}
    remaining = title.rstrip()
    vocab_by_length = sorted(VARIANT_VOCABULARY.items(), key=lambda kv: -len(kv[0]))

    changed = True
    while changed:
        changed = False
        for value, (dimension, canonical) in vocab_by_length:
            for pattern in SUFFIX_PATTERNS:
                suffix = pattern.format(value=value)
                if remaining.endswith(suffix) and len(remaining) > len(suffix):
                    remaining = remaining[: -len(suffix)].rstrip()
                    stripped[dimension] = canonical
                    changed = True
                    break
            if changed:
                break

    return remaining, stripped


def _grouping_key(product: ProductResult) -> tuple[tuple[str, str, str], dict[str, str]]:
    normalized_title, stripped = _strip_dimension_suffixes(product.name)
    key = (
        product.merchant_domain,
        normalized_title.strip().lower(),
        (product.brand or "").strip().lower(),
    )
    return key, stripped


def _synthesize_variant(member: ProductResult, stripped: dict[str, str]) -> ProductVariant:
    """Build a single-member synthetic variant for a member with no
    variants of its own — its only "variant" is the dimension(s) stripped
    from its title (e.g. a single-SKU "Crewneck - Black" listing)."""
    return ProductVariant(
        variant_id=f"{member.product_id}:{member.product_id}",
        sku=None,
        options=dict(stripped),
        price=member.price,
        in_stock=member.in_stock,
        image=member.images[0] if member.images else None,
    )


def group_into_families(products: list[ProductResult]) -> list[ProductFamily]:
    """Group ``products`` into ``ProductFamily`` entries.

    Grouping key: ``(merchant_domain, normalized_title, brand)`` where
    ``normalized_title`` has every recognized variant-dimension suffix
    stripped (per :mod:`config.variant_vocabulary`). Products whose
    grouping keys match are members of one family.

    - A family of 1 with no stripped dimensions passes through unchanged
      (``option_names``/``variants`` = the product's own).
    - A family of 1 WITH a stripped dimension (a standalone listing whose
      title happens to end in a recognized variant word, but with no
      siblings) ALSO passes through unchanged — there's nothing to merge.
    - A family of >1 members synthesizes ``option_names`` as the union of
      each member's own ``option_names`` plus every dimension stripped
      during grouping, and ``variants`` as the cross product: each member's
      variants get the stripped dimension(s) added to their ``options``
      dict, with ``variant_id`` rewritten to
      ``f"{member.product_id}:{member_variant.variant_id}"`` so add-to-cart
      can trace back to the correct underlying product+variant. Members
      with no variants of their own contribute one synthetic variant.
    """
    groups: dict[tuple[str, str, str], list[tuple[ProductResult, dict[str, str]]]] = {}
    order: list[tuple[str, str, str]] = []

    for product in products:
        key, stripped = _grouping_key(product)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((product, stripped))

    families: list[ProductFamily] = []
    for key in order:
        members_with_stripped = groups[key]

        if len(members_with_stripped) == 1:
            product, _stripped = members_with_stripped[0]
            families.append(
                ProductFamily(
                    primary=product,
                    members=[product],
                    option_names=list(product.option_names),
                    variants=list(product.variants),
                )
            )
            continue

        members = [m for m, _ in members_with_stripped]
        representative = min(members, key=lambda m: m.product_id)

        # The merged card represents the FAMILY, not one member's specific
        # variant — strip that member's own dimension suffix from the
        # display name (e.g. "Kith Logo Crewneck - Black" -> "Kith Logo
        # Crewneck") so the card never implies a single color/material/etc.
        normalized_name, _ = _strip_dimension_suffixes(representative.name)
        primary = representative.model_copy(update={"name": normalized_name})

        option_names = list(primary.option_names)
        for _member, stripped in members_with_stripped:
            for dimension in stripped:
                if dimension not in option_names:
                    option_names.append(dimension)

        variants: list[ProductVariant] = []
        for member, stripped in members_with_stripped:
            if member.variants:
                for member_variant in member.variants:
                    options = dict(member_variant.options)
                    options.update(stripped)
                    price = (
                        member_variant.price if member_variant.price is not None else member.price
                    )
                    image = member_variant.image or (member.images[0] if member.images else None)
                    variants.append(
                        ProductVariant(
                            variant_id=f"{member.product_id}:{member_variant.variant_id}",
                            sku=member_variant.sku,
                            options=options,
                            price=price,
                            in_stock=member_variant.in_stock,
                            image=image,
                        )
                    )
            else:
                variants.append(_synthesize_variant(member, stripped))

        families.append(
            ProductFamily(
                primary=primary,
                members=members,
                option_names=option_names,
                variants=variants,
            )
        )

    return families
