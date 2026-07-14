"""
Virtuelni ribolovački savjetnik — vođeni chat:

  Koja oprema? → Feeder / Šaranska / Varaličarska
    → šta tražiš? (štap / mašinica / najlon / hranilice…)
      → štap|mašinica → budžet → preporuka
      → najlon → debljina (mm) → preporuka
      → hranilice → gramaža (g) → preporuka

Pravila štap/mašinica:
  Šaran: mašinice 6000+; štapovi 3 / 3,25 / 3,5 lb
  Varalica: štapovi 2,4 / 2,7 / 3 m; mašinice 3000 / 4000 / 4500
  Feeder: feeder štapovi i mašinice
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from django.db.models import Q

from .models import Product

BUDGET_OPTIONS = (
    (100, 'Do 100 KM'),
    (200, 'Do 200 KM'),
    (300, 'Do 300 KM'),
    (500, 'Do 500 KM'),
)

# Debljina najlona (mm)
LINE_DIAMETERS = (
    ('0.10', '0,10 mm'),
    ('0.12', '0,12 mm'),
    ('0.14', '0,14 mm'),
    ('0.16', '0,16 mm'),
    ('0.18', '0,18 mm'),
    ('0.20', '0,20 mm'),
    ('0.22', '0,22 mm'),
    ('0.25', '0,25 mm'),
    ('0.28', '0,28 mm'),
    ('0.30', '0,30 mm'),
)

# Gramaža hranilica
FEEDER_WEIGHTS = (
    (20, '20 g'),
    (30, '30 g'),
    (40, '40 g'),
    (45, '45 g'),
    (50, '50 g'),
    (60, '60 g'),
    (80, '80 g'),
    (100, '100 g'),
    (150, '150 g'),
)

STYLES = {
    'feeder': {
        'label': 'Feeder oprema',
        'short': 'Feeder',
        'emoji': '🎣',
        'intro': 'Feeder oprema — hranilica, precizan rad, puno pečenja.',
        'items': ('stap', 'masinica', 'najlon', 'hranilice'),
        'rod_note': 'Feeder štapovi (feeder / method / picker).',
        'reel_note': 'Feeder mašinice (match / feeder reel).',
    },
    'saran': {
        'label': 'Šaranska oprema',
        'short': 'Šaran',
        'emoji': '🐟',
        'intro': 'Šaranska oprema — jači štapovi (lb) i veće mašinice.',
        'items': ('stap', 'masinica', 'najlon', 'hranilice'),
        'rod_note': 'Štapovi 3 lb / 3,25 lb / 3,5 lb.',
        'reel_note': 'Mašinice veličine 6000 i više.',
    },
    'varalica': {
        'label': 'Varaličarska oprema',
        'short': 'Varalica',
        'emoji': '🌀',
        'intro': 'Varaličarska oprema — spinning štapovi i manje mašinice.',
        'items': ('stap', 'masinica', 'najlon'),
        'rod_note': 'Štapovi 2,4 m / 2,7 m / 3 m.',
        'reel_note': 'Mašinice 3000 / 4000 / 4500 (ovisno o dužini).',
    },
}

ITEM_TYPES = {
    'stap': {
        'label': 'Štap',
        'emoji': '🥢',
        'needs': 'budget',
    },
    'masinica': {
        'label': 'Mašinica',
        'emoji': '⚙️',
        'needs': 'budget',
    },
    'najlon': {
        'label': 'Najlon',
        'emoji': '🧵',
        'needs': 'diameter',
    },
    'hranilice': {
        'label': 'Hranilice',
        'emoji': '⚖️',
        'needs': 'weight',
    },
}

ROD_KEYWORDS = (
    'štap', 'stap', 'rod', 'casting', 'spinning rod', 'feeder rod',
    'carp rod', 'picker', 'match rod', 'spinn', 'h-cast', 'h cast',
)
REEL_KEYWORDS = (
    'mašin', 'masin', 'reel', 'navijač', 'navijac', 'baitrunner',
    'spool', 'eos',
)
LINE_KEYWORDS = (
    'najlon', 'mono', 'monofilament', 'micron', 'sinking', 'fluorocarbon',
    'fluoro', 'line', 'pletenica', 'braid', 'špag', 'spag',
)
FEEDER_CAGE_KEYWORDS = (
    'method feeder', 'open method', 'hranil', 'cage feeder', 'pellet feeder',
    'alloy method', 'inline method', 'banjo', 'flat method', 'feeder medium',
    'feeder large', 'feeder small', 'gfr',
)

SARAN_ROD_LB = (3.0, 3.25, 3.5)
SARAN_REEL_MIN = 6000
VARALICA_ROD_LENGTHS = (2.4, 2.7, 3.0)
VARALICA_REEL_BY_LENGTH = {
    2.4: (3000,),
    2.7: (4000,),
    3.0: (4500, 4000),
}
VARALICA_REEL_SIZES = (3000, 4000, 4500)


def _dec(val):
    try:
        return Decimal(str(val))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0')


def _product_price(product):
    try:
        return _dec(product.prikazna_cijena)
    except Exception:
        return _dec(getattr(product, 'cijena', 0))


def _product_name(product):
    return (getattr(product, 'naziv', '') or '').lower()


def _product_text(product):
    parts = [
        getattr(product, 'naziv', '') or '',
        getattr(product, 'opis', '') or '',
    ]
    brend = getattr(product, 'brend', None)
    if brend is not None:
        parts.append(getattr(brend, 'naziv', '') or '')
    return ' '.join(parts).lower()


def _product_image_url(product, request=None):
    img = getattr(product, 'slika', None)
    if img:
        try:
            url = img.url
            if request:
                return request.build_absolute_uri(url)
            return url
        except Exception:
            pass
    return ''


def _serialize_product(product, request=None, role='', note=''):
    price = _product_price(product)
    in_stock = bool(getattr(product, 'na_stanju', True))
    return {
        'id': product.pk,
        'name': product.naziv,
        'slug': product.slug,
        'url': product.get_absolute_url(),
        'price': str(price.quantize(Decimal('0.01'))),
        'price_display': f'{price.quantize(Decimal("0.01"))} KM'.replace('.', ','),
        'image': _product_image_url(product, request),
        'role': role,
        'note': note,
        'in_stock': in_stock,
    }


def _base_qs(require_stock=True):
    qs = (
        Product.objects.filter(aktivan=True)
        .exclude(naziv__icontains='gift card')
        .exclude(naziv__icontains='poklon')
        .exclude(naziv__icontains='vaučer')
        .exclude(naziv__icontains='vaucer')
        .exclude(naziv__icontains='testni')
        .exclude(naziv__icontains='test artikal')
        .select_related('brend', 'kategorija')
    )
    if require_stock:
        qs = qs.filter(na_stanju=True)
    return qs


def _keyword_q(keywords):
    q = Q()
    for kw in keywords:
        q |= Q(naziv__icontains=kw) | Q(opis__icontains=kw)
        q |= Q(brend__naziv__icontains=kw)
    return q


def _is_noise_product(text):
    noise = (
        'udica', 'udice', 'hook', 'trokuk', 'glove', 'rukavic', 'elastic',
        'meredov', 'landing net', 'feeder link', 'snap', 'kopča', 'kopca',
        'virbl', 'swivel', 'assist cord', 'shot', 'disgorger', 'catapult',
        'mould', 'boilies', 'kuka', 'spoon-', 'tackle system', 'tackle box',
    )
    return any(n in text for n in noise)


def _nearest(value, targets, tol=0.15):
    for t in targets:
        if abs(value - t) <= max(tol, t * 0.06):
            return t
    return None


def _parse_lb(text):
    found = []
    for m in re.finditer(r'(\d+(?:[.,]\d+)?)\s*lb\b', text, re.I):
        try:
            found.append(float(m.group(1).replace(',', '.')))
        except ValueError:
            continue
    return found


def _parse_length_m(text):
    found = []
    for m in re.finditer(r'(\d+(?:[.,]\d+)?)\s*m\b', text, re.I):
        try:
            v = float(m.group(1).replace(',', '.'))
        except ValueError:
            continue
        if 1.5 <= v <= 5.0:
            found.append(v)
    for m in re.finditer(r'(\d{3})\s*cm\b', text, re.I):
        try:
            v = int(m.group(1)) / 100.0
        except ValueError:
            continue
        if 1.5 <= v <= 5.0:
            found.append(v)
    return found


def _parse_mm(text):
    found = []
    for m in re.finditer(r'(\d+[.,]\d+)\s*mm\b', text, re.I):
        try:
            found.append(float(m.group(1).replace(',', '.')))
        except ValueError:
            continue
    # npr. 0.16 bez mm u Power Micron X 0.16mm već uhvaćeno; fallback 0.16 u nazivu
    for m in re.finditer(r'\b0[.,](\d{2})\b', text):
        try:
            found.append(float('0.' + m.group(1)))
        except ValueError:
            continue
    return found


def _parse_grams(text):
    found = []
    for m in re.finditer(r'(\d+(?:[.,]\d+)?)\s*g\b', text, re.I):
        try:
            found.append(float(m.group(1).replace(',', '.')))
        except ValueError:
            continue
    return found


def _looks_like_rod(text, name_only=''):
    check = name_only or text
    if _is_noise_product(check):
        return False
    if re.search(r'\b(reel|mašinica|masinica|navijač|navijac|baitrunner)\b', check):
        if not re.search(r'\b(rod|štap|stap)\b', check):
            return False
    if re.search(
        r'\b(rod|štap|stap|picker|feeder rod|carp rod|spinning rod|match rod|'
        r'h-cast|h cast|spin rod)\b',
        check,
    ):
        return True
    lbs = _parse_lb(check)
    if lbs and any(2.5 <= lb <= 4.0 for lb in lbs):
        if any(k in check for k in ('carp', 'šaran', 'saran', 'rod', 'štap', 'stap')):
            return True
    lengths = _parse_length_m(check)
    if lengths and any(k in check for k in ('feeder', 'cast', 'spin', 'carp', 'picker')):
        return True
    return False


def _looks_like_reel(text, name_only=''):
    check = name_only or text
    if _is_noise_product(check):
        return False
    if not re.search(
        r'\b(reel|mašin|masin|navijač|navijac|baitrunner|big pit)\b',
        check,
    ) and 'eos' not in check:
        return False
    if re.search(r'\b(rod|štap)\b', check) and not re.search(r'\b(reel|mašin|masin)\b', check):
        return False
    return True


def _looks_like_line(text, name_only=''):
    check = name_only or text
    if _is_noise_product(check) and 'najlon' not in check and 'mono' not in check:
        # dozvoli mono/najlon čak i ako ima "elastic" u opisu rijetko
        pass
    if any(k in check for k in ('elastic', 'glove', 'udica', 'hook', 'feeder link')):
        return False
    return any(k in check for k in (
        'najlon', 'mono', 'monofilament', 'micron', 'fluorocarbon', 'fluoro',
        'sinking mono', 'power micron', 'braid', 'pleten',
    )) or (bool(_parse_mm(check)) and any(k in check for k in ('line', 'mm', 'lb')))


def _looks_like_feeder_cage(text, name_only=''):
    check = name_only or text
    if any(k in check for k in ('feeder link', 'feeder bead', 'tackle box', 'rod', 'štap', 'reel')):
        return False
    if any(k in check for k in (
        'method feeder', 'open method', 'hranil', 'cage feeder', 'pellet feeder',
        'alloy method', 'alloy open', 'banjo', 'flat method',
    )):
        return True
    # "Feeder Medium 40g" stil
    if 'feeder' in check and _parse_grams(check) and not re.search(r'\b(rod|štap|3m|150g)\b', check):
        # 150g na štapu — izbjegni; 40g na hranilici ok
        grams = _parse_grams(check)
        if any(10 <= g <= 200 for g in grams) and 'rod' not in check and 'štap' not in check:
            if re.search(r'\b(method|open|cage|inline|medium|large|small|gfr)\b', check):
                return True
    return False


def _parse_reel_size(text, name_only=''):
    check = name_only or text
    if not _looks_like_reel(check) and 'eos' not in check:
        return []
    sizes = []
    patterns = (
        r'(?:eos|size|sz|reel|mašin\w*|masin\w*)\s*([1-9]\d{3,4})\b',
        r'\b([1-9]\d{3,4})\b',
    )
    typical = {
        2500, 3000, 3500, 4000, 4500, 5000, 5500, 6000, 7000, 8000, 10000, 12000, 14000,
    }
    for pat in patterns:
        for m in re.finditer(pat, check, re.I):
            try:
                n = int(m.group(1))
            except ValueError:
                continue
            if n in typical or (1000 <= n <= 20000 and n % 500 == 0):
                sizes.append(n)
    return list(dict.fromkeys(sizes))


def _score_rod(product, style_key):
    name = _product_name(product)
    text = _product_text(product)
    if not _looks_like_rod(text, name_only=name):
        return -1, ''
    score = 10
    note = 'Štap'
    if style_key == 'saran':
        lbs = _parse_lb(text)
        hit = None
        for lb in lbs:
            hit = _nearest(lb, SARAN_ROD_LB, tol=0.2)
            if hit:
                break
        if hit:
            score += 50
            note = f'{hit:g} lb — idealno za šarana'
        elif lbs and any(2.5 <= lb <= 4.0 for lb in lbs):
            score += 30
            note = f'{lbs[0]:g} lb (blizu 3–3,5 lb)'
        elif any(k in text for k in ('carp', 'šaran', 'saran')):
            score += 22
            note = 'Šaranski štap'
        else:
            score += 5
            note = 'Štap (za šarana: 3 / 3,25 / 3,5 lb)'
    elif style_key == 'varalica':
        lengths = _parse_length_m(text)
        hit = None
        for ln in lengths:
            hit = _nearest(ln, VARALICA_ROD_LENGTHS, tol=0.12)
            if hit:
                break
        if hit:
            score += 50
            reel_hint = VARALICA_REEL_BY_LENGTH.get(hit, (4000,))
            note = f'{hit:g} m — mašinica ~{reel_hint[0]}'
        elif any(k in text for k in ('spin', 'varalic', 'rage', 'casting', 'cast')):
            score += 22
            note = 'Spinning / varalica štap'
        else:
            score += 5
            note = 'Štap (varalica: 2,4 / 2,7 / 3 m)'
    else:
        if 'feeder' in text or 'fider' in text:
            score += 50
            note = 'Feeder štap'
        elif 'picker' in text:
            score += 40
            note = 'Picker / feeder štap'
        else:
            score += 5
            note = 'Štap (feeder)'
    return score, note


def _score_reel(product, style_key, preferred_sizes=None):
    name = _product_name(product)
    text = _product_text(product)
    if not _looks_like_reel(text, name_only=name):
        return -1, ''
    sizes = _parse_reel_size(text, name_only=name)
    score = 10
    note = 'Mašinica'
    preferred_sizes = preferred_sizes or ()
    if style_key == 'saran':
        big = [s for s in sizes if s >= SARAN_REEL_MIN]
        if big:
            score += 50
            note = f'Veličina {max(big)} — 6000+ za šarana'
        elif sizes:
            score += 8
            note = f'Veličina {sizes[0]} (za šarana 6000+)'
        elif any(k in text for k in ('carp', 'baitrunner', 'big pit')):
            score += 28
            note = 'Šaranska / baitrunner mašinica'
        else:
            score += 5
            note = 'Mašinica (šaran: 6000+)'
    elif style_key == 'varalica':
        matched = None
        for s in sizes:
            if s in VARALICA_REEL_SIZES or 2500 <= s <= 5000:
                matched = s
                break
        if preferred_sizes:
            for s in sizes:
                if s in preferred_sizes:
                    score += 55
                    note = f'Veličina {s} — uz štap'
                    break
            else:
                if matched:
                    score += 35
                    note = f'Veličina {matched} (spinning)'
        elif matched:
            score += 45
            note = f'Veličina {matched} — spinning'
        else:
            score += 8
            note = 'Mašinica (spinning 3000–4500)'
    else:
        if 'feeder' in text or 'match' in text:
            score += 50
            note = 'Feeder / match mašinica'
        elif sizes and any(2500 <= s <= 6500 for s in sizes):
            score += 35
            note = f'Veličina {sizes[0]} — ok za feeder'
        else:
            score += 10
            note = 'Mašinica (feeder)'
    return score, note


def _score_line(product, diameter_mm):
    name = _product_name(product)
    text = _product_text(product)
    if not _looks_like_line(text, name_only=name):
        return -1, ''
    mms = _parse_mm(text)
    target = float(diameter_mm)
    score = 10
    note = 'Najlon'
    hit = None
    for mm in mms:
        if abs(mm - target) <= 0.015:
            hit = mm
            break
        if abs(mm - target) <= 0.03:
            hit = mm
    if hit is not None and abs(hit - target) <= 0.015:
        score += 55
        note = f'{hit:.2f} mm — tačna debljina'.replace('.', ',')
    elif hit is not None:
        score += 30
        note = f'{hit:.2f} mm — blizu {target:.2f} mm'.replace('.', ',')
    elif mms:
        score += 8
        note = f'{mms[0]:.2f} mm'.replace('.', ',')
    else:
        score += 5
        note = 'Najlon / mono'
    if getattr(product, 'na_stanju', False):
        score += 8
    return score, note


def _score_feeder_cage(product, weight_g):
    name = _product_name(product)
    text = _product_text(product)
    if not _looks_like_feeder_cage(text, name_only=name):
        return -1, ''
    grams = _parse_grams(text)
    target = float(weight_g)
    score = 10
    note = 'Hranilica'
    hit = None
    for g in grams:
        if abs(g - target) <= 5:
            hit = g
            break
        if abs(g - target) <= 15:
            hit = g
    if hit is not None and abs(hit - target) <= 5:
        score += 55
        note = f'{int(hit) if hit == int(hit) else hit} g — tražena gramaža'
    elif hit is not None:
        score += 28
        note = f'{int(hit) if hit == int(hit) else hit} g — blizu {int(target)} g'
    elif grams:
        score += 10
        note = f'{int(grams[0])} g'
    else:
        score += 5
    if 'method' in text:
        score += 8
    if getattr(product, 'na_stanju', False):
        score += 8
    return score, note


def _candidate_products(kind, budget=None, require_stock=True, limit_scan=250):
    kws = {
        'rod': ROD_KEYWORDS,
        'reel': REEL_KEYWORDS,
        'line': LINE_KEYWORDS,
        'cage': FEEDER_CAGE_KEYWORDS,
    }.get(kind, ())
    qs = _base_qs(require_stock=require_stock)
    if kws:
        qs = qs.filter(_keyword_q(kws))
    out = []
    for p in qs[:limit_scan]:
        price = _product_price(p)
        if budget is not None and (price <= 0 or price > budget):
            continue
        out.append(p)
    if len(out) < 4 and require_stock:
        # proširi i na rasprodato za prikaz
        return _candidate_products(kind, budget=budget, require_stock=False, limit_scan=limit_scan)
    if len(out) < 4:
        for p in _base_qs(require_stock=False)[:300]:
            if p.pk in {x.pk for x in out}:
                continue
            price = _product_price(p)
            if budget is not None and (price <= 0 or price > budget):
                continue
            name = _product_name(p)
            text = _product_text(p)
            ok = False
            if kind == 'rod':
                ok = _looks_like_rod(text, name_only=name)
            elif kind == 'reel':
                ok = _looks_like_reel(text, name_only=name)
            elif kind == 'line':
                ok = _looks_like_line(text, name_only=name)
            elif kind == 'cage':
                ok = _looks_like_feeder_cage(text, name_only=name)
            if ok:
                out.append(p)
            if len(out) >= 40:
                break
    return out


def _rank_rods(style_key, budget, limit=5):
    scored = []
    for p in _candidate_products('rod', budget=budget, require_stock=True):
        sc, note = _score_rod(p, style_key)
        if sc < 0:
            continue
        sc += float(min(_product_price(p), budget) / budget) * 5
        if p.na_stanju:
            sc += 5
        scored.append((sc, p, note))
    if not scored:
        for p in _candidate_products('rod', budget=budget, require_stock=False):
            sc, note = _score_rod(p, style_key)
            if sc < 0:
                continue
            scored.append((sc, p, note))
    scored.sort(key=lambda x: (-x[0], _product_price(x[1])))
    return scored[:limit]


def _rank_reels(style_key, budget, preferred_sizes=None, limit=5):
    scored = []
    for p in _candidate_products('reel', budget=budget, require_stock=True):
        sc, note = _score_reel(p, style_key, preferred_sizes=preferred_sizes)
        if sc < 0:
            continue
        sc += float(min(_product_price(p), budget) / budget) * 5
        if p.na_stanju:
            sc += 5
        scored.append((sc, p, note))
    if not scored:
        for p in _candidate_products('reel', budget=budget, require_stock=False):
            sc, note = _score_reel(p, style_key, preferred_sizes=preferred_sizes)
            if sc < 0:
                continue
            scored.append((sc, p, note))
    scored.sort(key=lambda x: (-x[0], _product_price(x[1])))
    return scored[:limit]


def _rank_lines(diameter_mm, limit=6):
    scored = []
    for p in _candidate_products('line', budget=None, require_stock=False):
        sc, note = _score_line(p, diameter_mm)
        if sc < 0:
            continue
        scored.append((sc, p, note))
    scored.sort(key=lambda x: (-x[0], not x[1].na_stanju, _product_price(x[1])))
    return scored[:limit]


def _rank_cages(weight_g, limit=6):
    scored = []
    for p in _candidate_products('cage', budget=None, require_stock=False):
        sc, note = _score_feeder_cage(p, weight_g)
        if sc < 0:
            continue
        scored.append((sc, p, note))
    scored.sort(key=lambda x: (-x[0], not x[1].na_stanju, _product_price(x[1])))
    return scored[:limit]


def _item_options_for_style(style_key):
    style = STYLES.get(style_key) or STYLES['feeder']
    opts = []
    for key in style['items']:
        it = ITEM_TYPES[key]
        opts.append({'id': key, 'label': f'{it["emoji"]} {it["label"]}'})
    return opts


def build_recommendation(style_key, item_key, budget_km=None, diameter=None, weight_g=None, request=None):
    style = STYLES.get(style_key) or STYLES['feeder']
    item = ITEM_TYPES.get(item_key) or ITEM_TYPES['stap']
    products = []
    tip = ''
    headline = ''

    if item_key == 'stap':
        budget = _dec(budget_km or 100)
        ranked = _rank_rods(style_key, budget, limit=4)
        for sc, p, note in ranked:
            products.append(_serialize_product(p, request, role='stap', note=note))
        tip = style['rod_note']
        headline = (
            f'Predloženi štapovi — {style["short"].lower()} do {int(budget)} KM:'
            if products else
            f'Nema štapova do {int(budget)} KM za {style["short"].lower()}. {style["rod_note"]}'
        )

    elif item_key == 'masinica':
        budget = _dec(budget_km or 100)
        ranked = _rank_reels(style_key, budget, limit=4)
        for sc, p, note in ranked:
            products.append(_serialize_product(p, request, role='masinica', note=note))
        tip = style['reel_note']
        headline = (
            f'Predložene mašinice — {style["short"].lower()} do {int(budget)} KM:'
            if products else
            f'Nema mašinica do {int(budget)} KM. {style["reel_note"]}'
        )

    elif item_key == 'najlon':
        diam = str(diameter or '0.18')
        ranked = _rank_lines(diam, limit=6)
        for sc, p, note in ranked:
            products.append(_serialize_product(p, request, role='najlon', note=note))
        tip = f'Tražiš najlon ~{diam.replace(".", ",")} mm.'
        headline = (
            f'Najlon / mono oko {diam.replace(".", ",")} mm:'
            if products else
            f'Trenutno nema najlona ~{diam.replace(".", ",")} mm u katalogu. '
            f'Provjeri kasnije ili drugu debljinu.'
        )

    elif item_key == 'hranilice':
        w = int(weight_g or 40)
        ranked = _rank_cages(w, limit=6)
        for sc, p, note in ranked:
            products.append(_serialize_product(p, request, role='hranilica', note=note))
        tip = f'Tražiš hranilice oko {w} g (method / open / cage).'
        headline = (
            f'Hranilice oko {w} g:'
            if products else
            f'Trenutno nema hranilica ~{w} g. Probaj drugu gramažu.'
        )
    else:
        headline = 'Odaberi šta tražiš.'
        tip = ''

    total = sum((_dec(p['price']) for p in products), Decimal('0'))
    return {
        'headline': headline,
        'tip': tip,
        'style': style_key,
        'style_label': style['label'],
        'item': item_key,
        'item_label': item['label'],
        'budget': int(budget_km) if budget_km else None,
        'diameter': diameter,
        'weight_g': weight_g,
        'set_kind': item_key,
        'products': products,
        'total': str(total.quantize(Decimal('0.01'))),
        'total_display': f'{total.quantize(Decimal("0.01"))} KM'.replace('.', ','),
    }


def process_step(step, answer, state=None, request=None):
    """
    start → style → item → budget|diameter|weight → done
    """
    state = dict(state or {})
    answer = (answer or '').strip().lower()
    step = (step or 'start').strip().lower()

    def bot(text, options=None, next_step=None, recommendation=None, done=False):
        payload = {
            'ok': True,
            'messages': [{'role': 'bot', 'text': text}],
            'options': options or [],
            'state': state,
            'step': next_step or step,
            'done': done,
        }
        if recommendation:
            payload['recommendation'] = recommendation
        return payload

    def finish_rec(rec):
        lines = [rec['headline']]
        if rec.get('tip'):
            lines.append(f'💡 {rec["tip"]}')
        for p in rec.get('products') or []:
            stock = '' if p.get('in_stock') else ' · rasprodato'
            if p.get('note'):
                lines.append(f'• {p["note"]}{stock}')
        if rec.get('products') and rec.get('budget'):
            lines.append(f'Ukupno u listi: {rec["total_display"]} (budžet do {rec["budget"]} KM).')
        elif rec.get('products'):
            lines.append(f'Ukupno u listi: {rec["total_display"]}.')
        lines.append('Otvori artikle ili kreni ispočetka.')
        return bot(
            '\n'.join(lines),
            options=[
                {'id': 'again', 'label': '🔄 Novi savjet'},
                {'id': 'catalog', 'label': '📦 Katalog', 'url': '/'},
            ],
            next_step='done',
            recommendation=rec,
            done=True,
        )

    # --- START: odmah stil opreme ---
    if step in ('start', 'reset', ''):
        state.clear()
        return bot(
            'Zdravo! 🎣 Ja sam tvoj ribolovački savjetnik.\n\n'
            'Koja ti oprema treba?',
            options=[
                {'id': 'feeder', 'label': f'{STYLES["feeder"]["emoji"]} Feeder oprema'},
                {'id': 'saran', 'label': f'{STYLES["saran"]["emoji"]} Šaranska oprema'},
                {'id': 'varalica', 'label': f'{STYLES["varalica"]["emoji"]} Varaličarska oprema'},
            ],
            next_step='style',
        )

    # --- STYLE ---
    if step == 'style':
        if answer not in STYLES:
            return bot(
                'Odaberi vrstu opreme:',
                options=[
                    {'id': 'feeder', 'label': f'{STYLES["feeder"]["emoji"]} Feeder oprema'},
                    {'id': 'saran', 'label': f'{STYLES["saran"]["emoji"]} Šaranska oprema'},
                    {'id': 'varalica', 'label': f'{STYLES["varalica"]["emoji"]} Varaličarska oprema'},
                ],
                next_step='style',
            )
        state['style'] = answer
        style = STYLES[answer]
        return bot(
            f'{style["intro"]}\n\nŠta tačno tražiš?',
            options=_item_options_for_style(answer),
            next_step='item',
        )

    # --- ITEM ---
    if step == 'item':
        style_key = state.get('style') or 'feeder'
        allowed = set((STYLES.get(style_key) or STYLES['feeder'])['items'])
        if answer not in allowed:
            return bot(
                'Odaberi šta tražiš:',
                options=_item_options_for_style(style_key),
                next_step='item',
            )
        state['item'] = answer
        item = ITEM_TYPES[answer]
        style = STYLES.get(style_key, STYLES['feeder'])

        if item['needs'] == 'budget':
            hint = style['rod_note'] if answer == 'stap' else style['reel_note']
            return bot(
                f'Super — {item["label"].lower()} za {style["short"].lower()}.\n'
                f'{hint}\n\nDo koliko bi izdvojio?',
                options=[{'id': str(b), 'label': lab} for b, lab in BUDGET_OPTIONS],
                next_step='budget',
            )

        if item['needs'] == 'diameter':
            return bot(
                'Za najlon mi treba debljina.\n'
                'Koju debljinu tražiš?',
                options=[{'id': d, 'label': lab} for d, lab in LINE_DIAMETERS],
                next_step='diameter',
            )

        if item['needs'] == 'weight':
            return bot(
                'Za hranilice mi treba gramaža.\n'
                'Koju težinu tražiš?',
                options=[{'id': str(w), 'label': lab} for w, lab in FEEDER_WEIGHTS],
                next_step='weight',
            )

    # --- BUDGET (štap / mašinica) ---
    if step == 'budget':
        try:
            budget = int(answer)
        except (TypeError, ValueError):
            budget = 0
        if budget not in {b for b, _ in BUDGET_OPTIONS}:
            return bot(
                'Odaberi budžet:',
                options=[{'id': str(b), 'label': lab} for b, lab in BUDGET_OPTIONS],
                next_step='budget',
            )
        state['budget'] = budget
        rec = build_recommendation(
            state.get('style') or 'feeder',
            state.get('item') or 'stap',
            budget_km=budget,
            request=request,
        )
        return finish_rec(rec)

    # --- DIAMETER (najlon) ---
    if step == 'diameter':
        allowed = {d for d, _ in LINE_DIAMETERS}
        # normalizuj 0,18 → 0.18
        ans = answer.replace(',', '.')
        if ans not in allowed:
            return bot(
                'Odaberi debljinu najlona:',
                options=[{'id': d, 'label': lab} for d, lab in LINE_DIAMETERS],
                next_step='diameter',
            )
        state['diameter'] = ans
        rec = build_recommendation(
            state.get('style') or 'feeder',
            'najlon',
            diameter=ans,
            request=request,
        )
        return finish_rec(rec)

    # --- WEIGHT (hranilice) ---
    if step == 'weight':
        try:
            w = int(float(answer))
        except (TypeError, ValueError):
            w = 0
        if w not in {x for x, _ in FEEDER_WEIGHTS}:
            return bot(
                'Odaberi gramažu hranilice:',
                options=[{'id': str(x), 'label': lab} for x, lab in FEEDER_WEIGHTS],
                next_step='weight',
            )
        state['weight_g'] = w
        rec = build_recommendation(
            state.get('style') or 'feeder',
            'hranilice',
            weight_g=w,
            request=request,
        )
        return finish_rec(rec)

    if step == 'done' or answer in ('again', 'reset'):
        state.clear()
        return process_step('start', '', state, request=request)

    return process_step('start', '', {}, request=request)
