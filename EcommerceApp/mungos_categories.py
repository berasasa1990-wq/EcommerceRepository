"""Explicit integration-only catalog rules. Never edit webshop taxonomy.

Exact aliases are reviewed catalog names, not fuzzy classification. A None rule
is an explicit review barrier and must not inherit an unrelated ancestor.
"""
CATEGORY_PREFIX = 'SportRecreation_Equipment_FishingEquipment_'
CATEGORY_CODES = {
    'štapovi': 'FishingRods', 'mašinice': 'Reels',
    'najloni i strune': 'FishingLinesLeaders', 'udice': 'Hooks',
    'varalice': 'Lures', 'plovci': 'FloatsBobbers', 'hranilice': 'Feeders',
    'mušičarski program': 'FlyFishingGear', 'primama i mamci': 'GroundbaitsBaits',
    'dodatna oprema': 'Accessories', 'odjeća i obuća za ribolov': 'FishingWear',
}
CONFIRMED_CATEGORY_CODES = frozenset(
    [CATEGORY_PREFIX.rstrip('_')] + [CATEGORY_PREFIX + suffix for suffix in CATEGORY_CODES.values()]
)
EXPLICIT_CATEGORY_MAPPING = {
    name: CATEGORY_PREFIX + suffix for name, suffix in CATEGORY_CODES.items()
}
EXPLICIT_CATEGORY_MAPPING.update({
    'oprema za ribolov': CATEGORY_PREFIX.rstrip('_'),
    'udice za ribolov': CATEGORY_PREFIX + 'Hooks',
    'trokuke': CATEGORY_PREFIX + 'Hooks',
    'garderoba': CATEGORY_PREFIX + 'FishingWear',
    'kačketi': CATEGORY_PREFIX + 'FishingWear',
    'virble i kopče': CATEGORY_PREFIX + 'Accessories',
    'feeder stapovi': CATEGORY_PREFIX + 'FishingRods',
    'saranski stapovi': CATEGORY_PREFIX + 'FishingRods',
    'kape i kacketi test': CATEGORY_PREFIX + 'FishingWear',
    'stalci za spod test': CATEGORY_PREFIX + 'Accessories',
    'stalci za spod prod': CATEGORY_PREFIX + 'Accessories',
    'stapovi za spod test': CATEGORY_PREFIX + 'FishingRods',
    'stapovi spod prod': CATEGORY_PREFIX + 'FishingRods',
    'varalice more test': CATEGORY_PREFIX + 'Lures',
    'varalice single test': CATEGORY_PREFIX + 'Lures',
})


# Production IDs are paired with reviewed names so local ID collisions cannot
# silently classify unrelated categories. Child overrides take precedence.
PRODUCTION_CATEGORY_ID_MAPPING = {
    143: ('udice i sitni pribor', CATEGORY_PREFIX + 'Hooks'),
    157: ('feeder oprema', CATEGORY_PREFIX + 'Feeders'),
    162: ('kutije i torble', CATEGORY_PREFIX + 'Accessories'),
    167: ('oprema', CATEGORY_PREFIX + 'Accessories'),
    173: ('mamci', CATEGORY_PREFIX + 'GroundbaitsBaits'),
    181: ('odjeća i obuća', CATEGORY_PREFIX + 'FishingWear'),
    191: ('kamp', None),
}
PRODUCTION_CATEGORY_OVERRIDES = {
    'plovci za ribolov': CATEGORY_PREFIX + 'FloatsBobbers',
    **{name: CATEGORY_PREFIX + 'Accessories' for name in (
        'vrtilice i kopče', 'igle i alati', 'ribolovni sistemi',
        'krimp i split ring', 'olovo za ribolov', 'sajlice za grabljivice',
        'stoperi', 'rakete i kobre',
    )},
    **{name: None for name in (
        'naočare', 'kamp', 'šatori', 'stolice', 'upaljaci', 'lampe',
        'noževi', 'vreće za spavanje', 'kuhinja i kamp program',
        'baterije', 'suncobrani', 'kreveti', 'stolići',
    )},
}
EXPLICIT_CATEGORY_MAPPING.update(PRODUCTION_CATEGORY_OVERRIDES)


def resolve_category(category):
    """Return confirmed code and source ancestor; cycles fail closed."""
    visited = set()
    while category is not None and category.pk not in visited:
        visited.add(category.pk)
        name = category.naziv.strip().casefold()
        if name in EXPLICIT_CATEGORY_MAPPING:
            code = EXPLICIT_CATEGORY_MAPPING[name]
            return (code if code in CONFIRMED_CATEGORY_CODES else None), category.pk
        rule = PRODUCTION_CATEGORY_ID_MAPPING.get(category.pk)
        if rule is not None and name == rule[0]:
            code = rule[1]
            return (code if code in CONFIRMED_CATEGORY_CODES else None), category.pk
        category = category.roditelj
    return None, None


def category_code(category):
    return resolve_category(category)[0]
