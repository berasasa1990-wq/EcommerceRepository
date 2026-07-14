"""
Ribolovački savjetnik — razgovor s iskusnim ribolovcem.
Cilj: 30–60 s, malo pitanja, setovi iz admina.

Tok:
  experience → fish → water → budget
  → [samo iskusan] technique
  → kit_level → owned → results
  → accessories / single-item / again
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db.models import Q, Prefetch
from django.urls import reverse

from .models import Category, Product

# ─── Opcije ────────────────────────────────────────────────────────

EXPERIENCE = {
    'prvi': {
        'label': 'Prvi put kupujem opremu',
        'emoji': '🟢',
        'level': 'beginner',
    },
    'povremeno': {
        'label': 'Lovim povremeno',
        'emoji': '🔵',
        'level': 'mid',
    },
    'iskusan': {
        'label': 'Iskusan sam ribolovac',
        'emoji': '🔴',
        'level': 'pro',
    },
}

FISH = {
    'saran': {'label': 'Šaran', 'emoji': '🐟', 'codes': ('saran',)},
    'som': {'label': 'Som', 'emoji': '🐟', 'codes': ('som',)},
    'stuka': {'label': 'Štuka', 'emoji': '🐟', 'codes': ('stuka',)},
    'smud': {'label': 'Smuđ', 'emoji': '🐟', 'codes': ('smud', 'stuka')},
    'pastrmka': {'label': 'Pastrmka', 'emoji': '🐟', 'codes': ('pastrmka',)},
    'bijela': {'label': 'Bijela riba', 'emoji': '🐟', 'codes': ('bijela',)},
    'vise': {'label': 'Više vrsta ribe', 'emoji': '🐟', 'codes': ('saran', 'bijela', 'stuka')},
}

WATER = {
    'jezero': {'label': 'Jezero', 'emoji': '🏞'},
    'rijeka': {'label': 'Rijeka', 'emoji': '🌊'},
    'bara': {'label': 'Bara', 'emoji': '🟤'},
    'camac': {'label': 'Čamac', 'emoji': '🚤'},
    'sve': {'label': 'Sve pomalo', 'emoji': '❓'},
}

BUDGET = {
    '80': {'label': 'Do 80 KM', 'emoji': '💰', 'max': Decimal('80')},
    '150': {'label': 'Do 150 KM', 'emoji': '💰', 'max': Decimal('150')},
    '250': {'label': 'Do 250 KM', 'emoji': '💰', 'max': Decimal('250')},
    '250plus': {'label': 'Preko 250 KM', 'emoji': '💰', 'max': Decimal('9999')},
}

TECHNIQUE = {
    'teleskop': {'label': 'Teleskopski štap', 'emoji': '🎣'},
    'feeder': {'label': 'Feeder', 'emoji': '🎣'},
    'saran_tech': {'label': 'Šaran', 'emoji': '🎣'},
    'varalica': {'label': 'Varaličarenje', 'emoji': '🎣'},
    'som_tech': {'label': 'Som', 'emoji': '🎣'},
    'plovak': {'label': 'Plovak', 'emoji': '🎣'},
    'ne_znam': {'label': 'Ne znam', 'emoji': '🎣'},
}

KIT_LEVEL = {
    'osnovni': {'label': 'Samo osnovni komplet', 'emoji': '✅', 'tier': 1},
    'pribor': {'label': 'Komplet sa priborom', 'emoji': '✅', 'tier': 2},
    'profesionalno': {'label': 'Profesionalnu opremu', 'emoji': '✅', 'tier': 3},
}

OWNED = {
    'nista': {'label': 'Nemam ništa', 'emoji': ''},
    'stap': {'label': 'Imam štap', 'emoji': ''},
    'masinica': {'label': 'Imam mašinicu', 'emoji': ''},
    'skoro_sve': {'label': 'Imam skoro sve', 'emoji': ''},
}

SINGLE_ITEMS = {
    'stap': {
        'label': 'Štap',
        'emoji': '🎣',
        'slugs': ('feeder-stapovi', 'stapovi-za-varalicu', 'stapovi'),
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


def _kits_from_admin(fish_key, request=None, budget_max=None, kit_tier=None):
    """
    Setovi iz admina za TAČNU vrstu ribe, strogo filtrirani budžetom.
    Nema seta do npr. 80 KM → prazna lista (ne nudi se lažna ponuda).
    """
    try:
        from .models import AdvisorBeginnerFishType, AdvisorBeginnerSet
    except Exception:
        return []

    fish = FISH.get(fish_key) or {}
    # Primarni kod je izbor kupca; codes su samo aliasi iste vrste (ne druge ribe)
    codes = list(dict.fromkeys([fish_key] + list(fish.get('codes') or ())))
    # Za „više vrsta” dozvoli mapirane kodove; inače samo tačan code
    if fish_key != 'vise':
        codes = [fish_key]

    fish_types = list(
        AdvisorBeginnerFishType.objects
        .filter(aktivan=True, code__in=codes)
        .prefetch_related(
            Prefetch(
                'setovi',
                queryset=AdvisorBeginnerSet.objects
                .filter(aktivan=True)
                .prefetch_related('stavke__product')
                .order_by('redoslijed', 'id'),
            ),
        )
        .order_by('redoslijed')
    )
    # BEZ fallbacka na drugu vrstu ribe — ako nema setova za šarana, nema ponude za šarana

    kits = []
    for ft in fish_types:
        for s in ft.setovi.all():
            stavke = [
                it for it in s.stavke.all()
                if it.product_id
                and getattr(it.product, 'aktivan', False)
                and getattr(it.product, 'na_stanju', False)
            ]
            if not stavke:
                continue
            reg = s.regularni_iznos()
            sale = s.snizeni_iznos()
            # Strogi budžet: snizena cijena seta mora stati u odabrani limit
            if budget_max is not None and budget_max < Decimal('9000'):
                if sale > budget_max:
                    continue
            products = []
            for item in sorted(stavke, key=lambda x: (x.redoslijed, x.id)):
                products.append(
                    _serialize_product(
                        item.product,
                        request,
                        role='komplet',
                        quantity=int(item.kolicina or 1),
                    ),
                )
            has_disc = s.ima_popust()
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
            })

    kits.sort(key=lambda k: k['sort_price'])

    # kit_tier: uzmi slice među setovima koji VEĆ prolaze budžet
    if kit_tier == 1 and len(kits) > 1:
        kits = kits[: max(1, (len(kits) + 1) // 2)]
    elif kit_tier == 3 and len(kits) > 1:
        mid = len(kits) // 3
        kits = kits[mid:] or kits
    elif kit_tier == 2 and len(kits) > 2:
        n = len(kits)
        kits = kits[max(0, n // 4): max(1, n - n // 4)] or kits

    return kits[:6]


def _budget_options_for_fish(fish_key):
    """
    Budžet-opcije koje imaju barem jedan set u bazi za tu ribu.
    Npr. nema seta ≤80 KM za šarana → ne nudi se „Do 80 KM”.
    """
    all_kits = _kits_from_admin(fish_key, budget_max=None, kit_tier=None)
    if not all_kits:
        return []
    prices = [k['sort_price'] for k in all_kits]
    opts = []
    for key, conf in BUDGET.items():
        max_b = conf['max']
        if max_b >= Decimal('9000'):
            # „Preko 250 KM” — samo ako postoji set iznad 250 ili bilo koji set
            # (kupac s velikim budžetom može uzeti i jeftiniji set)
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


def build_single_item_rec(item_key, request=None):
    conf = SINGLE_ITEMS.get(item_key) or SINGLE_ITEMS['ostalo']
    if conf.get('url_home'):
        return {
            'item_label': conf['label'],
            'category_url': reverse('home'),
            'products': [],
            'headline': conf['label'],
        }
    cats = _find_categories(conf.get('slugs'), conf.get('names'))
    qs = _base_qs(require_stock=False)
    if cats:
        ids = [c.pk for c in cats]
        qs = qs.filter(Q(kategorija_id__in=ids) | Q(kategorija__roditelj_id__in=ids))
    else:
        qs = qs.filter(_keyword_q(conf.get('keywords')))
    products = [
        _serialize_product(p, request, role=item_key)
        for p in qs.order_by('-na_stanju', 'naziv')[:12]
    ]
    kw = (conf.get('keywords') or ('',))[0]
    return {
        'item_label': conf['label'],
        'category_url': _category_url(cats, fallback_q=kw),
        'category_label': cats[0].naziv if cats else conf['label'],
        'products': products,
        'headline': conf['label'],
        'total_display': '',
    }


def build_recommendation_from_state(state, request=None):
    fish_key = state.get('fish') or 'saran'
    budget_key = state.get('budget') or '150'
    budget_max = BUDGET.get(budget_key, BUDGET['150'])['max']
    kit_key = state.get('kit_level') or 'osnovni'
    tier = KIT_LEVEL.get(kit_key, KIT_LEVEL['osnovni'])['tier']
    exp = state.get('experience') or 'prvi'
    level = EXPERIENCE.get(exp, EXPERIENCE['prvi'])['level']
    technique = state.get('technique') or ''

    # "Ne znam" tehniku → početnički setovi
    force_beginner = (
        level == 'beginner'
        or technique == 'ne_znam'
        or not technique
    )

    kits = _kits_from_admin(
        fish_key,
        request=request,
        budget_max=budget_max,
        kit_tier=tier if force_beginner else (3 if level == 'pro' else tier),
    )
    # Ako tier-slice isprazni listu a ima setova u budžetu — vrati sve u budžetu
    if not kits:
        kits = _kits_from_admin(
            fish_key,
            request=request,
            budget_max=budget_max,
            kit_tier=None,
        )

    fish_label = FISH.get(fish_key, {}).get('label', '')
    return {
        'fish': fish_key,
        'fish_label': fish_label,
        'headline': '',
        'kits': kits,
        'products': [p for k in kits for p in k.get('products') or []],
        'item_label': fish_label or 'Komplet',
        'style_label': EXPERIENCE.get(exp, {}).get('label', ''),
        'total_display': kits[0]['total_display'] if kits else '',
        'from_admin': bool(kits),
        'budget_key': budget_key,
        'has_offer': bool(kits),
    }


# Mapiranje koraka → pitanje (za live analitiku)
_STEP_QUESTION = {
    'start': 'Otvorio savjetnik',
    'experience': 'Iskustvo',
    'fish': 'Riba',
    'water': 'Lokacija',
    'budget': 'Budžet',
    'technique': 'Tehnika',
    'kit_level': 'Tip kompleta',
    'owned': 'Postojeća oprema',
    'results': 'Rezultat',
    'single': 'Pojedinačna oprema',
    'post': 'Nakon preporuke',
}


def _answer_label(step, answer):
    """Ljudski čitljiv odgovor za staff live."""
    maps = {
        'experience': EXPERIENCE,
        'fish': FISH,
        'water': WATER,
        'budget': BUDGET,
        'technique': TECHNIQUE,
        'kit_level': KIT_LEVEL,
        'owned': OWNED,
        'single': SINGLE_ITEMS,
    }
    m = maps.get(step) or {}
    if answer in m:
        return m[answer].get('label') or answer
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
        'updated_at': now_iso,
    })
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
    elif step == 'owned' and answer:
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

    def after_budget_or_technique():
        """Sljedeći korak nakon budžeta (ili tehnike)."""
        return bot(
            'Šta želiš kupiti?',
            options=_opts(KIT_LEVEL),
            next_step='kit_level',
        )

    # ── START → odmah prvo pitanje (bez „Počni”) ─────────────────
    if step in ('start', 'reset', '', 'welcome'):
        state.clear()
        return bot(
            'Koliko iskustva imaš?',
            options=_opts(EXPERIENCE),
            next_step='experience',
        )

    # ── 1 EXPERIENCE ───────────────────────────────────────────────
    if step == 'experience':
        if answer not in EXPERIENCE:
            return bot(
                'Koliko iskustva imaš?',
                options=_opts(EXPERIENCE),
                next_step='experience',
            )
        state['experience'] = answer
        return bot(
            'Šta najčešće loviš?',
            options=_opts(FISH),
            next_step='fish',
        )

    # ── 2 FISH ─────────────────────────────────────────────────────
    if step == 'fish':
        if answer not in FISH:
            return bot(
                'Šta najčešće loviš?',
                options=_opts(FISH),
                next_step='fish',
            )
        state['fish'] = answer
        return bot(
            'Gdje najčešće pecaš?',
            options=_opts(WATER),
            next_step='water',
        )

    # ── 3 WATER ────────────────────────────────────────────────────
    if step == 'water':
        if answer not in WATER:
            return bot(
                'Gdje najčešće pecaš?',
                options=_opts(WATER),
                next_step='water',
            )
        state['water'] = answer
        fish_key = state.get('fish') or 'saran'
        budget_opts = _budget_options_for_fish(fish_key)
        if not budget_opts:
            # Nema nijednog seta u bazi za ovu ribu — ne nudi budžete lažno
            state['budget'] = ''
            return bot(
                'Za ovu vrstu ribe trenutno nema kompleta u ponudi.\n'
                'Šta tačno tražiš?',
                options=_opts(SINGLE_ITEMS),
                next_step='single',
            )
        return bot(
            'Koliki budžet imaš?',
            options=budget_opts,
            next_step='budget',
        )

    # ── 4 BUDGET ───────────────────────────────────────────────────
    if step == 'budget':
        fish_key = state.get('fish') or 'saran'
        budget_opts = _budget_options_for_fish(fish_key)
        allowed = {o['id'] for o in budget_opts}
        if answer not in allowed:
            if not budget_opts:
                return bot(
                    'Za ovu vrstu ribe trenutno nema kompleta u ponudi.\n'
                    'Šta tačno tražiš?',
                    options=_opts(SINGLE_ITEMS),
                    next_step='single',
                )
            return bot(
                'Koliki budžet imaš?',
                options=budget_opts,
                next_step='budget',
            )
        # Još jednom: mora postojati barem 1 set u tom budžetu
        bmax = BUDGET.get(answer, {}).get('max')
        matching = _kits_from_admin(fish_key, budget_max=bmax, kit_tier=None)
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
        exp = state.get('experience') or 'prvi'
        # Tehniku pitamo samo iskusne
        if exp == 'iskusan':
            return bot(
                'Koji način ribolova preferiraš?',
                options=_opts(TECHNIQUE),
                next_step='technique',
            )
        return after_budget_or_technique()

    # ── 5 TECHNIQUE (samo iskusan) ─────────────────────────────────
    if step == 'technique':
        if answer not in TECHNIQUE:
            return bot(
                'Koji način ribolova preferiraš?',
                options=_opts(TECHNIQUE),
                next_step='technique',
            )
        state['technique'] = answer
        return after_budget_or_technique()

    # ── 6 KIT LEVEL ────────────────────────────────────────────────
    if step == 'kit_level':
        if answer not in KIT_LEVEL:
            return bot(
                'Šta želiš kupiti?',
                options=_opts(KIT_LEVEL),
                next_step='kit_level',
            )
        state['kit_level'] = answer
        return bot(
            'Da li već posjeduješ nešto od opreme?',
            options=_opts(OWNED),
            next_step='owned',
        )

    # ── 7 OWNED → RESULTS ──────────────────────────────────────────
    if step == 'owned':
        if answer not in OWNED:
            return bot(
                'Da li već posjeduješ nešto od opreme?',
                options=_opts(OWNED),
                next_step='owned',
            )
        state['owned'] = answer
        rec = build_recommendation_from_state(state, request=request)
        kits = rec.get('kits') or []

        # Nema seta u budžetu / za ribu → ne prikazuj praznu „ponudu kompleta”
        if not kits:
            return bot(
                'Za tvoj izbor (riba + budžet) trenutno nema kompleta u ponudi.\n'
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
            '',  # samo setovi, bez teksta
            options=opts,
            next_step='results',
            recommendation=rec,
            kits=kits,
            done=False,
        )

    # ── RESULTS ACTIONS ────────────────────────────────────────────
    if step == 'results':
        if answer == 'view_kit':
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
                recommendation=build_recommendation_from_state(state, request=request),
                kits=(build_recommendation_from_state(state, request=request).get('kits') or []),
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

        # default keep results
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

    # ── SINGLE ITEM (ne želim komplet) ─────────────────────────────
    if step == 'single':
        if answer not in SINGLE_ITEMS:
            return bot(
                'Šta tačno tražiš?',
                options=_opts(SINGLE_ITEMS),
                next_step='single',
            )
        rec = build_single_item_rec(answer, request=request)
        opts = []
        if rec.get('category_url'):
            opts.append({
                'id': 'cat',
                'label': f'📦 Otvori: {rec.get("category_label") or rec.get("item_label")}',
                'url': rec['category_url'],
            })
        opts.extend([
            {'id': 'more', 'label': '👉 Prikaži još preporuka'},
            {'id': 'again', 'label': '🔄 Ispočetka'},
        ])
        return bot(
            rec.get('item_label') or 'Oprema',
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
        return process_step('start', '', state, request=request)

    return process_step('start', '', {}, request=request)
