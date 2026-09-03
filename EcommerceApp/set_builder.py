"""Kreiraj svoj set — preporuka artikala po vrsti ribolova, nivou, slotovima i budžetu."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Q

from .models import AdvisorBeginnerFishType, Category, Product


FISH_TYPES = (
    ('saranski', 'Šaranski ribolov', 'saranski.jpg'),
    ('feeder', 'Feeder', 'feeder.jpg'),
    ('varalicarski', 'Varaličarenje', 'varalicarski.jpg'),
    ('plovak', 'Plovak', 'plovak.jpg'),
    ('som', 'Som', 'som.jpg'),
    ('morski', 'Morski ribolov', 'morski.jpg'),
)

TIERS = (
    ('pristupacno', 'Pristupačno', 'Najbolja oprema za što manje novca.', 0.55),
    ('preporuka', 'Preporuka', 'Najbolji odnos cijene i kvaliteta.', 0.82),
    ('premium', 'Premium', 'Kvalitetnija oprema i jači brendovi.', 0.96),
)

SLOTS = (
    ('stap', 'Štap'),
    ('masinica', 'Mašinica'),
    ('najlon', 'Najlon / struna'),
    ('udice', 'Udice'),
    ('predvez', 'Predvez'),
    ('torba', 'Torba'),
    ('hranilice', 'Hranilice'),
    ('stalak', 'Stalak'),
    ('meredov', 'Meredov'),
    ('ostalo', 'Ostali pribor'),
)

DEFAULT_SLOTS = ('stap', 'masinica', 'najlon', 'udice')

SLOT_WEIGHT = {
    'stap': 0.38,
    'masinica': 0.32,
    'najlon': 0.07,
    'udice': 0.07,
    'predvez': 0.04,
    'torba': 0.06,
    'hranilice': 0.05,
    'stalak': 0.05,
    'meredov': 0.04,
    'ostalo': 0.04,
}

# Traži se po kategoriji (naziv/slug + podkategorije), zatim po cijeni slota.
SLOT_CATEGORY_TERMS = {
    'stap': ('štap', 'stapov', 'stapovi', 'prut'),
    'masinica': ('mašinic', 'masinic', 'masince', 'reel', 'rola', 'role'),
    'najlon': ('najlon', 'strun', 'pletenic', 'fluorocarbon'),
    'udice': ('udic', 'hook'),
    'predvez': ('predvez', 'leader', 'rig'),
    'torba': ('torb', 'ruksak', 'ranac', 'carryall'),
    'hranilice': ('hranilic', 'method feeder', 'feeder hranil'),
    'stalak': ('stalak', 'stalci', 'rod pod', 'rodpod'),
    'meredov': ('meredov', 'podmet', 'landing net'),
    'ostalo': ('pribor', 'ostalo'),
}

SLOT_NAME_TERMS = {
    'stap': ('štap', 'stap ', 'stapovi', 'prut', 'carp rod', 'feeder rod', 'spin rod', 'picker', 'teleskop', '2sec', '3sec', '2 sec', '3 sec'),
    'masinica': ('masinica', 'mašinica', ' reel', 'reel ', 'baitrunner'),
    'najlon': ('najlon', 'fluorocarbon', 'pletenic', 'braid', 'struna'),
    'udice': ('udica', 'udice', 'hook'),
    'predvez': ('predvez', 'leader', 'hooklink'),
    'torba': ('torba', 'ruksak', 'ranac', 'carryall'),
    'hranilice': ('hranilic', 'method feeder', 'cage feeder'),
    'stalak': ('stalak', 'rod pod', 'buzz bar'),
    'meredov': ('meredov', 'landing net', 'podmet'),
    'ostalo': (),
}

SLOT_NAME_EXCLUDE = {
    'stap': ('shad', 'kacket', 'kačket', 'vest', 'varalic', 'udic', 'blist', 'masinic', 'reel', ' fd', 'baitrunner'),
    'masinica': ('rucic', 'ručic', 'case', 'futrol', 'neoprene', 'handle'),
    'najlon': ('masinic', 'štap', 'stap '),
    'udice': ('masinic', 'štap'),
}

FISH_KEYWORDS = {
    'saranski': ('saran', 'carp', 'šaran'),
    'feeder': ('feeder',),
    'varalicarski': ('varalic', 'spin', 'wobbl', 'jig', 'štuka', 'stuka', 'smuđ'),
    'plovak': ('plovak', 'match', 'float'),
    'som': ('som', 'catfish', 'wels'),
    'morski': ('morsk', 'sea', 'boat'),
}

BUDGET_MIN = 50
BUDGET_MAX = 1500
BUDGET_PRESETS = (150, 250, 400, 600, 1000)


def _money(value):
    try:
        return Decimal(str(value or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal('0.00')


def _display(value):
    return f'{_money(value)} KM'.replace('.', ',')


def _product_image(product, request=None):
    img = getattr(product, 'slika', None)
    if not img:
        return ''
    try:
        url = img.url
    except Exception:
        return ''
    if request:
        return request.build_absolute_uri(url)
    return url


def serialize_product(product, request=None, slot='', quantity=1):
    price = _money(getattr(product, 'prikazna_cijena', 0))
    return {
        'id': product.pk,
        'name': product.naziv,
        'slug': product.slug,
        'url': product.get_absolute_url(),
        'price': str(price),
        'price_display': _display(price),
        'image': _product_image(product, request),
        'slot': slot,
        'slot_label': dict(SLOTS).get(slot, ''),
        'quantity': int(quantity or 1),
        'in_stock': bool(getattr(product, 'na_stanju', False)),
    }


def _text_blob(product):
    kat = getattr(product, 'kategorija', None)
    return ' '.join((
        (getattr(product, 'naziv', '') or ''),
        (getattr(product, 'opis', '') or '')[:180],
        (getattr(kat, 'naziv', '') or '') if kat else '',
        (getattr(kat, 'slug', '') or '') if kat else '',
    )).lower()


def product_slot(product):
    kat = getattr(product, 'kategorija', None)
    kn = ((getattr(kat, 'naziv', '') or '') + ' ' + (getattr(kat, 'slug', '') or '')).lower()
    text = _text_blob(product)
    for slot, terms in SLOT_CATEGORY_TERMS.items():
        if slot == 'ostalo':
            continue
        if any(t in kn for t in terms):
            return slot
    for slot, terms in SLOT_NAME_TERMS.items():
        if slot == 'ostalo':
            continue
        if any(t in text for t in terms):
            banned = SLOT_NAME_EXCLUDE.get(slot) or ()
            if any(b in text for b in banned):
                continue
            return slot
    return 'ostalo'


def _in_stock_qs():
    return (
        Product.objects.filter(aktivan=True, sakriven_do_stanja=False, na_stanju=True, stanje__gt=0)
        .exclude(naziv__icontains='gift card')
        .exclude(naziv__icontains='testni')
        .select_related('kategorija', 'brend')
    )


def _category_ids_for_slot(slot, fish_code=''):
    """Kategorije (i podkategorije) koje odgovaraju slotu; ribolov suzi ako postoji."""
    terms = SLOT_CATEGORY_TERMS.get(slot) or ()
    if not terms:
        return [], []
    fish_terms = FISH_KEYWORDS.get(fish_code) or ()
    all_ids = []
    fish_ids = []
    cats = list(Category.objects.filter(aktivan=True).prefetch_related('podkategorije'))
    for cat in cats:
        blob = f'{cat.naziv} {cat.slug}'.lower()
        if not any(term in blob for term in terms):
            continue
        ids = cat.get_descendant_ids()
        all_ids.extend(ids)
        if fish_terms and any(term in blob for term in fish_terms):
            fish_ids.extend(ids)
    return list(dict.fromkeys(fish_ids)), list(dict.fromkeys(all_ids))


def _name_q_for_slot(slot):
    q = Q()
    for term in SLOT_NAME_TERMS.get(slot) or ():
        q |= Q(naziv__icontains=term.strip())
    return q


def _apply_name_excludes(qs, slot):
    for term in SLOT_NAME_EXCLUDE.get(slot) or ():
        qs = qs.exclude(naziv__icontains=term)
    return qs


def _narrow_slot_qs(qs, slot):
    """Unutar kategorije zadrži artikle čiji naziv odgovara slotu, ako ih ima."""
    qs = _apply_name_excludes(qs, slot)
    name_q = _name_q_for_slot(slot)
    if name_q:
        named = qs.filter(name_q)
        if named.exists():
            return named
    return qs


def _slot_queryset(slot, fish_code=''):
    """Artikli iz kategorije slota (npr. Mašinice). Ako kategorije nema, naziv."""
    qs = _in_stock_qs()
    fish_ids, all_ids = _category_ids_for_slot(slot, fish_code)
    name_q = _name_q_for_slot(slot)
    cat_ids = fish_ids or all_ids
    if cat_ids:
        cat_qs = _narrow_slot_qs(qs.filter(kategorija_id__in=cat_ids), slot)
        if cat_qs.count() >= 3 or not name_q:
            return cat_qs
        extra = _apply_name_excludes(qs.filter(name_q), slot)
        return (cat_qs | extra).distinct()
    if name_q:
        return _apply_name_excludes(qs.filter(name_q), slot)
    return qs.none()


def _fish_score(product, fish_code):
    keys = FISH_KEYWORDS.get(fish_code) or (fish_code,)
    text = _text_blob(product)
    return sum(4 if k in text else 0 for k in keys)


def _pick_for_slot(slot, *, fish_code, budget_left, target, exclude_ids):
    qs = _slot_queryset(slot, fish_code).exclude(pk__in=exclude_ids)
    cheap_ok = slot in ('udice', 'predvez', 'najlon', 'hranilice')
    min_price = Decimal('0.50') if cheap_ok else max(Decimal('8.00'), target * Decimal('0.12'))
    candidates = []
    for product in qs[:280]:
        price = _money(product.prikazna_cijena)
        if price <= 0 or price > budget_left:
            continue
        if price < min_price and budget_left >= min_price:
            continue
        score = _fish_score(product, fish_code)
        distance = abs(float(price) - float(target))
        candidates.append((score, distance, float(price), product, price))
    if not candidates:
        return None, Decimal('0')
    candidates.sort(key=lambda row: (-row[0], row[1], row[2]))
    product = candidates[0][3]
    return product, candidates[0][4]


def _kits_for_fish(fish_code):
    codes = [fish_code]
    if fish_code == 'saranski':
        codes.append('saran')
    try:
        types = list(
            AdvisorBeginnerFishType.objects.filter(aktivan=True, code__in=codes)
            .prefetch_related('setovi__stavke__product__kategorija')
        )
    except Exception:
        return []
    kits = []
    for ft in types:
        for kit in ft.setovi.all():
            if not kit.aktivan:
                continue
            kits.append(kit)
    return kits


def _kit_items(kit, slots):
    wanted = set(slots)
    items = []
    for stavka in kit.stavke.all():
        product = stavka.product
        if not product or not product.aktivan or not product.na_stanju:
            continue
        slot = product_slot(product)
        if slot not in wanted and 'ostalo' not in wanted:
            continue
        if slot not in wanted:
            slot = 'ostalo'
        items.append((slot, product, max(1, int(stavka.kolicina or 1))))
    return items


def build_set(*, fish_code, tier, slots, budget, request=None, exclude_ids=None):
    fish_code = (fish_code or 'feeder').strip().lower()
    tier = (tier or 'preporuka').strip().lower()
    if tier not in {k for k, _n, _d, _f in TIERS}:
        tier = 'preporuka'
    slots = [s for s in (slots or list(DEFAULT_SLOTS)) if s in dict(SLOTS)]
    if not slots:
        slots = list(DEFAULT_SLOTS)
    budget = max(BUDGET_MIN, min(BUDGET_MAX, int(budget or 300)))
    budget_dec = _money(budget)
    exclude_ids = set(exclude_ids or ())
    factor = dict((k, f) for k, _n, _d, f in TIERS)[tier]
    spend_target = budget_dec * Decimal(str(factor))

    chosen = []
    used_slots = set()
    remaining = spend_target

    kits = _kits_for_fish(fish_code)
    if kits:
        kits_sorted = sorted(kits, key=lambda k: k.snizeni_iznos())
        if tier == 'pristupacno':
            kit = kits_sorted[0]
        elif tier == 'premium':
            kit = kits_sorted[-1]
        else:
            kit = kits_sorted[len(kits_sorted) // 2]
        for slot, product, qty in _kit_items(kit, slots):
            price = _money(product.prikazna_cijena) * qty
            if remaining - price < Decimal('-5'):
                continue
            chosen.append((slot, product, qty, price))
            used_slots.add(slot)
            remaining -= price
            exclude_ids.add(product.pk)

    weights = {s: SLOT_WEIGHT.get(s, 0.04) for s in slots if s not in used_slots}
    total_w = sum(weights.values()) or 1
    for slot in slots:
        if slot in used_slots:
            continue
        share = Decimal(str(weights.get(slot, 0.04) / total_w))
        target = remaining * share
        product, price = _pick_for_slot(
            slot,
            fish_code=fish_code,
            budget_left=remaining,
            target=target if target > 0 else remaining * Decimal('0.2'),
            exclude_ids=exclude_ids,
        )
        if not product:
            continue
        chosen.append((slot, product, 1, price))
        used_slots.add(slot)
        remaining -= price
        exclude_ids.add(product.pk)

    items = [
        serialize_product(product, request, slot=slot, quantity=qty)
        for slot, product, qty, _price in chosen
    ]
    total = sum((_money(row['price']) * int(row.get('quantity') or 1) for row in items), Decimal('0'))
    leftover = max(Decimal('0.00'), budget_dec - total)
    fish_label = dict((c, n) for c, n, _i in FISH_TYPES).get(fish_code, fish_code)
    tier_label = dict((k, n) for k, n, _d, _f in TIERS).get(tier, tier)
    return {
        'ok': True,
        'fish': fish_code,
        'tier': tier,
        'title': f'{fish_label} set • {tier_label}',
        'budget': budget,
        'budget_display': _display(budget_dec),
        'items': items,
        'total': str(_money(total)),
        'total_display': _display(total),
        'leftover': str(_money(leftover)),
        'leftover_display': _display(leftover),
    }


def alternatives(*, slot, product_id, fish_code, budget, exclude_ids=None, request=None):
    slot = (slot or '').strip().lower()
    if slot not in dict(SLOTS):
        return []
    exclude_ids = set(exclude_ids or ())
    if product_id:
        exclude_ids.add(int(product_id))
    current = None
    if product_id:
        current = Product.objects.filter(pk=product_id).first()
    current_price = _money(getattr(current, 'prikazna_cijena', 0)) if current else _money(0)
    cap = _money(budget or 400)
    if current_price > 0:
        cap = max(cap, current_price * Decimal('1.35'))
    rows = []
    qs = _slot_queryset(slot, fish_code).exclude(pk__in=exclude_ids)
    for product in qs[:60]:
        price = _money(product.prikazna_cijena)
        if price <= 0 or price > cap:
            continue
        score = _fish_score(product, fish_code)
        distance = abs(float(price) - float(current_price or price))
        rows.append((score, distance, product))
    rows.sort(key=lambda row: (-row[0], row[1]))
    out = []
    for _score, _d, product in rows[:8]:
        payload = serialize_product(product, request, slot=slot)
        payload['recommended'] = _score > 0
        out.append(payload)
    return out


def page_config(request=None):
    try:
        admin_types = list(
            AdvisorBeginnerFishType.objects.filter(aktivan=True).order_by('redoslijed', 'naziv')
        )
    except Exception:
        admin_types = []
    static_map = {code: (label, img) for code, label, img in FISH_TYPES}
    static_map.update({
        'saran': ('Šaranski ribolov', 'saranski.jpg'),
        'stuka': ('Varaličarenje', 'varalicarski.jpg'),
        'ul': ('UL ribolov', 'varalicarski.jpg'),
    })
    fish = []
    if admin_types:
        for ft in admin_types:
            code = (ft.code or '').strip().lower()
            if not code:
                continue
            label, img = static_map.get(code, (ft.naziv, ''))
            fish.append({
                'id': code,
                'label': (ft.naziv or label or code).upper(),
                'image': f'img/set-builder/{img}' if img else '',
            })
    if not fish:
        fish = [
            {
                'id': code,
                'label': label.upper(),
                'image': f'img/set-builder/{img}',
            }
            for code, label, img in FISH_TYPES
        ]
    return {
        'fish_types': fish,
        'tiers': [
            {'id': k, 'label': n, 'desc': d}
            for k, n, d, _f in TIERS
        ],
        'slots': [{'id': k, 'label': n} for k, n in SLOTS],
        'default_slots': list(DEFAULT_SLOTS),
        'budget_min': BUDGET_MIN,
        'budget_max': BUDGET_MAX,
        'budget_presets': list(BUDGET_PRESETS),
        'default_budget': 300,
        'default_tier': 'preporuka',
    }
