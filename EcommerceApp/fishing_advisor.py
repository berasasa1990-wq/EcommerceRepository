"""
Ribolovački savjetnik — razgovor s iskusnim ribolovcem.
Cilj: 30–60 s, malo pitanja, setovi iz admina.

Tok:
  kit_level (set na akciji / pojedinačno)
  → [set] set_type (iz admina: saranski, varaličarski, feeder, plovak…)
  → [samo varaličarski] varalic_style (lov štuke / lov soma / UL)
  → budget → results
  → accessories / single-item / again
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db.models import Q, Prefetch
from django.urls import reverse

from .models import Category, Product

# ─── Opcije ────────────────────────────────────────────────────────

# Legacy / fallback za pojedinačnu opremu (kategorije po ribi)
FISH = {
    'saran': {'label': 'Šaran', 'emoji': '🐟', 'codes': ('saran', 'saranski')},
    'saranski': {'label': 'Saranski set', 'emoji': '🎣', 'codes': ('saranski', 'saran')},
    'som': {'label': 'Som', 'emoji': '🐟', 'codes': ('som',)},
    'stuka': {'label': 'Štuka', 'emoji': '🐟', 'codes': ('stuka',)},
    'smud': {'label': 'Smuđ', 'emoji': '🐟', 'codes': ('smud', 'stuka')},
    'pastrmka': {'label': 'Pastrmka', 'emoji': '🐟', 'codes': ('pastrmka',)},
    'bijela': {'label': 'Bijela riba', 'emoji': '🐟', 'codes': ('bijela',)},
    'feeder': {'label': 'Feeder set', 'emoji': '🎣', 'codes': ('feeder',)},
    'plovak': {'label': 'Pečaljke za plovak', 'emoji': '🎣', 'codes': ('plovak',)},
    'ul': {'label': 'UL ribolov', 'emoji': '🎣', 'codes': ('ul', 'ul_ribolov')},
    'varalicarski': {'label': 'Varaličarski set', 'emoji': '🎣', 'codes': ('varalicarski',)},
    'vise': {'label': 'Više vrsta ribe', 'emoji': '🐟', 'codes': ('saran', 'bijela', 'stuka')},
}

BUDGET = {
    '80': {'label': 'Do 80 KM', 'emoji': '💰', 'max': Decimal('80')},
    '150': {'label': 'Do 150 KM', 'emoji': '💰', 'max': Decimal('150')},
    '250': {'label': 'Do 250 KM', 'emoji': '💰', 'max': Decimal('250')},
    '250plus': {'label': 'Preko 250 KM', 'emoji': '💰', 'max': Decimal('9999')},
}

# Prvo pitanje: set na akciji ili pojedinačno
KIT_LEVEL = {
    'komplet': {
        'label': 'Setove za ribolov na akciji',
        'emoji': '🎁',
        'tier': None,
        'mode': 'set',
    },
    'pojedinacno': {
        'label': 'Pojedinačno kupovati',
        'emoji': '🛒',
        'tier': None,
        'mode': 'single',
    },
}

# Samo za varaličarski set — prije budžeta
VARALIC_STYLE = {
    'stuka': {
        'label': 'Lov štuke',
        'emoji': '🐟',
        'codes': ('stuka', 'lov_stuke', 'varalic_stuka'),
    },
    'som': {
        'label': 'Lov soma',
        'emoji': '🐟',
        'codes': ('som', 'lov_soma', 'varalic_som'),
    },
    'ul': {
        'label': 'UL ribolov',
        'emoji': '🎣',
        'codes': ('ul', 'ul_ribolov', 'varalic_ul'),
    },
}

# Kodovi u adminu koji spadaju pod „Varaličarski set” (ne prikazuju se kao zasebni top-level)
VARALIC_SUBTYPE_CODES = frozenset({
    'stuka', 'som', 'ul',
    'lov_stuke', 'lov_soma', 'ul_ribolov',
    'varalic_stuka', 'varalic_som', 'varalic_ul',
})
VARALICARSKI_CODE = 'varalicarski'

SINGLE_ITEMS = {
    'stap': {
        'label': 'Štap',
        'emoji': '🎣',
        'slugs': ('feeder-stapovi', 'stapovi-za-varalicu', 'stapovi', 'saranski-stapovi'),
        'names': ('štap', 'stap', 'rod'),
        'keywords': ('štap', 'stap', 'rod'),
    },
    'masinica': {
        'label': 'Mašinicu',
        'emoji': '⚙',
        'slugs': ('masinice', 'mašinice'),
        'names': ('mašin', 'masin', 'reel'),
        'keywords': ('reel', 'mašin', 'masin', 'eos'),
    },
    'najlon': {
        'label': 'Najlon',
        'emoji': '🧵',
        'slugs': ('najloni', 'najlon'),
        'names': ('najlon', 'mono'),
        'keywords': ('najlon', 'mono', 'micron'),
    },
    'udice': {
        'label': 'Udice',
        'emoji': '🪝',
        'slugs': (
            'udice-za-ribolov', 'saranske-udice', 'feeder-udice',
            'udice-za-bjelu-ribu', 'udice-za-soma',
        ),
        'names': ('udic',),
        'keywords': ('udica', 'udice', 'hook'),
    },
    'varalice': {
        'label': 'Varalice',
        'emoji': '🎯',
        'slugs': ('varalice', 'virble-i-kopce'),
        'names': ('varalic', 'wobbl'),
        'keywords': ('varalic', 'softbait', 'wobbl', 'jig', 'rage'),
    },
    'ostalo': {
        'label': 'Ostalu opremu',
        'emoji': '🧰',
        'slugs': (),
        'names': (),
        'keywords': (),
        'url_home': True,
    },
}

# Pojedinačna oprema po ribi → kategorije (slug/naziv) koje prvo tražimo
FISH_ITEM_CATEGORIES = {
    'stap': {
        'saran': {
            'slugs': (
                'saranski-stapovi', 'saran-stapovi', 'saranski-stap',
                'carp-rods', 'carp-stapovi', 'stapovi-za-sarana',
                'feeder-stapovi',  # feeder često za šarana
            ),
            'names': (
                'šaransk', 'saransk', 'šaran štap', 'saran stap',
                'carp rod', 'carp stap', 'feeder stap', 'feeder štap',
            ),
            'keywords': ('carp', 'saran', 'šaran', 'feeder rod', 'carp rod'),
        },
        'som': {
            'slugs': (
                'stapovi-za-soma', 'som-stapovi', 'somski-stapovi',
                'catfish-rods', 'stapovi-som',
            ),
            'names': ('som', 'catfish', 'štap za soma', 'stap za soma'),
            'keywords': ('som', 'catfish'),
        },
        'stuka': {
            'slugs': (
                'stapovi-za-varalicu', 'varalicarski-stapovi', 'spinning-stapovi',
                'stapovi-za-stuku', 'predator-stapovi',
            ),
            'names': ('varalič', 'varalic', 'spinning', 'štuka', 'stuka', 'predator'),
            'keywords': ('spinning', 'varalic', 'pike', 'štuka'),
        },
        'smud': {
            'slugs': (
                'stapovi-za-varalicu', 'varalicarski-stapovi', 'spinning-stapovi',
                'stapovi-za-smuda', 'predator-stapovi',
            ),
            'names': ('varalič', 'varalic', 'spinning', 'smuđ', 'smud', 'predator'),
            'keywords': ('spinning', 'varalic', 'zander', 'smuđ'),
        },
        'pastrmka': {
            'slugs': (
                'stapovi-za-varalicu', 'varalicarski-stapovi', 'spinning-stapovi',
                'stapovi-za-pastrmku', 'trout-stapovi',
            ),
            'names': ('pastrm', 'trout', 'varalič', 'spinning'),
            'keywords': ('pastrm', 'trout', 'spinning'),
        },
        'bijela': {
            'slugs': (
                'feeder-stapovi', 'plovkarski-stapovi', 'match-stapovi',
                'stapovi-za-bjelu-ribu', 'picker-stapovi',
            ),
            'names': ('feeder', 'plovk', 'match', 'picker', 'bijel'),
            'keywords': ('feeder', 'match', 'picker', 'plovak'),
        },
        'vise': {
            'slugs': (
                'feeder-stapovi', 'stapovi-za-varalicu', 'saranski-stapovi',
                'stapovi', 'plovkarski-stapovi',
            ),
            'names': ('štap', 'stap', 'rod', 'feeder', 'varalič'),
            'keywords': ('štap', 'stap', 'rod'),
        },
    },
    'masinica': {
        'saran': {
            'slugs': ('masinice', 'mašinice', 'saranske-masinice', 'baitrunner', 'carp-reels'),
            'names': ('mašin', 'masin', 'baitrunner', 'carp reel', 'šaran'),
            'keywords': ('baitrunner', 'carp reel', 'mašin', 'masin'),
        },
        'som': {
            'slugs': ('masinice', 'mašinice', 'masinice-za-soma', 'catfish-reels'),
            'names': ('mašin', 'masin', 'som', 'catfish'),
            'keywords': ('mašin', 'masin', 'som', 'catfish'),
        },
        'stuka': {
            'slugs': ('masinice', 'mašinice', 'spinning-masinice'),
            'names': ('mašin', 'masin', 'spinning', 'reel'),
            'keywords': ('spinning', 'reel', 'mašin'),
        },
        'smud': {
            'slugs': ('masinice', 'mašinice', 'spinning-masinice'),
            'names': ('mašin', 'masin', 'spinning', 'reel'),
            'keywords': ('spinning', 'reel', 'mašin'),
        },
        'pastrmka': {
            'slugs': ('masinice', 'mašinice', 'spinning-masinice'),
            'names': ('mašin', 'masin', 'spinning'),
            'keywords': ('spinning', 'reel', 'mašin'),
        },
        'bijela': {
            'slugs': ('masinice', 'mašinice', 'feeder-masinice'),
            'names': ('mašin', 'masin', 'feeder', 'match'),
            'keywords': ('feeder', 'match', 'mašin'),
        },
        'vise': {
            'slugs': ('masinice', 'mašinice'),
            'names': ('mašin', 'masin', 'reel'),
            'keywords': ('mašin', 'masin', 'reel'),
        },
    },
    'udice': {
        'saran': {
            'slugs': ('saranske-udice', 'udice-za-ribolov'),
            'names': ('šaransk', 'saransk'),
            'keywords': ('šaran', 'saran', 'carp hook'),
        },
        'som': {
            'slugs': ('udice-za-soma', 'udice-za-ribolov'),
            'names': ('som',),
            'keywords': ('som', 'catfish'),
        },
        'bijela': {
            'slugs': ('udice-za-bjelu-ribu', 'feeder-udice', 'udice-za-ribolov'),
            'names': ('bijel', 'feeder'),
            'keywords': ('bijel', 'feeder'),
        },
        'vise': {
            'slugs': ('udice-za-ribolov', 'feeder-udice', 'saranske-udice'),
            'names': ('udic',),
            'keywords': ('udica', 'hook'),
        },
    },
}

# Cross-sell pribor (keyword matching)
ACCESSORIES = (
    {'id': 'stolica', 'label': 'Stolica', 'keywords': ('stolic', 'chair', 'seat box')},
    {'id': 'suncobran', 'label': 'Suncobran', 'keywords': ('suncobran', 'umbrella', 'brolly')},
    {'id': 'torba', 'label': 'Torba', 'keywords': ('torba', 'bag', 'rucksack', 'carryall')},
    {'id': 'hranilice', 'label': 'Hranilice', 'keywords': ('hranil', 'method feeder', 'cage feeder', 'feeder medium', 'feeder large')},
    {'id': 'signalizatori', 'label': 'Signalizatori', 'keywords': ('signaliz', 'swinger', 'alarm', 'bite')},
    {'id': 'cuvarica', 'label': 'Čuvarica', 'keywords': ('čuvar', 'cuvar', 'keepnet', 'sak')},
    {'id': 'podmetac', 'label': 'Podmetač', 'keywords': ('podmeta', 'unhooking', 'mat')},
    {'id': 'varalice', 'label': 'Varalice', 'keywords': ('varalic', 'softbait', 'wobbl', 'jig')},
    {'id': 'najlon', 'label': 'Najlon', 'keywords': ('najlon', 'mono', 'micron', 'fluoro')},
    {'id': 'spula', 'label': 'Rezervna špula', 'keywords': ('špul', 'spul', 'spool')},
)


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


def _serialize_product(product, request=None, role='', note='', quantity=1):
    price = _product_price(product)
    name = product.naziv
    if quantity and quantity > 1:
        name = f'{name} ×{quantity}'
    return {
        'id': product.pk,
        'name': name,
        'slug': product.slug,
        'url': product.get_absolute_url(),
        'price': str(price.quantize(Decimal('0.01'))),
        'price_display': f'{price.quantize(Decimal("0.01"))} KM'.replace('.', ','),
        'image': _product_image_url(product, request),
        'role': role,
        'note': note,
        'quantity': quantity,
        'in_stock': bool(getattr(product, 'na_stanju', True)),
    }


def _opts(mapping):
    return [
        {
            'id': k,
            'label': f'{v["emoji"]} {v["label"]}'.strip() if v.get('emoji') else v['label'],
        }
        for k, v in mapping.items()
    ]


def _find_categories(slugs=(), name_parts=()):
    q = Q()
    for s in slugs or ():
        q |= Q(slug__iexact=s) | Q(slug__icontains=s)
    for n in name_parts or ():
        q |= Q(naziv__icontains=n)
    if not q:
        return []
    cats = list(Category.objects.filter(q))
    if not cats:
        return []
    parent_ids = [c.pk for c in cats]
    children = list(Category.objects.filter(roditelj_id__in=parent_ids))
    by_id = {c.pk: c for c in cats + children}
    return list(by_id.values())


def _category_url(cats, fallback_q=''):
    if cats:
        try:
            return cats[0].get_absolute_url()
        except Exception:
            pass
    if fallback_q:
        from urllib.parse import quote
        return reverse('home') + f'?q={quote(fallback_q)}'
    return reverse('home')


def _base_qs(require_stock=False):
    qs = (
        Product.objects.filter(aktivan=True)
        .exclude(naziv__icontains='gift card')
        .exclude(naziv__icontains='testni')
        .select_related('brend', 'kategorija')
    )
    if require_stock:
        qs = qs.filter(na_stanju=True)
    return qs


def _keyword_q(keywords):
    q = Q()
    for kw in keywords or ():
        q |= Q(naziv__icontains=kw) | Q(opis__icontains=kw)
        q |= Q(brend__naziv__icontains=kw)
    return q


def _product_kind(product):
    """'masinica' | 'stap' | 'ostalo' — za izbacivanje iz seta ako kupac već ima."""
    name = (getattr(product, 'naziv', '') or '').lower()
    opis = (getattr(product, 'opis', '') or '').lower()[:200]
    text = f'{name} {opis}'
    # mašinica prije štapa (npr. „rod tip reel”)
    reel_kw = (
        'reel', 'mašin', 'masin', 'navijač', 'navijac', 'baitrunner',
        'spool', 'eos ', 'eos-',
    )
    if any(k in text for k in reel_kw):
        return 'masinica'
    rod_kw = (
        'štap', 'stap ', ' stap', 'rod ', ' rod', 'feeder rod', 'carp rod',
        'spinning rod', 'picker', 'teleskop', 'match rod', 'h-cast', 'h cast',
    )
    if any(k in text for k in rod_kw):
        return 'stap'
    # „Danube Feeder 3m” = štap; method feeder 40g = hranilica
    if 'feeder' in text and not any(
        x in text for x in (
            'method feeder', 'open method', 'feeder link', 'feeder bead',
            'cage feeder', 'pellet feeder', 'alloy method', 'alloy open',
        )
    ):
        import re
        if re.search(r'\d+(?:[.,]\d+)?\s*m\b', text) or re.search(r'\d+\s*cm\b', text):
            return 'stap'
        if 'stap' in text or 'štap' in text or 'rod' in text:
            return 'stap'
    # kategorija
    kat = getattr(product, 'kategorija', None)
    if kat is not None:
        kn = (getattr(kat, 'naziv', '') or '').lower()
        ks = (getattr(kat, 'slug', '') or '').lower()
        if 'masin' in kn or 'mašin' in kn or 'reel' in kn or 'masin' in ks:
            return 'masinica'
        if 'stap' in kn or 'štap' in kn or 'rod' in kn or 'stap' in ks or 'feeder-stap' in ks:
            return 'stap'
    return 'ostalo'


def _exclude_kinds_for_owned(owned):
    """Šta izbaciti iz seta prema odgovoru „imaš li opremu”."""
    owned = (owned or '').strip().lower()
    if owned == 'masinica':
        return {'masinica'}
    if owned == 'stap':
        return {'stap'}
    if owned == 'skoro_sve':
        # ima skoro sve — u setu ostavi sitni pribor, izbaci štap i mašinicu
        return {'masinica', 'stap'}
    return set()


def _filter_stavke_by_owned(stavke, owned):
    """Ukloni iz seta ono što kupac već posjeduje."""
    exclude = _exclude_kinds_for_owned(owned)
    if not exclude:
        return list(stavke)
    out = []
    for it in stavke:
        kind = _product_kind(it.product)
        if kind in exclude:
            continue
        out.append(it)
    return out


def _line_regular_total(stavke):
    total = Decimal('0')
    for it in stavke:
        try:
            unit = Decimal(str(it.product.prikazna_cijena))
        except Exception:
            unit = Decimal('0')
        total += unit * Decimal(int(it.kolicina or 1))
    return total.quantize(Decimal('0.01'))


def _line_sale_total(stavke, popust_postotak):
    reg = _line_regular_total(stavke)
    if not popust_postotak or popust_postotak <= 0:
        return reg
    pct = min(Decimal(str(popust_postotak)), Decimal('100'))
    return (reg * (Decimal('1') - pct / Decimal('100'))).quantize(Decimal('0.01'))


def _codes_for_set_choice(fish_key, varalic_style=''):
    """
    Admin kodovi za izabrani tip seta.
    Varaličarski → koristi pod-izbor (lov štuke / soma / UL).
    """
    fish_key = (fish_key or '').strip().lower()
    style = (varalic_style or '').strip().lower()

    if fish_key == VARALICARSKI_CODE or fish_key in VARALIC_SUBTYPE_CODES:
        if style and style in VARALIC_STYLE:
            return list(VARALIC_STYLE[style]['codes'])
        if fish_key in VARALIC_STYLE:
            return list(VARALIC_STYLE[fish_key]['codes'])
        if fish_key in VARALIC_SUBTYPE_CODES:
            return [fish_key]
        # parent varalicarski bez stila — svi pod-kodovi
        codes = [VARALICARSKI_CODE]
        for conf in VARALIC_STYLE.values():
            codes.extend(conf['codes'])
        return list(dict.fromkeys(codes))

    fish = FISH.get(fish_key) or {}
    codes = [fish_key] + list(fish.get('codes') or ())
    # alias: stari "saran" ↔ "saranski"
    if fish_key in ('saran', 'saranski'):
        codes.extend(['saran', 'saranski'])
    return list(dict.fromkeys(c for c in codes if c))


def _set_type_options_from_admin():
    """
    Top-level tipovi setova iz admina.
    Varaličarski podtipovi (stuka/som/ul) se grupišu u jednu opciju.
    """
    try:
        from .models import AdvisorBeginnerFishType
    except Exception:
        return []

    types = list(
        AdvisorBeginnerFishType.objects
        .filter(aktivan=True)
        .order_by('redoslijed', 'naziv')
    )
    if not types:
        return []

    opts = []
    has_varalic = False
    for ft in types:
        code = (ft.code or '').strip().lower()
        if not code:
            continue
        if code == VARALICARSKI_CODE or code in VARALIC_SUBTYPE_CODES:
            has_varalic = True
            continue
        emoji = (ft.emoji or '🎣').strip()
        label = (ft.naziv or code).strip()
        opts.append({
            'id': code,
            'label': f'{emoji} {label}'.strip(),
        })

    if has_varalic:
        # Umetni varaličarski blizu vrha (nakon saranskog ako postoji)
        varalic_opt = {
            'id': VARALICARSKI_CODE,
            'label': '🎣 Varaličarski set',
        }
        insert_at = 0
        for i, o in enumerate(opts):
            if o['id'] in ('saranski', 'saran'):
                insert_at = i + 1
                break
        opts.insert(insert_at, varalic_opt)

    return opts


def _is_varalicarski_choice(fish_key):
    key = (fish_key or '').strip().lower()
    return key == VARALICARSKI_CODE or key in VARALIC_SUBTYPE_CODES


def _varalic_style_options():
    """Opcije: lov štuke / lov soma / UL ribolov."""
    return _opts(VARALIC_STYLE)


def _kits_from_admin(fish_key, request=None, budget_max=None, kit_tier=None, owned=None, varalic_style=''):
    """
    Setovi iz admina za izabrani tip seta, strogo filtrirani budžetom.
    owned=masinica/stap/skoro_sve → te stavke se izbace iz seta (kupac već ima).
    """
    try:
        from .models import AdvisorBeginnerFishType, AdvisorBeginnerSet
    except Exception:
        return []

    codes = _codes_for_set_choice(fish_key, varalic_style=varalic_style or '')
    if not codes:
        codes = [fish_key] if fish_key else []

    fish_types = list(
        AdvisorBeginnerFishType.objects
        .filter(aktivan=True, code__in=codes)
        .prefetch_related(
            Prefetch(
                'setovi',
                queryset=AdvisorBeginnerSet.objects
                .filter(aktivan=True)
                .prefetch_related('stavke__product', 'stavke__product__kategorija')
                .order_by('redoslijed', 'id'),
            ),
        )
        .order_by('redoslijed')
    )

    # Fallback: varaličarski parent ako nema setova na podtipu
    if not fish_types and _is_varalicarski_choice(fish_key):
        fish_types = list(
            AdvisorBeginnerFishType.objects
            .filter(aktivan=True, code=VARALICARSKI_CODE)
            .prefetch_related(
                Prefetch(
                    'setovi',
                    queryset=AdvisorBeginnerSet.objects
                    .filter(aktivan=True)
                    .prefetch_related('stavke__product', 'stavke__product__kategorija')
                    .order_by('redoslijed', 'id'),
                ),
            )
            .order_by('redoslijed')
        )

    kits = []
    for ft in fish_types:
        for s in ft.setovi.all():
            stavke = [
                it for it in s.stavke.all()
                if it.product_id
                and getattr(it.product, 'aktivan', False)
                and getattr(it.product, 'na_stanju', False)
            ]
            # Izbaci štap/mašinicu ako kupac već ima
            stavke = _filter_stavke_by_owned(stavke, owned)
            if not stavke:
                continue
            reg = _line_regular_total(stavke)
            sale = _line_sale_total(stavke, s.popust_postotak)
            # Budžet na preostale stavke (nakon izbacivanja)
            if budget_max is not None and budget_max < Decimal('9000'):
                if sale > budget_max:
                    continue
            products = []
            for item in sorted(stavke, key=lambda x: (x.redoslijed, x.id)):
                kind = _product_kind(item.product)
                products.append(
                    _serialize_product(
                        item.product,
                        request,
                        role=kind if kind != 'ostalo' else 'komplet',
                        quantity=int(item.kolicina or 1),
                    ),
                )
            has_disc = bool(s.popust_postotak and s.popust_postotak > 0)
            note_parts = []
            if owned == 'masinica':
                note_parts.append('bez mašinice (već imaš)')
            elif owned == 'stap':
                note_parts.append('bez štapa (već imaš)')
            elif owned == 'skoro_sve':
                note_parts.append('bez štapa/mašinice')
            kits.append({
                'id': str(s.pk),
                'db_id': s.pk,
                'label': s.naziv,
                'emoji': s.emoji or '🎣',
                'products': products,
                'total': str(sale),
                'total_display': f'{sale} KM'.replace('.', ','),
                'regular_total': str(reg),
                'regular_total_display': f'{reg} KM'.replace('.', ','),
                'discount_percent': float(s.popust_postotak) if has_disc else None,
                'has_discount': has_disc,
                'sort_price': sale,
                'fish_type_name': ft.naziv,
                'owned_note': ', '.join(note_parts),
                'excluded_owned': owned or '',
            })

    kits.sort(key=lambda k: k['sort_price'])

    if kit_tier == 1 and len(kits) > 1:
        kits = kits[: max(1, (len(kits) + 1) // 2)]
    elif kit_tier == 3 and len(kits) > 1:
        mid = len(kits) // 3
        kits = kits[mid:] or kits
    elif kit_tier == 2 and len(kits) > 2:
        n = len(kits)
        kits = kits[max(0, n // 4): max(1, n - n // 4)] or kits

    return kits[:6]


def _budget_options_for_fish(fish_key, varalic_style=''):
    """
    Budžet-opcije koje imaju barem jedan set u bazi za taj tip seta.
    Npr. nema seta ≤80 KM → ne nudi se „Do 80 KM”.
    """
    all_kits = _kits_from_admin(
        fish_key,
        budget_max=None,
        kit_tier=None,
        varalic_style=varalic_style,
    )
    if not all_kits:
        return []
    prices = [k['sort_price'] for k in all_kits]
    opts = []
    for key, conf in BUDGET.items():
        max_b = conf['max']
        if max_b >= Decimal('9000'):
            if prices:
                opts.append({
                    'id': key,
                    'label': f'{conf["emoji"]} {conf["label"]}'.strip(),
                })
            continue
        if any(p <= max_b for p in prices):
            opts.append({
                'id': key,
                'label': f'{conf["emoji"]} {conf["label"]}'.strip(),
            })
    return opts


def _products_by_keywords(keywords, limit=8, require_stock=False):
    if not keywords:
        return []
    qs = _base_qs(require_stock=require_stock).filter(_keyword_q(keywords))
    return list(qs.order_by('-na_stanju', '?')[:limit])


def build_accessory_products(request=None, limit_per=2):
    """Najčešći pribor uz komplet — po kategorijama/keywordima."""
    out = []
    seen = set()
    for acc in ACCESSORIES:
        items = _products_by_keywords(acc['keywords'], limit=limit_per, require_stock=False)
        for p in items:
            if p.pk in seen:
                continue
            seen.add(p.pk)
            out.append(
                _serialize_product(p, request, role='pribor', note=acc['label']),
            )
        if len(out) >= 12:
            break
    return out


def _resolve_item_search(item_key, fish_key=''):
    """
    Kategorije/ključne riječi za pojedinačni artikal.
    Ako je poznata riba (npr. šaran + štap) → npr. Šaranski štapovi.
    """
    conf = SINGLE_ITEMS.get(item_key) or SINGLE_ITEMS['ostalo']
    fish_key = (fish_key or '').strip().lower()
    by_fish = (FISH_ITEM_CATEGORIES.get(item_key) or {}).get(fish_key) or {}
    if by_fish:
        slugs = tuple(dict.fromkeys(tuple(by_fish.get('slugs') or ()) + tuple(conf.get('slugs') or ())))
        names = tuple(dict.fromkeys(tuple(by_fish.get('names') or ()) + tuple(conf.get('names') or ())))
        keywords = tuple(dict.fromkeys(tuple(by_fish.get('keywords') or ()) + tuple(conf.get('keywords') or ())))
        return {
            'slugs': slugs,
            'names': names,
            'keywords': keywords,
            'label': conf.get('label') or item_key,
            'url_home': conf.get('url_home'),
            'prefer_fish': True,
        }
    return {
        'slugs': tuple(conf.get('slugs') or ()),
        'names': tuple(conf.get('names') or ()),
        'keywords': tuple(conf.get('keywords') or ()),
        'label': conf.get('label') or item_key,
        'url_home': conf.get('url_home'),
        'prefer_fish': False,
    }


def _filter_products_by_budget(products, budget_max=None, limit=40):
    """Zadrži artikle ≤ budžet (prikazna cijena). Preko 250 = bez gornje granice."""
    out = []
    strict = budget_max is not None and budget_max < Decimal('9000')
    for p in products:
        price = _product_price(p)
        if strict and price > budget_max:
            continue
        if price <= 0:
            continue
        out.append(p)
        if len(out) >= limit:
            break
    return out


def build_single_item_rec(item_key, request=None, state=None):
    """
    Pojedinačna oprema: kategorija po ribi (npr. šaran → šaranski štapovi)
    + svi artikli u budžetu koji je kupac izabrao.
    """
    state = state or {}
    fish_key = (state.get('fish') or '').strip().lower()
    budget_key = (state.get('budget') or '').strip()
    budget_max = None
    if budget_key and budget_key in BUDGET:
        budget_max = BUDGET[budget_key]['max']

    conf = _resolve_item_search(item_key, fish_key)
    if conf.get('url_home'):
        return {
            'item_label': conf['label'],
            'category_url': reverse('home'),
            'products': [],
            'headline': conf['label'],
            'kits': [],
            'has_offer': False,
        }

    # 1) prvo kategorije za ribu (npr. Šaranski štapovi)
    cats = _find_categories(conf.get('slugs'), conf.get('names'))
    # Ako preferiramo ribu, pokušaj još uži match po nazivu kategorije
    if conf.get('prefer_fish') and fish_key and cats:
        fish_bits = (FISH_ITEM_CATEGORIES.get(item_key) or {}).get(fish_key) or {}
        prefer_names = [n.lower() for n in (fish_bits.get('names') or ())]
        prefer_slugs = [s.lower() for s in (fish_bits.get('slugs') or ())]
        ranked = []
        for c in cats:
            score = 0
            kn = (c.naziv or '').lower()
            ks = (c.slug or '').lower()
            for s in prefer_slugs:
                if s and (ks == s or s in ks):
                    score += 10
            for n in prefer_names:
                if n and n in kn:
                    score += 5
            ranked.append((score, c))
        ranked.sort(key=lambda x: (-x[0], x[1].naziv or ''))
        if ranked and ranked[0][0] > 0:
            # zadrži pogođene + djecu; ostale na kraju
            cats = [c for sc, c in ranked if sc > 0] or [c for _, c in ranked]

    qs = _base_qs(require_stock=True)
    used_category = bool(cats)
    if cats:
        ids = [c.pk for c in cats]
        qs = qs.filter(Q(kategorija_id__in=ids) | Q(kategorija__roditelj_id__in=ids))
    else:
        qs = qs.filter(_keyword_q(conf.get('keywords')))
        used_category = False

    # Učitaj više, pa filtriraj budžet po prikaznoj cijeni
    candidates = list(qs.order_by('-na_stanju', 'cijena', 'naziv')[:120])
    filtered = _filter_products_by_budget(candidates, budget_max=budget_max, limit=40)

    # Ako kategorija postoji ali ništa u budžetu — ne širi na cijelu trgovinu
    # Ako nema kategorije i keyword je prazan rezultat, ostavi prazno
    if not filtered and cats and conf.get('prefer_fish'):
        # drugi pokušaj: samo keyword + budžet (širi net)
        qs2 = _base_qs(require_stock=True).filter(_keyword_q(conf.get('keywords')))
        candidates2 = list(qs2.order_by('-na_stanju', 'cijena', 'naziv')[:120])
        filtered = _filter_products_by_budget(candidates2, budget_max=budget_max, limit=40)
        used_category = False

    products = [
        _serialize_product(p, request, role=item_key)
        for p in filtered
    ]

    fish_label = FISH.get(fish_key, {}).get('label', '')
    budget_label = BUDGET.get(budget_key, {}).get('label', '') if budget_key else ''
    cat_label = cats[0].naziv if cats else conf['label']
    item_label = conf['label']
    if fish_label and item_key in ('stap', 'masinica', 'udice', 'varalice'):
        item_label = f'{conf["label"]} — {fish_label}'
        if budget_label:
            item_label = f'{item_label} · {budget_label}'

    kw = (conf.get('keywords') or ('',))[0]
    headline_parts = [cat_label if used_category and cats else conf['label']]
    if budget_label:
        headline_parts.append(budget_label)

    return {
        'item_label': item_label,
        'category_url': _category_url(cats, fallback_q=kw),
        'category_label': cat_label,
        'products': products,
        'headline': ' · '.join(headline_parts),
        'total_display': '',
        'kits': [],
        'has_offer': bool(products),
        'fish': fish_key,
        'budget_key': budget_key,
        'count': len(products),
    }


def build_recommendation_from_state(state, request=None):
    fish_key = state.get('fish') or state.get('set_type') or 'saranski'
    budget_key = state.get('budget') or '150'
    budget_max = BUDGET.get(budget_key, BUDGET['150'])['max']
    varalic_style = state.get('varalic_style') or ''

    kits = _kits_from_admin(
        fish_key,
        request=request,
        budget_max=budget_max,
        kit_tier=None,
        owned=state.get('owned') or '',
        varalic_style=varalic_style,
    )

    fish_label = FISH.get(fish_key, {}).get('label', '')
    if not fish_label and varalic_style in VARALIC_STYLE:
        fish_label = VARALIC_STYLE[varalic_style]['label']
    if not fish_label:
        fish_label = (state.get('set_type_label') or fish_key or 'Komplet')

    style_label = ''
    if varalic_style in VARALIC_STYLE:
        style_label = VARALIC_STYLE[varalic_style]['label']

    return {
        'fish': fish_key,
        'fish_label': fish_label,
        'headline': '',
        'kits': kits,
        'products': [p for k in kits for p in k.get('products') or []],
        'item_label': fish_label or 'Komplet',
        'style_label': style_label,
        'total_display': kits[0]['total_display'] if kits else '',
        'from_admin': bool(kits),
        'budget_key': budget_key,
        'has_offer': bool(kits),
    }


# Mapiranje koraka → pitanje (za live analitiku)
_STEP_QUESTION = {
    'start': 'Otvorio savjetnik',
    'kit_level': 'Set ili pojedinačno',
    'set_type': 'Tip seta',
    'varalic_style': 'Varaličarski stil',
    'budget': 'Budžet',
    'results': 'Rezultat',
    'single': 'Pojedinačna oprema',
    'post': 'Nakon preporuke',
    # legacy (stari live logovi)
    'experience': 'Iskustvo',
    'fish': 'Tip seta',
    'water': 'Lokacija',
    'technique': 'Tehnika',
    'owned': 'Postojeća oprema',
}


def _answer_label(step, answer):
    """Ljudski čitljiv odgovor za staff live."""
    maps = {
        'budget': BUDGET,
        'kit_level': KIT_LEVEL,
        'varalic_style': VARALIC_STYLE,
        'single': SINGLE_ITEMS,
        'fish': FISH,
        'set_type': FISH,
    }
    m = maps.get(step) or {}
    if answer in m:
        return m[answer].get('label') or answer
    if step == 'set_type' and answer:
        # Naziv iz admina
        try:
            from .models import AdvisorBeginnerFishType
            ft = AdvisorBeginnerFishType.objects.filter(code=answer).first()
            if ft:
                return ft.naziv
        except Exception:
            pass
        if answer == VARALICARSKI_CODE:
            return 'Varaličarski set'
    special = {
        'no_kit': 'Ne želi komplet',
        'view_kit': 'Gleda komplet',
        'accessories_yes': 'Želi pribor',
        'accessories_ask': 'Pribor uz komplet',
        'again': 'Ispočetka',
        'finish': 'Završio',
        'more': 'Još preporuka',
        'continue': 'Nastavi',
    }
    return special.get(answer) or (answer or '—')


def track_advisor_live(request, *, step='', answer='', state=None, payload=None, accepted_set=None):
    """
    Snimi stanje savjetnika na LiveVisitor + staff event (uživo analitika).
    """
    if not request:
        return
    try:
        from django.utils import timezone

        from .cart_tracking import get_cart_session_key
        from .models import LiveVisitor, StaffSiteEvent
    except Exception:
        return

    session_key = ''
    try:
        session_key = get_cart_session_key(request) or ''
    except Exception:
        session_key = (getattr(request.session, 'session_key', None) or '')[:40]
    if not session_key:
        return

    state = state or {}
    payload = payload or {}
    now = timezone.now()
    now_iso = now.isoformat()

    q_label = _STEP_QUESTION.get(step) or step or 'Savjetnik'
    a_label = _answer_label(step, answer) if answer else ''

    lv = LiveVisitor.objects.filter(session_key=session_key).only(
        'pk', 'savjetnik', 'ime', 'email', 'grad',
    ).first()
    if not lv:
        # Heartbeat još nije stigao — snimi minimalni red da live analitika vidi savjetnik
        try:
            user = getattr(request, 'user', None)
            ime = 'Gost'
            email = ''
            if user and getattr(user, 'is_authenticated', False):
                ime = (user.get_full_name() or user.first_name or user.email or 'Kupac')[:120]
                email = (user.email or '')[:254]
            lv, _ = LiveVisitor.objects.get_or_create(
                session_key=session_key[:40],
                defaults={
                    'ime': ime,
                    'email': email,
                    'last_seen': now,
                    'savjetnik': {},
                },
            )
        except Exception:
            return

    data = dict(lv.savjetnik) if isinstance(lv.savjetnik, dict) else {}
    answers = list(data.get('answers') or [])
    if answer and step not in ('start', 'reset', '', 'welcome'):
        # ne dupliciraj isti zadnji odgovor
        last = answers[-1] if answers else None
        if not (last and last.get('step') == step and last.get('answer') == answer):
            answers.append({
                'step': step,
                'q': q_label,
                'a': a_label,
                'answer_id': answer,
                'at': now_iso,
            })
            answers = answers[-20:]

    next_step = payload.get('step') or step
    rec = payload.get('recommendation') or {}
    kits = payload.get('kits') or rec.get('kits') or []
    kit_names = [k.get('label') for k in kits if k.get('label')][:6]

    # offer_shown samo ako stvarno ima setova za prikaz
    offer_shown = bool(data.get('offer_shown'))
    if kits:
        offer_shown = True
    offer_accepted = bool(data.get('offer_accepted'))
    accepted_set_name = data.get('accepted_set') or ''
    if accepted_set:
        offer_accepted = True
        accepted_set_name = accepted_set
        offer_shown = True

    summary_parts = [f'{x.get("q")}: {x.get("a")}' for x in answers[-6:]]
    summary = ' · '.join(summary_parts) if summary_parts else 'U savjetniku'

    data.update({
        'active': True,
        'step': next_step,
        'step_label': _STEP_QUESTION.get(next_step) or next_step,
        'question': (payload.get('messages') or [{}])[0].get('text') or data.get('question') or '',
        'answers': answers,
        'summary': summary[:400],
        'last_answer': a_label,
        'last_q': q_label if answer else data.get('last_q') or '',
        'offer_shown': offer_shown,
        'offer_accepted': offer_accepted,
        'accepted_set': accepted_set_name,
        'kit_names': kit_names or data.get('kit_names') or [],
        'fish': state.get('fish') or data.get('fish') or '',
        'budget': state.get('budget') or data.get('budget') or '',
        'experience': state.get('experience') or data.get('experience') or '',
        'owned': state.get('owned') or data.get('owned') or '',
        'updated_at': now_iso,
    })
    if state.get('owned'):
        data['owned'] = state.get('owned')
    if next_step in ('start',) and answer in ('again', 'reset'):
        data['active'] = True
        data['offer_accepted'] = False

    LiveVisitor.objects.filter(pk=lv.pk).update(
        savjetnik=data,
        last_seen=now,
        trenutno_gleda='Ribolovački savjetnik'[:200],
    )

    # Staff toast / event — pri otvaranju, ključnim odgovorima, ponudi, prihvatu
    fire = False
    naslov = 'Savjetnik'
    poruka = summary[:280]
    if step in ('start', 'reset', '') and not answer:
        fire = True
        poruka = 'Otvorio ribolovački savjetnik'
    elif step == 'budget' and answer and kits:
        fire = True
        naslov = 'Savjetnik — preporuka'
        if kit_names:
            poruka = f'Ponuda setova: {", ".join(kit_names[:3])}'
        else:
            poruka = f'Završio pitanja ({summary[:200]})'
    elif accepted_set:
        fire = True
        naslov = 'Savjetnik — prihvatio set'
        poruka = f'Kupio set: {accepted_set}'
    elif answer == 'no_kit':
        fire = True
        naslov = 'Savjetnik — odbio komplet'
        poruka = 'Ne želi komplet — bira pojedinačno'
    elif step in ('experience', 'fish', 'budget') and answer:
        # lagani update bez spama toasta — samo snimi JSON (već snimljeno)
        fire = False

    if fire:
        try:
            StaffSiteEvent.objects.create(
                tip=StaffSiteEvent.Tip.ADVISOR,
                naslov=naslov[:120],
                poruka=poruka[:300],
                ime=(lv.ime or '')[:120],
                email=(lv.email or '')[:254],
                grad=(lv.grad or '')[:100],
                session_key=session_key[:40],
            )
        except Exception:
            pass


def process_step(step, answer, state=None, request=None):
    state = dict(state or {})
    answer = (answer or '').strip().lower()
    step = (step or 'start').strip().lower()
    _incoming_step = step
    _incoming_answer = answer

    def bot(text, options=None, next_step=None, recommendation=None, done=False, kits=None, extra=None):
        payload = {
            'ok': True,
            'messages': [{'role': 'bot', 'text': text}],
            'options': options or [],
            'state': state,
            'step': next_step or step,
            'done': done,
        }
        if recommendation is not None:
            payload['recommendation'] = recommendation
        if kits is not None:
            payload['kits'] = kits
        if extra:
            payload.update(extra)
        try:
            track_advisor_live(
                request,
                step=_incoming_step,
                answer=_incoming_answer,
                state=state,
                payload=payload,
            )
        except Exception:
            pass
        return payload

    def ask_budget():
        fish_key = state.get('fish') or state.get('set_type') or ''
        style = state.get('varalic_style') or ''
        budget_opts = _budget_options_for_fish(fish_key, varalic_style=style)
        if not budget_opts:
            return bot(
                'Za ovaj tip seta trenutno nema kompleta u ponudi.\n'
                'Šta tačno tražiš?',
                options=_opts(SINGLE_ITEMS) + [
                    {'id': 'again', 'label': '🔄 Ispočetka'},
                ],
                next_step='single',
            )
        return bot(
            'Koliki budžet imaš?',
            options=budget_opts,
            next_step='budget',
        )

    def show_results():
        rec = build_recommendation_from_state(state, request=request)
        kits = rec.get('kits') or []
        if not kits:
            return bot(
                'Za tvoj izbor trenutno nema kompleta u ponudi.\n'
                'Šta tačno tražiš?',
                options=_opts(SINGLE_ITEMS) + [
                    {'id': 'again', 'label': '🔄 Ispočetka'},
                ],
                next_step='single',
                recommendation={'kits': [], 'products': [], 'has_offer': False},
                kits=[],
            )
        opts = [
            {'id': 'no_kit', 'label': 'Ne želim komplet'},
            {'id': 'accessories_ask', 'label': '✅ Pribor uz komplet'},
            {'id': 'again', 'label': '🔄 Ispočetka'},
        ]
        if kits[0].get('db_id'):
            opts.insert(0, {
                'id': 'view_kit',
                'label': f'👉 Pogledaj: {kits[0].get("label", "komplet")}',
            })
        return bot(
            '',
            options=opts,
            next_step='results',
            recommendation=rec,
            kits=kits,
            done=False,
        )

    # ── START → set na akciji ili pojedinačno ─────────────────────
    if step in ('start', 'reset', '', 'welcome'):
        state.clear()
        return bot(
            'Želite li setove za ribolov na akciji ili pojedinačno kupovati?',
            options=_opts(KIT_LEVEL),
            next_step='kit_level',
        )

    # ── 1 KIT LEVEL (set / pojedinačno) ────────────────────────────
    if step == 'kit_level':
        if answer not in KIT_LEVEL:
            return bot(
                'Želite li setove za ribolov na akciji ili pojedinačno kupovati?',
                options=_opts(KIT_LEVEL),
                next_step='kit_level',
            )
        state['kit_level'] = answer
        if answer == 'pojedinacno' or KIT_LEVEL[answer].get('mode') == 'single':
            return bot(
                'Šta tačno tražiš?',
                options=_opts(SINGLE_ITEMS),
                next_step='single',
            )
        set_opts = _set_type_options_from_admin()
        if not set_opts:
            return bot(
                'Trenutno nema setova u ponudi.\nŠta tačno tražiš?',
                options=_opts(SINGLE_ITEMS) + [
                    {'id': 'again', 'label': '🔄 Ispočetka'},
                ],
                next_step='single',
            )
        return bot(
            'Koji tip seta te zanima?',
            options=set_opts,
            next_step='set_type',
        )

    # ── 2 SET TYPE (iz admina) ─────────────────────────────────────
    if step == 'set_type':
        set_opts = _set_type_options_from_admin()
        allowed = {o['id'] for o in set_opts}
        if answer not in allowed:
            if not set_opts:
                return bot(
                    'Trenutno nema setova u ponudi.\nŠta tačno tražiš?',
                    options=_opts(SINGLE_ITEMS),
                    next_step='single',
                )
            return bot(
                'Koji tip seta te zanima?',
                options=set_opts,
                next_step='set_type',
            )
        state['set_type'] = answer
        state['fish'] = answer
        for o in set_opts:
            if o['id'] == answer:
                state['set_type_label'] = o.get('label', answer)
                break

        # Samo varaličarski: prije budžeta pitaj stil lova
        if _is_varalicarski_choice(answer):
            return bot(
                'Šta te zanima unutar varaličarskog seta?',
                options=_varalic_style_options(),
                next_step='varalic_style',
            )
        return ask_budget()

    # ── 3 VARALIC STYLE (lov štuke / soma / UL) ────────────────────
    if step == 'varalic_style':
        if answer not in VARALIC_STYLE:
            return bot(
                'Šta te zanima unutar varaličarskog seta?',
                options=_varalic_style_options(),
                next_step='varalic_style',
            )
        state['varalic_style'] = answer
        state['fish'] = answer
        state['set_type'] = VARALICARSKI_CODE
        state['set_type_label'] = VARALIC_STYLE[answer]['label']
        return ask_budget()

    # ── 4 BUDGET → RESULTS ─────────────────────────────────────────
    if step == 'budget':
        if answer == 'no_kit':
            return bot(
                'Šta tačno tražiš?',
                options=_opts(SINGLE_ITEMS),
                next_step='single',
            )
        fish_key = state.get('fish') or state.get('set_type') or ''
        style = state.get('varalic_style') or ''
        budget_opts = _budget_options_for_fish(fish_key, varalic_style=style)
        allowed = {o['id'] for o in budget_opts}
        if answer not in allowed:
            if not budget_opts:
                return bot(
                    'Za ovaj tip seta trenutno nema kompleta u ponudi.\n'
                    'Šta tačno tražiš?',
                    options=_opts(SINGLE_ITEMS),
                    next_step='single',
                )
            return bot(
                'Koliki budžet imaš?',
                options=budget_opts,
                next_step='budget',
            )
        bmax = BUDGET.get(answer, {}).get('max')
        matching = _kits_from_admin(
            fish_key,
            budget_max=bmax,
            kit_tier=None,
            varalic_style=style,
        )
        if not matching:
            return bot(
                'U tom budžetu trenutno nema kompleta.\n'
                'Izaberi drugi budžet ili pojedinačnu opremu:',
                options=budget_opts + [
                    {'id': 'no_kit', 'label': 'Pojedinačna oprema'},
                ],
                next_step='budget',
            )
        state['budget'] = answer
        return show_results()

    # ── RESULTS ACTIONS ────────────────────────────────────────────
    if step == 'results':
        if answer == 'view_kit':
            rec = build_recommendation_from_state(state, request=request)
            return bot(
                'Iznad su artikli kompleta — možeš Kupiti set ili pojedinačno.\n\n'
                'Da li želiš da ti odmah pokažem najčešće proizvode koje ribolovci kupuju uz ovaj komplet?',
                options=[
                    {'id': 'accessories_yes', 'label': 'DA'},
                    {'id': 'finish', 'label': 'Ne, hvala'},
                    {'id': 'no_kit', 'label': 'Ne želim komplet'},
                    {'id': 'again', 'label': '🔄 Ispočetka'},
                ],
                next_step='results',
                recommendation=rec,
                kits=(rec.get('kits') or []),
            )

        if answer in ('accessories_ask', 'accessories_yes'):
            acc = build_accessory_products(request=request)
            rec = {
                'item_label': 'Pribor uz komplet',
                'products': acc,
                'kits': [],
                'headline': 'Pribor',
                'total_display': '',
            }
            return bot(
                'Evo što ribolovci često uzimaju uz komplet — dodaj u korpu što ti treba:',
                options=[
                    {'id': 'finish', 'label': 'Završi'},
                    {'id': 'more', 'label': '👉 Prikaži još preporuka'},
                    {'id': 'no_kit', 'label': 'Pojedinačna oprema'},
                    {'id': 'again', 'label': '🔄 Ispočetka'},
                ],
                next_step='post',
                recommendation=rec,
            )

        if answer == 'no_kit':
            return bot(
                'Nema problema.\nŠta tačno tražiš?',
                options=_opts(SINGLE_ITEMS),
                next_step='single',
            )

        if answer == 'finish':
            return bot(
                '🎣 Nadam se da sam pomogao.\n\n'
                'Ako želiš, mogu ti preporučiti još opreme ili pribor uz odabrani komplet.',
                options=[
                    {'id': 'more', 'label': '👉 Prikaži još preporuka'},
                    {'id': 'accessories_yes', 'label': 'Pribor uz komplet'},
                    {'id': 'again', 'label': '🔄 Ispočetka'},
                ],
                next_step='post',
            )

        if answer == 'again':
            state.clear()
            return process_step('start', '', state, request=request)

        rec = build_recommendation_from_state(state, request=request)
        return bot(
            'Šta dalje?',
            options=[
                {'id': 'accessories_yes', 'label': 'DA — pribor uz komplet'},
                {'id': 'no_kit', 'label': 'Ne želim komplet'},
                {'id': 'finish', 'label': 'Završi'},
                {'id': 'again', 'label': '🔄 Ispočetka'},
            ],
            next_step='results',
            recommendation=rec,
            kits=rec.get('kits'),
        )

    # ── SINGLE ITEM ────────────────────────────────────────────────
    if step == 'single':
        if answer not in SINGLE_ITEMS:
            return bot(
                'Šta tačno tražiš?',
                options=_opts(SINGLE_ITEMS),
                next_step='single',
            )
        state['single_item'] = answer
        rec = build_single_item_rec(answer, request=request, state=state)
        fish_label = FISH.get(state.get('fish') or '', {}).get('label', '')
        if not fish_label and state.get('varalic_style') in VARALIC_STYLE:
            fish_label = VARALIC_STYLE[state['varalic_style']]['label']
        budget_label = BUDGET.get(state.get('budget') or '', {}).get('label', '')
        cat_label = rec.get('category_label') or rec.get('item_label') or ''
        n = int(rec.get('count') or len(rec.get('products') or []))

        if n:
            if answer == 'stap' and fish_label:
                msg = f'Evo štapova za {fish_label.lower()}'
                if budget_label:
                    msg += f' ({budget_label.lower()})'
                if cat_label:
                    msg += f'\nKategorija: {cat_label}'
                msg += f'\n{n} artikal(a) na stanju — možeš kupiti pojedinačno.'
            else:
                msg = rec.get('item_label') or 'Oprema'
                if budget_label:
                    msg += f' ({budget_label})'
                msg += f'\n{n} artikal(a) u ponudi.'
        else:
            msg = (
                f'Za tvoj izbor trenutno nema artikala'
                f'{(" u budžetu " + budget_label) if budget_label else ""}'
                f'{(" za " + fish_label.lower()) if fish_label else ""}.\n'
                'Probaj drugi budžet ili otvori cijelu kategoriju.'
            )

        opts = []
        if rec.get('category_url'):
            opts.append({
                'id': 'cat',
                'label': f'📦 Otvori: {rec.get("category_label") or rec.get("item_label")}',
                'url': rec['category_url'],
            })
        opts.extend([
            {'id': 'again', 'label': '🔄 Ispočetka'},
        ])
        return bot(
            msg,
            options=opts,
            next_step='post',
            recommendation=rec,
            done=True,
        )

    # ── POST / MORE ────────────────────────────────────────────────
    if step == 'post':
        if answer in ('more', 'accessories_yes'):
            acc = build_accessory_products(request=request)
            rec = {
                'item_label': 'Još preporuka',
                'products': acc,
                'kits': [],
                'headline': 'Pribor',
            }
            return bot(
                'Još pribora koji se često uzima uz opremu:',
                options=[
                    {'id': 'finish', 'label': 'Završi'},
                    {'id': 'no_kit', 'label': 'Pojedinačna oprema'},
                    {'id': 'again', 'label': '🔄 Ispočetka'},
                ],
                next_step='post',
                recommendation=rec,
            )
        if answer == 'no_kit':
            return bot(
                'Šta tačno tražiš?',
                options=_opts(SINGLE_ITEMS),
                next_step='single',
            )
        if answer == 'finish':
            return bot(
                '🎣 Nadam se da sam pomogao. Sretno na vodi!',
                options=[{'id': 'again', 'label': '🔄 Novi savjet'}],
                next_step='post',
                done=True,
            )
        if answer == 'again':
            state.clear()
            return process_step('start', '', state, request=request)

    if answer in ('again', 'reset'):
        state.clear()
        return process_step('start', '', {}, request=request)

    return process_step('start', '', {}, request=request)
