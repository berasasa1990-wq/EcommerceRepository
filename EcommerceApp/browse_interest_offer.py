"""
AI prodaja — prati kupca i šalje personalizovanu popup ponudu.

Pravila:
- Max 2 popup ponude po posjeti
- Razmak min ~3 min između 1. i 2. ponude
- 1 ili 2 artikla (zavisi od gledanja)
- Popust nikad preko 10%
- Prva: kad AI osjeti namjeru (~40 s high-intent / ~2 min inače)
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Q
from django.utils import timezone

from .cart_tracking import get_cart_session_key
from .live_visitors import _normalize_category_views, _normalize_product_views
from .models import LiveVisitor, LiveVisitorOffer, Product, ProductVariation, SiteSettings
from .models import _izracunaj_akcijsku_od_postotka

SESSION_STATE_KEY = 'browse_interest_state'
SESSION_CLAIMED_IDS_KEY = 'browse_interest_claimed_ids'
_LEGACY_OFFER_KEY = 'browse_interest_offer'
_LEGACY_DISMISSED_KEY = 'browse_interest_offer_dismissed'

# Marker u LiveVisitorOffer.aktivacioni_kod — staff praćenje
AUTO_BROWSE_CODE = 'AUTO-BROWSE'  # legacy / staff tracking
AI_PRODAJA_CODE = 'AI-PRODAJA'

DEFAULT_DISCOUNT = Decimal('10')
AI_MAX_DISCOUNT = Decimal('10')
MIN_PRODUCT_VIEWS_PRIORITY = 2
MIN_CATEGORY_VIEWS = 1
# Uvijek max 2 artikla u jednoj AI ponudi
MAX_RECOMMENDATIONS = 2
MAX_RECOMMENDATIONS_MOBILE = 2
# Max 2 popup-a po posjeti
MAX_OFFERS_PER_SESSION = 2
FIRST_OFFER_AFTER_MINUTES = 2
HIGH_INTENT_OFFER_SECONDS = 45
HIGH_INTENT_SCORE = 55
# Minimalni razmak između zatvaranja 1. i otvaranja 2. ponude
MIN_GAP_BETWEEN_OFFERS_SECONDS = 180  # 3 min
SECOND_OFFER_AFTER_MINUTES = 5  # od ulaska na sajt (uz min gap)
OFFER_EVERY_MINUTES = 2  # legacy
OFFER_TIMER_MINUTES = 3
OFFER_TTL_MINUTES = 3

_MOBILE_UA_TOKENS = (
    'mobile', 'android', 'iphone', 'ipod', 'ipad', 'webos',
    'blackberry', 'iemobile', 'opera mini', 'opera mobi',
)


def _is_mobile_request(request):
    """Detekcija mobitela (UA) radi manjeg broja artikala u 2-min ponudi."""
    if not request:
        return False
    # Klijent može poslati X-Viewport-Mobile: 1 iz live-offer-poll.js
    try:
        hint = (request.headers.get('X-Viewport-Mobile') or request.META.get('HTTP_X_VIEWPORT_MOBILE') or '').strip()
        if hint in ('1', 'true', 'yes'):
            return True
        if hint in ('0', 'false', 'no'):
            return False
    except Exception:
        pass
    ua = (request.META.get('HTTP_USER_AGENT') or '').lower()
    return any(token in ua for token in _MOBILE_UA_TOKENS)


def _rec_limit(request=None, visitor=None):
    """1 ili 2 artikla prema ponašanju (hard max 2)."""
    if visitor is not None:
        return max(1, min(2, _ai_product_count(visitor, request)))
    if request is not None and _is_mobile_request(request):
        return MAX_RECOMMENDATIONS_MOBILE
    return MAX_RECOMMENDATIONS


def _clamp_percent(value):
    """AI prodaja: nikad preko 10%."""
    try:
        percent = Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        percent = Decimal('0')
    if percent < 0:
        return Decimal('0')
    if percent > AI_MAX_DISCOUNT:
        return AI_MAX_DISCOUNT
    return percent.quantize(Decimal('0.01'))


def _settings():
    postavke = SiteSettings.load()
    aktivan = bool(getattr(postavke, 'browse_interest_popup_aktivan', True))
    percent = _clamp_percent(
        getattr(postavke, 'browse_interest_popust', None) or DEFAULT_DISCOUNT,
    )
    return aktivan, percent


def _ai_product_count(visitor, request=None):
    """
    1 artikal ako je fokus jasan (jedan artikal / skoro-korpa / jak povratak).
    2 artikla ako gleda više stvari / kategorija.
    """
    products = _normalize_product_views(getattr(visitor, 'pregledani_proizvodi', None))
    categories = _normalize_category_views(getattr(visitor, 'pregledane_kategorije', None))
    n = len(products)
    if n <= 1:
        return 1
    max_views = max((int(p.get('views') or 1) for p in products), default=1)
    # Jak fokus na jedan artikal (3+ ulaza) → samo taj
    if max_views >= 3:
        return 1
    almost = []
    try:
        from .almost_cart import get_almost_cart_products
        almost = get_almost_cart_products(visitor) or []
    except Exception:
        almost = []
    if almost and max_views >= 2:
        return 1
    # Gleda više artikala ili više kategorija → 2
    if n >= 2 or len(categories) >= 2:
        return 2
    return 1


def _blocked_path(request):
    path = getattr(request, 'path', '') or ''
    return path.startswith('/nalog/') or path.startswith('/admin')


def _default_state():
    return {
        'offers_done': 0,
        'offered_ids': [],
        'active': None,
        'first_product_ts': None,
        'site_start_ts': None,
        'last_completed_ts': None,
        'tracking_offer_id': None,
    }


def _get_state(request):
    raw = request.session.get(SESSION_STATE_KEY)
    if not isinstance(raw, dict):
        return _default_state()
    state = _default_state()
    state.update({
        'offers_done': int(raw.get('offers_done') or 0),
        'offered_ids': list(raw.get('offered_ids') or []),
        'active': raw.get('active') if isinstance(raw.get('active'), dict) else None,
        'first_product_ts': raw.get('first_product_ts'),
        'site_start_ts': raw.get('site_start_ts'),
        'last_completed_ts': raw.get('last_completed_ts'),
        'tracking_offer_id': raw.get('tracking_offer_id'),
    })
    return state


def _save_state(request, state):
    request.session[SESSION_STATE_KEY] = state
    request.session.modified = True


def _session_offer(request):
    state = _get_state(request)
    active = state.get('active')
    if isinstance(active, dict) and active.get('show'):
        return active
    return None


def _claimed_ids(request):
    raw = request.session.get(SESSION_CLAIMED_IDS_KEY) or []
    ids = set()
    for item in raw:
        try:
            ids.add(int(item))
        except (TypeError, ValueError):
            continue
    return ids


def _mark_claimed(request, product_id):
    claimed = _claimed_ids(request)
    claimed.add(int(product_id))
    request.session[SESSION_CLAIMED_IDS_KEY] = list(claimed)[:40]
    request.session.modified = True


def _cart_product_ids(request):
    try:
        from .cart import Cart

        cart = Cart(request)
        ids = set()
        for item in cart.cart.values():
            try:
                ids.add(int(item.get('product_id') or 0))
            except (TypeError, ValueError):
                continue
        return {i for i in ids if i}
    except Exception:
        return set()


def is_auto_browse_offer(offer):
    if not offer:
        return False
    code = (getattr(offer, 'aktivacioni_kod', None) or '').strip()
    if code == AUTO_BROWSE_CODE or code.startswith(AUTO_BROWSE_CODE):
        return True
    if code == AI_PRODAJA_CODE or code.startswith(f'{AI_PRODAJA_CODE}-'):
        return True
    # Dwell 1 min na artiklu — isto auto za staff boje
    try:
        from .live_visitor_offer import is_auto_dwell_offer

        return is_auto_dwell_offer(offer)
    except Exception:
        return False


def _has_active_staff_offer(request):
    """
    Ručna staff ponuda (poslao staff) ima prioritet — isključuje auto preporuke.
    Auto welcome / dwell / browse NE blokiraju 2-min ponudu.
    """
    session_key = get_cart_session_key(request)
    if not session_key:
        return False
    lookup = Q(session_key=session_key)
    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        lookup |= Q(user=user)
    return (
        LiveVisitorOffer.objects.filter(lookup, show_popup=True, poslao__isnull=False)
        .filter(
            Q(tip=LiveVisitorOffer.Tip.NARUDZBA, kod_aktiviran=False)
            | Q(tip=LiveVisitorOffer.Tip.ARTIKAL, added_to_cart=False)
            | Q(tip=LiveVisitorOffer.Tip.REGISTRACIJA)
        )
        .exclude(aktivacioni_kod=AUTO_BROWSE_CODE)
        .exclude(aktivacioni_kod__startswith=f'{AUTO_BROWSE_CODE}-')
        .exclude(aktivacioni_kod='AUTO-DWELL')
        .exclude(aktivacioni_kod__startswith='AUTO-DWELL-')
        .exclude(aktivacioni_kod='AUTO-REG-WELCOME')
        .exclude(aktivacioni_kod__startswith='AUTO-REG')
        .exists()
    )


def _discounted_price(base_price, percent):
    base_price = Decimal(str(base_price or 0))
    percent = _clamp_percent(percent)
    if percent <= 0:
        return base_price.quantize(Decimal('0.01'))
    snizena = _izracunaj_akcijsku_od_postotka(base_price, percent)
    if snizena is None:
        return base_price.quantize(Decimal('0.01'))
    return snizena


def _percent_display(discount):
    if not discount or discount <= 0:
        return None
    return int(discount) if discount == int(discount) else float(discount)


def _product_available(product):
    if not product or not product.aktivan:
        return False
    if product.varijacije.exists():
        return product.varijacije.filter(na_stanju=True).exists()
    return bool(product.na_stanju)


def _top_category_name(visitor):
    cats = _normalize_category_views(getattr(visitor, 'pregledane_kategorije', None))
    if not cats:
        return None
    top = max(cats, key=lambda c: (c.get('views') or 1, 0))
    return top.get('naziv') or None


def _top_viewed_entry(viewed):
    """
    Artikal koji najviše gleda.
    viewed lista je most-recent-first — pri istom broju views noviji ima prednost.
    """
    if not viewed:
        return None
    best = None
    best_key = None
    for i, p in enumerate(viewed):
        if not p.get('id'):
            continue
        # views DESC, pa noviji (manji index) prvi
        key = (int(p.get('views') or 1), -i)
        if best is None or key > best_key:
            best = p
            best_key = key
    return best


def _focus_category_name(visitor):
    """
    Kategorija interesa: kategorija artikla koji najviše gleda,
    inače najgledanija kategorija.
    """
    top = _top_category_name(visitor)
    viewed = _normalize_product_views(getattr(visitor, 'pregledani_proizvodi', None))
    top_entry = _top_viewed_entry(viewed)
    if top_entry:
        p = (
            Product.objects.filter(pk=top_entry['id'])
            .select_related('kategorija')
            .first()
        )
        if p and p.kategorija_id:
            return p.kategorija.naziv
    return top


def _category_bestseller_ids(focus_cat, *, exclude_ids=None, limit=8):
    """Bestselleri / istaknuti artikli iz fokus kategorije (za popunu #2–#4)."""
    if not focus_cat or limit <= 0:
        return []
    exclude_ids = {int(x) for x in (exclude_ids or set()) if x}
    qs = (
        Product.objects.filter(aktivan=True, kategorija__naziv=focus_cat)
        .filter(Q(na_stanju=True) | Q(varijacije__na_stanju=True))
        .exclude(pk__in=exclude_ids)
        .distinct()
        .order_by(
            '-prioritet_lagera',
            '-prikazi_na_pocetnoj',
            '-akcija_postotak',
            '-kreiran',
        )
        .values_list('pk', flat=True)[:limit]
    )
    return list(qs)


def _candidate_product_ids(visitor, *, exclude_ids=None, pool_size=24):
    """Širi pool: prvo gledano po views, pa bestselleri fokus kategorije, pa brend."""
    exclude_ids = {int(x) for x in (exclude_ids or set()) if x}
    viewed = _normalize_product_views(getattr(visitor, 'pregledani_proizvodi', None))
    # most-recent-first → sort po views DESC, pa noviji prvi
    ordered = []
    ranked_viewed = sorted(
        [(i, p) for i, p in enumerate(viewed) if p.get('id')],
        key=lambda ip: (-(int(ip[1].get('views') or 1)), ip[0]),
    )
    for _i, p in ranked_viewed:
        pid = int(p['id'])
        if pid not in exclude_ids and pid not in ordered:
            ordered.append(pid)

    focus_cat = _focus_category_name(visitor)
    if focus_cat and len(ordered) < pool_size:
        for pid in _category_bestseller_ids(
            focus_cat,
            exclude_ids=exclude_ids | set(ordered),
            limit=pool_size - len(ordered),
        ):
            if pid not in ordered:
                ordered.append(pid)
            if len(ordered) >= pool_size:
                break

    # Ako ima brend među gledanim — slični od istog brenda
    if ordered and len(ordered) < pool_size:
        brand_ids = list(
            Product.objects.filter(pk__in=ordered[:8], brend_id__isnull=False)
            .values_list('brend_id', flat=True)
            .distinct()[:3]
        )
        if brand_ids:
            same_brand = (
                Product.objects.filter(aktivan=True, brend_id__in=brand_ids)
                .filter(Q(na_stanju=True) | Q(varijacije__na_stanju=True))
                .exclude(pk__in=exclude_ids)
                .exclude(pk__in=ordered)
                .distinct()
                .order_by('-prioritet_lagera', '-prikazi_na_pocetnoj', '-kreiran')
                .values_list('pk', flat=True)[:8]
            )
            for pid in same_brand:
                if pid not in ordered:
                    ordered.append(pid)
                if len(ordered) >= pool_size:
                    break
    return ordered[:pool_size]


def _score_reason_for_product(product, views, *, focus_cat, avg_viewed_price, is_recent):
    """
    Psihologija kupovine — bodovi + razlog za staff / auto ponudu.
    """
    score = 0
    reasons = []
    views = max(1, int(views or 1))

    # Povratak = visok intent (consideration → decision)
    if views >= 4:
        score += 100
        reasons.append(f'Vraća se {views}× — skoro odluka')
    elif views >= 3:
        score += 80
        reasons.append(f'Vraća se {views}× — jak interes')
    elif views >= 2:
        score += 55
        reasons.append(f'Otvorio {views}× — razmišlja / poredi')
    elif views == 1:
        score += 18
        reasons.append('Jednom gledao')

    cat_name = product.kategorija.naziv if product.kategorija_id and product.kategorija else ''
    if focus_cat and cat_name == focus_cat:
        score += 28
        reasons.append(f'U fokusu: {focus_cat}')

    if getattr(product, 'na_akciji', False) or getattr(product, 'katalog_na_akciji', False):
        score += 22
        reasons.append('Već na akciji — lakše zatvaranje')

    if getattr(product, 'prikazi_na_pocetnoj', False):
        score += 8

    # Redukovanje lagera — samo boost, ne mijenja relevantnost (već smo u kandidatima)
    try:
        lager_prio = int(getattr(product, 'prioritet_lagera', 0) or 0)
    except (TypeError, ValueError):
        lager_prio = 0
    if lager_prio >= 2:
        score += 40
        reasons.append('Hit redukovanje lagera')
    elif lager_prio == 1:
        score += 18
        reasons.append('Favorizovano (lager)')

    try:
        price = Decimal(str(product.prikazna_cijena or 0))
    except Exception:
        price = Decimal('0')
    if avg_viewed_price and price > 0:
        # Sličan cjenovni rang = manje trenja
        ratio = float(price / avg_viewed_price) if avg_viewed_price else 1
        if 0.7 <= ratio <= 1.35:
            score += 16
            reasons.append('Cijena u njegovom rangu')
        elif ratio < 0.7:
            score += 10
            reasons.append('Povoljnija alternativa (upsell-down)')
        elif ratio > 1.5:
            score += 6
            reasons.append('Premium alternativa')

    if is_recent:
        score += 12
        reasons.append('Nedavno gledao')

    if not reasons:
        reasons.append('Sličan interesu')
    return score, reasons[0]


def get_accepted_offer_product_ids(visitor):
    """Artikli koje je kupac već prihvatio iz ponude (u korpi) — ne predlaži ponovo."""
    if not visitor:
        return set()
    from django.db.models import Q

    lookup = Q()
    if getattr(visitor, 'session_key', None):
        lookup |= Q(session_key=visitor.session_key)
    if getattr(visitor, 'user_id', None):
        lookup |= Q(user_id=visitor.user_id)
    if not lookup:
        return set()
    ids = set(
        LiveVisitorOffer.objects.filter(
            lookup,
            tip=LiveVisitorOffer.Tip.ARTIKAL,
            added_to_cart=True,
            product_id__isnull=False,
        ).values_list('product_id', flat=True)
    )
    return {int(i) for i in ids if i}


def get_offer_outcome_summary(visitor):
    """
    Sažetak ishoda ponuda za staff UI.
    Svaka ponuda zasebno (odbio A + prihvatio B — obje ostaju).
    """
    if not visitor:
        return {'accepted': [], 'rejected': [], 'pending': [], 'all': []}
    from django.db.models import Q

    lookup = Q()
    if getattr(visitor, 'session_key', None):
        lookup |= Q(session_key=visitor.session_key)
    if getattr(visitor, 'user_id', None):
        lookup |= Q(user_id=visitor.user_id)
    if not lookup:
        return {'accepted': [], 'rejected': [], 'pending': [], 'all': []}

    offers = (
        LiveVisitorOffer.objects.filter(
            lookup,
            tip=LiveVisitorOffer.Tip.ARTIKAL,
            product_id__isnull=False,
        )
        .select_related('product')
        .order_by('-azurirano')[:40]
    )
    accepted, rejected, pending, all_rows = [], [], [], []
    for offer in offers:
        pid = offer.product_id
        if not pid:
            continue
        name = offer.product.naziv if offer.product_id and offer.product else f'Artikal #{pid}'
        is_auto = is_auto_browse_offer(offer)
        pct = offer.discount_percent or 0
        pct_label = ''
        try:
            if pct and float(pct) > 0:
                pct_i = int(pct) if float(pct) == int(float(pct)) else float(pct)
                pct_label = f' (−{pct_i}%)'
        except Exception:
            pct_label = ''
        row = {
            'offer_id': offer.pk,
            'product_id': pid,
            'naziv': name,
            'naziv_full': f'{name}{pct_label}' if pct_label else name,
            'status': '',
            'status_label': '',
            'is_auto': is_auto,
            'kind_label': 'Auto preporuka' if is_auto else 'Ponuda',
            'discount_percent': str(pct) if pct else '',
        }
        if offer.added_to_cart:
            row['status'] = 'accepted'
            row['status_label'] = 'Prihvatio'
            accepted.append(row)
        elif offer.show_popup:
            row['status'] = 'pending'
            row['status_label'] = 'Čeka'
            pending.append(row)
        else:
            row['status'] = 'rejected'
            row['status_label'] = 'Odbio'
            rejected.append(row)
        all_rows.append(row)
    return {
        'accepted': accepted,
        'rejected': rejected,
        'pending': pending,
        'all': all_rows,
    }


def _serialize_rec_row(product, *, views, almost_hovers, reason, score, source='viewed'):
    try:
        price = product.prikazna_cijena
    except Exception:
        price = product.cijena
    return {
        'id': product.pk,
        'product_id': product.pk,
        'naziv': product.naziv,
        'score': score,
        'reason': reason,
        'views': int(views or 0),
        'almost_add': bool(almost_hovers),
        'almost_hovers': int(almost_hovers or 0),
        'price': str(price),
        'price_label': f'{price} KM',
        'image_url': product.prikazna_slika.url if product.prikazna_slika else '',
        'category': (
            product.kategorija.naziv
            if product.kategorija_id and product.kategorija
            else ''
        ),
        'on_sale': bool(
            getattr(product, 'na_akciji', False)
            or getattr(product, 'katalog_na_akciji', False)
        ),
        'suggested_discount': 10,
        'source': source,  # viewed | category
        'priority': (
            'high' if (views or 0) >= 3
            else ('medium' if (views or 0) >= 2 else 'normal')
        ),
    }


def build_sell_recommendations(visitor, *, exclude_ids=None, limit=MAX_RECOMMENDATIONS):
    """
    Spremne ponude strogo po redu gledanja:
      #1 = artikal koji najviše gleda (npr. BKK LOGO PERFORMANCE HAT)
      #2 = sljedeći po broju ulazaka / hoveru
      …
    Ako nema dovoljno gledanih, dopuni bestsellerima iz fokus kategorije
    (npr. Kačketi) — uvijek poslije gledanih.
    Hover s početne se ne računa (samo u artiklu).
    """
    limit = max(1, int(limit or MAX_RECOMMENDATIONS))
    exclude_ids = {int(x) for x in (exclude_ids or set()) if x}
    try:
        exclude_ids |= get_accepted_offer_product_ids(visitor)
    except Exception:
        pass

    viewed = _normalize_product_views(getattr(visitor, 'pregledani_proizvodi', None))
    # most-recent-first u historiji
    viewed = [p for p in viewed if p.get('id') and int(p['id']) not in exclude_ids]
    views_map = {int(p['id']): max(1, int(p.get('views') or 1)) for p in viewed}
    recency_rank = {int(p['id']): i for i, p in enumerate(viewed)}

    almost_map = {}
    try:
        from .almost_cart import get_almost_cart_products

        for item in get_almost_cart_products(visitor):
            pid = item.get('id')
            if not pid:
                continue
            pid = int(pid)
            if pid in views_map and pid not in exclude_ids:
                almost_map[pid] = item
    except Exception:
        almost_map = {}

    focus_cat = _focus_category_name(visitor)
    candidate_ids = [int(p['id']) for p in viewed]
    products_by_id = {}
    if candidate_ids:
        for p in (
            Product.objects.filter(pk__in=candidate_ids, aktivan=True)
            .select_related('kategorija', 'brend')
            .prefetch_related('varijacije')
        ):
            if _product_available(p):
                products_by_id[p.pk] = p

    prices = []
    for p in products_by_id.values():
        try:
            prices.append(Decimal(str(p.prikazna_cijena or 0)))
        except Exception:
            pass
    avg_price = (sum(prices) / len(prices)) if prices else Decimal('0')

    scored = []
    for pid, product in products_by_id.items():
        views = int(views_map.get(pid, 0) or 0)
        if views < 1:
            continue
        is_recent = recency_rank.get(pid, 99) < 3
        score, _ = _score_reason_for_product(
            product,
            views,
            focus_cat=focus_cat,
            avg_viewed_price=avg_price,
            is_recent=is_recent,
        )
        almost = almost_map.get(pid)
        almost_hovers = 0
        if almost:
            almost_hovers = max(1, int(almost.get('hovers') or 1))
            score += 20 + min(15, almost_hovers * 4)

        if views >= 2:
            reason = f'Najviše gleda · vratio se {views}×'
            if almost_hovers:
                reason += ' · hover na Dodaj'
        elif almost_hovers:
            reason = 'Ušao u artikal · hover na Dodaj u korpu'
        else:
            reason = 'Ušao u artikal'

        scored.append(
            _serialize_rec_row(
                product,
                views=views,
                almost_hovers=almost_hovers,
                reason=reason,
                score=score,
                source='viewed',
            )
        )

    # Redoslijed: views DESC → almost hover → noviji gledan → score
    scored.sort(
        key=lambda x: (
            -(x.get('views') or 0),
            -(x.get('almost_hovers') or 0),
            recency_rank.get(x['product_id'], 99),
            -(x.get('score') or 0),
            x['product_id'],
        ),
    )

    # Popuna bestsellerima iz fokus kategorije (poslije gledanih)
    used_ids = {r['product_id'] for r in scored} | exclude_ids
    if focus_cat and len(scored) < limit:
        fill_ids = _category_bestseller_ids(
            focus_cat,
            exclude_ids=used_ids,
            limit=limit - len(scored) + 2,
        )
        if fill_ids:
            fill_products = {
                p.pk: p
                for p in (
                    Product.objects.filter(pk__in=fill_ids, aktivan=True)
                    .select_related('kategorija', 'brend')
                    .prefetch_related('varijacije')
                )
                if _product_available(p)
            }
            for pid in fill_ids:
                if len(scored) >= limit:
                    break
                product = fill_products.get(pid)
                if not product or product.pk in used_ids:
                    continue
                used_ids.add(product.pk)
                scored.append(
                    _serialize_rec_row(
                        product,
                        views=0,
                        almost_hovers=0,
                        reason=f'Bestseller iz „{focus_cat}”',
                        score=12,
                        source='category',
                    )
                )

    result = scored[:limit]
    for i, row in enumerate(result):
        row['rank'] = i + 1
        if i == 0 and row.get('source') == 'viewed':
            v = int(row.get('views') or 0)
            h = int(row.get('almost_hovers') or 0)
            parts = ['#1 najviše gleda']
            if v > 1:
                parts[0] = f'#1 najviše gleda ({v}×)'
            if h:
                parts.append('hover na Dodaj')
            row['reason'] = ' · '.join(parts)
        elif row.get('source') == 'viewed' and (row.get('views') or 0) >= 2:
            row['reason'] = f'#{i + 1} po gledanju ({row["views"]}×)'
        elif row.get('source') == 'viewed':
            row['reason'] = f'#{i + 1} po gledanju'
        elif row.get('source') == 'category':
            row['reason'] = (
                f'#{i + 1} bestseller'
                + (f' iz „{row.get("category") or focus_cat or ""}”' if (row.get('category') or focus_cat) else '')
            )
    return result


def pick_recommendation_product_ids(visitor, *, exclude_ids=None, limit=MAX_RECOMMENDATIONS):
    """Do 4 artikla — redom po gledanju (#1 = najviše gleda)."""
    recs = build_sell_recommendations(visitor, exclude_ids=exclude_ids, limit=limit)
    return [r['product_id'] for r in recs if r.get('product_id')]


def build_visitor_insight(visitor):
    """Sažetak ponašanja kupca za staff panel."""
    viewed = _normalize_product_views(getattr(visitor, 'pregledani_proizvodi', None))
    cats = _normalize_category_views(getattr(visitor, 'pregledane_kategorije', None))
    focus = _focus_category_name(visitor)
    revisited = [p for p in viewed if (p.get('views') or 1) >= 2]
    top_product = _top_viewed_entry(viewed)

    almost = []
    try:
        from .almost_cart import get_almost_cart_products

        almost = get_almost_cart_products(visitor)
    except Exception:
        almost = []

    top_name = (top_product.get('naziv') or 'artikal') if top_product else ''
    top_views = int(top_product.get('views') or 1) if top_product else 0

    if almost:
        top_a = almost[0]
        intent = 'hot'
        intent_label = 'Skoro dodao u korpu — nije kliknuo'
        tip = (
            f'#1 pošalji „{top_a.get("naziv") or "artikal"}” — '
            f'kursor bio na Dodaj u korpu. 10% odmah.'
        )
    elif top_product and top_views >= 3:
        intent = 'hot'
        intent_label = 'Vruć trag — vraća se na artikal'
        tip = (
            f'#1 pošalji „{top_name}” ({top_views}× gleda) s 10–15%. '
            f'Ostale ponude redom po gledanju.'
        )
    elif revisited and top_product:
        intent = 'warm'
        intent_label = 'Toplo — poredi / razmišlja'
        tip = (
            f'#1 pošalji „{top_name}” ({top_views}×) s 10%, '
            f'pa redom ostale koje gleda'
            + (f' iz „{focus}”.' if focus else '.')
        )
    elif focus and len(viewed) >= 1 and top_product:
        intent = 'explore'
        intent_label = f'Istražuje: {focus}'
        tip = (
            f'#1 pošalji „{top_name}” (najviše gleda), '
            f'pa bestsellere iz „{focus}” redom s 10%.'
        )
    elif focus and not viewed:
        intent = 'explore'
        intent_label = f'Istražuje: {focus}'
        tip = f'Predloži bestsellere iz „{focus}” s 10% — još nije ušao u artikal.'
    elif viewed and top_product:
        intent = 'browse'
        intent_label = 'Gleda artikle'
        tip = (
            f'#1 kad pošalješ: „{top_name}”. '
            f'Ponude idu redom po tome koliko gleda.'
        )
    else:
        intent = 'cold'
        intent_label = 'Još nema jasnog interesa'
        tip = 'Prati kategorije — kad krene po artiklima, pošalji redom po gledanju.'

    almost_top = almost[0] if almost else None
    return {
        'intent': intent,
        'intent_label': intent_label,
        'tip': tip,
        'focus_category': focus or '',
        'products_viewed': len(viewed),
        'categories_viewed': len(cats),
        'return_count': len(revisited),
        'almost_cart_count': len(almost),
        'almost_cart_product_id': almost_top.get('id') if almost_top else None,
        'almost_cart_product_name': (almost_top.get('naziv') or '') if almost_top else '',
        'almost_cart_hovers': (almost_top.get('hovers') or 0) if almost_top else 0,
        'top_product_id': (
            almost_top.get('id') if almost_top
            else (top_product['id'] if top_product else None)
        ),
        'top_product_name': (
            (almost_top.get('naziv') or '') if almost_top
            else ((top_product.get('naziv') or '') if top_product else '')
        ),
        'top_product_views': (
            almost_top.get('hovers') if almost_top
            else ((top_product.get('views') or 0) if top_product else 0)
        ),
    }


def _viewed_product_count(visitor):
    return len(_normalize_product_views(getattr(visitor, 'pregledani_proizvodi', None)))


def _has_browse_signal(visitor):
    return _viewed_product_count(visitor) >= 1


def _site_seconds(request, visitor, state):
    now_ts = timezone.now().timestamp()
    start_ts = state.get('site_start_ts')
    if start_ts:
        try:
            return max(0, now_ts - float(start_ts))
        except (TypeError, ValueError):
            pass
    if visitor and visitor.first_seen:
        return max(0, (timezone.now() - visitor.first_seen).total_seconds())
    return 0


def compute_purchase_intent_score(visitor, request=None):
    """Skor 0–100 — delegira AI conversion engine."""
    try:
        from .ai_conversion import score_visitor
        return int(score_visitor(visitor, request).get('score') or 0)
    except Exception:
        return 0


def _minutes_required_for_wave(wave, *, intent_score=0):
    """
    Wave 1: ~2 min, ili ~45 s pri high-intent.
    Wave 2: tek nakon ~5 min na sajtu + high intent + min gap 3 min od 1. ponude.
    """
    try:
        from .ai_conversion import auto_offer_delay_seconds, INTENT_WARM as _WARM
        warm = _WARM
        delay_fn = auto_offer_delay_seconds
    except Exception:
        warm = HIGH_INTENT_SCORE

        def delay_fn(s):
            return HIGH_INTENT_OFFER_SECONDS if s >= HIGH_INTENT_SCORE else (FIRST_OFFER_AFTER_MINUTES * 60)

    wave = max(1, int(wave or 1))
    if wave == 1:
        return delay_fn(intent_score) / 60.0
    if wave == 2:
        if intent_score >= warm:
            return float(SECOND_OFFER_AFTER_MINUTES)
        return 10**9
    return 10**9


def _should_trigger_wave(request, visitor, state, wave):
    offers_done = int(state.get('offers_done') or 0)
    if offers_done != wave - 1:
        return False
    if offers_done >= MAX_OFFERS_PER_SESSION:
        return False
    if not _has_browse_signal(visitor):
        return False
    intent = compute_purchase_intent_score(visitor, request)
    needed = _minutes_required_for_wave(wave, intent_score=intent) * 60
    if _site_seconds(request, visitor, state) < needed:
        return False
    # Razmak između 1. i 2. ponude (nakon zatvaranja / prihvatanja prve)
    if wave >= 2:
        last = state.get('last_completed_ts')
        if last:
            try:
                gap = timezone.now().timestamp() - float(last)
            except (TypeError, ValueError):
                gap = 0
            if gap < MIN_GAP_BETWEEN_OFFERS_SECONDS:
                return False
        # Druga ponuda samo ako još uvijek ima jasan signal
        if intent < HIGH_INTENT_SCORE:
            return False
    return True


def _create_tracking_offer(request, visitor, product_ids, percent, wave):
    """
    LiveVisitorOffer za staff: zeleni/crveni krug.
    Ne prikazuje se kupcu kao staff popup (AUTO-BROWSE filter).
    """
    session_key = get_cart_session_key(request)
    if not session_key or not product_ids:
        return None

    primary_id = int(product_ids[0])
    product = Product.objects.filter(pk=primary_id).first()
    user = None
    if getattr(request, 'user', None) and request.user.is_authenticated:
        user = request.user
    elif visitor and visitor.user_id:
        user = visitor.user

    # Zatvori stare auto AI tracking redove (samo jedna aktivna po sesiji)
    LiveVisitorOffer.objects.filter(
        session_key=session_key,
        show_popup=True,
        aktivacioni_kod__in=[AUTO_BROWSE_CODE, AI_PRODAJA_CODE],
    ).update(show_popup=False)

    offer = LiveVisitorOffer.objects.create(
        session_key=session_key,
        user=user,
        tip=LiveVisitorOffer.Tip.ARTIKAL,
        product=product,
        discount_percent=_clamp_percent(percent or Decimal('0')),
        aktivacioni_kod=AI_PRODAJA_CODE,  # AI prodaja (tracking)
        show_popup=True,
        added_to_cart=False,
        kod_aktiviran=False,
        poslao=None,
    )
    return offer


def _mark_tracking_accepted(request, state, product_id=None):
    offer_id = state.get('tracking_offer_id')
    if not offer_id:
        return
    try:
        offer = LiveVisitorOffer.objects.filter(pk=int(offer_id)).first()
    except (TypeError, ValueError):
        return
    if not offer or not is_auto_browse_offer(offer):
        return
    if product_id:
        product = Product.objects.filter(pk=product_id).first()
        if product:
            offer.product = product
    offer.added_to_cart = True
    offer.show_popup = False
    offer.save(update_fields=['product', 'added_to_cart', 'show_popup', 'azurirano'])


def _mark_tracking_rejected(request, state):
    offer_id = state.get('tracking_offer_id')
    if not offer_id:
        return
    try:
        offer = LiveVisitorOffer.objects.filter(pk=int(offer_id)).first()
    except (TypeError, ValueError):
        return
    if not offer or not is_auto_browse_offer(offer):
        return
    if offer.added_to_cart:
        return
    offer.show_popup = False
    offer.save(update_fields=['show_popup', 'azurirano'])


def _complete_active_offer(request, state, *, product_id=None, outcome='dismiss'):
    """
    outcome: 'accept' | 'dismiss' | 'expire'
    accept → zeleni, dismiss/expire → crveni
    """
    active = state.get('active')
    offered = list(state.get('offered_ids') or [])
    if product_id:
        try:
            pid = int(product_id)
            if pid and pid not in offered:
                offered.append(pid)
        except (TypeError, ValueError):
            pass
    if active:
        for pid in (active.get('product_ids') or []):
            try:
                pid = int(pid)
            except (TypeError, ValueError):
                continue
            if pid and pid not in offered:
                offered.append(pid)

    if outcome == 'accept':
        _mark_tracking_accepted(request, state, product_id=product_id)
    else:
        _mark_tracking_rejected(request, state)

    state['offered_ids'] = offered[:60]
    state['active'] = None
    state['tracking_offer_id'] = None
    state['offers_done'] = min(
        MAX_OFFERS_PER_SESSION,
        int(state.get('offers_done') or 0) + 1,
    )
    state['last_completed_ts'] = timezone.now().timestamp()
    _save_state(request, state)
    return state


def _create_active_offer(request, visitor, state, percent, wave):
    exclude = (
        _cart_product_ids(request)
        | _claimed_ids(request)
        | {int(x) for x in (state.get('offered_ids') or []) if x}
    )
    limit = _rec_limit(request, visitor=visitor)
    product_ids = pick_recommendation_product_ids(
        visitor,
        exclude_ids=exclude,
        limit=limit,
    )
    if not product_ids:
        return None

    product_ids = product_ids[:limit]
    percent = _clamp_percent(percent)
    tracking = _create_tracking_offer(request, visitor, product_ids, percent, wave)
    now = timezone.now()
    active = {
        'show': True,
        'wave': wave,
        'product_ids': product_ids,
        'discount_percent': str(percent),
        'top_category': _focus_category_name(visitor) or _top_category_name(visitor) or '',
        'created_ts': now.timestamp(),
        'expires_ts': (now + timedelta(minutes=OFFER_TTL_MINUTES)).timestamp(),
        'version': int(now.timestamp()),
        'reason': _build_reason(visitor, product_ids),
        'tracking_offer_id': tracking.pk if tracking else None,
        'mobile_limit': True,  # max 2 artikla
        'ai_prodaja': True,
        'product_count': len(product_ids),
    }
    state['active'] = active
    state['tracking_offer_id'] = tracking.pk if tracking else None
    _save_state(request, state)
    return active


def maybe_create_browse_interest_offer(request, visitor=None):
    """
    AI prodaja — glavni auto-popup:
    - prati gledanje / skoro-korpu / korpu
    - max 2 ponude, min 3 min razmaka
    - 1–2 artikla, popust ≤ 10%
    """
    if not request or _blocked_path(request):
        return None

    user = getattr(request, 'user', None)
    if user and getattr(user, 'is_authenticated', False) and (
        user.is_staff or user.is_superuser
    ):
        return None

    aktivan, percent = _settings()
    if not aktivan or percent <= 0:
        return None

    if _has_active_staff_offer(request):
        return None

    session_key = get_cart_session_key(request)
    if not session_key:
        return None

    if visitor is None:
        visitor = LiveVisitor.objects.filter(session_key=session_key).first()
    if not visitor:
        return None

    if _LEGACY_DISMISSED_KEY in request.session:
        del request.session[_LEGACY_DISMISSED_KEY]
        request.session.modified = True
    if _LEGACY_OFFER_KEY in request.session:
        del request.session[_LEGACY_OFFER_KEY]
        request.session.modified = True

    state = _get_state(request)
    now_ts = timezone.now().timestamp()

    if not state.get('site_start_ts'):
        if visitor.first_seen:
            state['site_start_ts'] = visitor.first_seen.timestamp()
        else:
            state['site_start_ts'] = now_ts

    viewed_count = _viewed_product_count(visitor)
    if viewed_count >= 1 and not state.get('first_product_ts'):
        state['first_product_ts'] = now_ts

    # % iz postavki, hard cap 10%
    intent = compute_purchase_intent_score(visitor, request)
    try:
        from .ai_conversion import auto_offer_discount
        offer_percent = _clamp_percent(auto_offer_discount(percent, intent))
    except Exception:
        offer_percent = _clamp_percent(percent)

    active = state.get('active')
    if active and active.get('show'):
        expires = active.get('expires_ts') or 0
        try:
            expired = expires and now_ts > float(expires)
        except (TypeError, ValueError):
            expired = False
        if expired:
            state = _complete_active_offer(request, state, outcome='expire')
            active = None
        else:
            _save_state(request, state)
            return active

    offers_done = int(state.get('offers_done') or 0)
    if offers_done >= MAX_OFFERS_PER_SESSION:
        _save_state(request, state)
        return None

    if not _has_browse_signal(visitor):
        _save_state(request, state)
        return None

    next_wave = offers_done + 1
    if _should_trigger_wave(request, visitor, state, next_wave):
        return _create_active_offer(
            request, visitor, state, offer_percent, wave=next_wave,
        )

    _save_state(request, state)
    return None


def _build_reason(visitor, product_ids):
    viewed = {
        p['id']: p
        for p in _normalize_product_views(getattr(visitor, 'pregledani_proizvodi', None))
    }
    primary = viewed.get(product_ids[0]) if product_ids else None
    if primary and (primary.get('views') or 1) >= MIN_PRODUCT_VIEWS_PRIORITY:
        return 'revisit'
    if primary:
        return 'browse'
    if _focus_category_name(visitor):
        return 'category'
    return 'browse'


def _build_product_card(product, percent):
    in_stock_variations = list(
        product.varijacije.filter(na_stanju=True).order_by('redoslijed', 'id'),
    )
    variations = []
    for variation in in_stock_variations:
        base_price = variation.prikazna_cijena
        final_price = _discounted_price(base_price, percent)
        variations.append({
            'id': variation.pk,
            'naziv': variation.naziv,
            'base_price': str(base_price),
            'final_price': str(final_price),
            'has_discount': percent > 0 and final_price < base_price,
        })

    if variations:
        display_base = variations[0]['base_price']
        display_final = variations[0]['final_price']
        has_discount = variations[0]['has_discount']
        can_add_directly = len(variations) == 1
        variation_id = variations[0]['id'] if can_add_directly else ''
    else:
        display_base = str(product.prikazna_cijena)
        display_final = str(_discounted_price(product.prikazna_cijena, percent))
        has_discount = percent > 0 and Decimal(display_final) < Decimal(display_base)
        can_add_directly = True
        variation_id = ''

    return {
        'product_id': product.pk,
        'product_name': product.naziv,
        'product_url': product.get_absolute_url(),
        'image_url': product.prikazna_slika.url if product.prikazna_slika else '',
        'has_variations': len(variations) > 1,
        'variations': variations,
        'display_base_price': display_base,
        'display_final_price': display_final,
        'has_discount': has_discount,
        'can_add_directly': can_add_directly and (
            bool(variations) or product.na_stanju
        ),
        'variation_id': variation_id,
    }


def _timer_seconds(offer_data):
    expires = offer_data.get('expires_ts')
    if not expires:
        return OFFER_TIMER_MINUTES * 60
    remaining = int(float(expires) - timezone.now().timestamp())
    return max(0, remaining)


def build_browse_interest_payload(request):
    """JSON payload AI prodaje — 1 ili 2 artikla."""
    aktivan, percent = _settings()
    if not aktivan:
        return None
    if _has_active_staff_offer(request):
        return None

    session_key = get_cart_session_key(request)
    visitor = (
        LiveVisitor.objects.filter(session_key=session_key).first()
        if session_key else None
    )
    limit = _rec_limit(request, visitor=visitor)

    data = _session_offer(request)
    if not data or not data.get('show'):
        data = maybe_create_browse_interest_offer(request, visitor)
        if not data or not data.get('show'):
            return None

    if data.get('expires_ts') and timezone.now().timestamp() > float(data['expires_ts']):
        state = _get_state(request)
        _complete_active_offer(request, state, outcome='expire')
        return None

    product_ids = [int(p) for p in (data.get('product_ids') or []) if p]
    if not product_ids:
        return None

    # Prefer limit sačuvan u active, max 2
    try:
        stored = int(data.get('product_count') or 0)
        if 1 <= stored <= 2:
            limit = stored
    except (TypeError, ValueError):
        pass
    limit = max(1, min(2, limit))

    exclude = _cart_product_ids(request) | _claimed_ids(request)
    product_ids = [pid for pid in product_ids if pid not in exclude]
    if not product_ids:
        state = _get_state(request)
        _complete_active_offer(request, state, outcome='dismiss')
        return None

    products = list(
        Product.objects.filter(pk__in=product_ids, aktivan=True)
        .select_related('kategorija', 'brend')
        .prefetch_related('varijacije')
    )
    by_id = {p.pk: p for p in products}
    cards = []
    for pid in product_ids:
        product = by_id.get(pid)
        if product and _product_available(product):
            cards.append(_build_product_card(product, percent))
        if len(cards) >= limit:
            break

    if not cards:
        return None

    state = _get_state(request)
    active = state.get('active') or data
    active['product_ids'] = [c['product_id'] for c in cards][:limit]
    active['discount_percent'] = str(_clamp_percent(percent))
    active['show'] = True
    active['mobile_limit'] = True
    active['ai_prodaja'] = True
    state['active'] = active
    if active.get('tracking_offer_id'):
        state['tracking_offer_id'] = active['tracking_offer_id']
    _save_state(request, state)

    pct = _percent_display(_clamp_percent(percent))
    top_category = (active.get('top_category') or '').strip()
    wave = int(active.get('wave') or 1)

    kicker = 'AI ponuda za tebe'
    title = 'Posebna cijena baš za tebe'
    if len(cards) > 1:
        if pct:
            message = f'Samo sada — {pct}% popusta. Izaberite artikal:'
        else:
            message = 'Samo sada — izaberite artikal:'
    else:
        if pct:
            message = f'Samo sada — {pct}% popusta na ovaj artikal.'
        else:
            message = 'Samo sada — specijalna cijena na ovaj artikal.'

    timer = _timer_seconds(active)
    if timer <= 0:
        _complete_active_offer(request, state, outcome='expire')
        return None

    layout = 'grid-1x2' if len(cards) <= 2 else 'grid-2x2'
    return {
        'offer_type': 'browse_interest',
        'offer_id': f'browse-{active.get("version") or int(timezone.now().timestamp())}',
        'offer_version': int(active.get('version') or timezone.now().timestamp()),
        'discount_percent': pct,
        'title': title,
        'message': message,
        'kicker': kicker,
        'top_category': top_category,
        'wave': wave,
        'products': cards,
        'layout': layout,
        'mobile': limit <= MAX_RECOMMENDATIONS_MOBILE,
        'timer_seconds': timer,
        'timer_minutes': OFFER_TIMER_MINUTES,
        'add_url': '/preporuka/dodaj/',
        'dismiss_url': '/preporuka/zatvori/',
    }


def poll_browse_interest_offer(request):
    return build_browse_interest_payload(request)


def apply_browse_interest_offer(request, cart):
    """Dodaj izabrani artikal u korpu s popustom (zeleni status)."""
    data = _session_offer(request)
    if not data or not data.get('show'):
        return False, 'Ponuda više nije dostupna.'

    aktivan, percent = _settings()
    if not aktivan or percent <= 0:
        return False, 'Ponuda više nije dostupna.'

    try:
        product_id = int(request.POST.get('product_id') or 0)
    except (TypeError, ValueError):
        product_id = 0
    allowed = {int(x) for x in (data.get('product_ids') or [])}
    if not product_id or product_id not in allowed:
        return False, 'Artikal nije dio ponude.'

    product = Product.objects.filter(pk=product_id, aktivan=True).first()
    if not product:
        return False, 'Artikal više nije dostupan.'

    in_stock_variations = product.varijacije.filter(na_stanju=True)
    variation = None
    var_id = (request.POST.get('variation_id') or '').strip()
    if var_id:
        try:
            variation = in_stock_variations.get(pk=int(var_id))
        except (ProductVariation.DoesNotExist, ValueError):
            return False, 'Izaberite ispravnu varijaciju.'
    elif in_stock_variations.exists():
        if in_stock_variations.count() == 1:
            variation = in_stock_variations.first()
        else:
            return False, 'Izaberite varijaciju.'

    if variation and not variation.na_stanju:
        return False, 'Varijacija nije na stanju.'
    if not variation and not product.na_stanju:
        return False, 'Artikal nije na stanju.'

    base_price = variation.prikazna_cijena if variation else product.prikazna_cijena
    final_price = _discounted_price(base_price, percent)
    cart.add(
        product,
        variation=variation,
        quantity=1,
        custom_price=final_price,
        promo_bazna=base_price,
        discount_source=f'AI prodaja / browse ponuda (−{percent}%)',
        discount_percent=percent,
    )

    _mark_claimed(request, product_id)
    state = _get_state(request)
    _complete_active_offer(request, state, product_id=product_id, outcome='accept')

    # Staff obavijest — prihvaćena personalizovana / AI ponuda
    try:
        from .staff_alerts import notify_offer_accepted
        from .models import LiveVisitor
        from .cart_tracking import get_cart_session_key

        sk = get_cart_session_key(request) or ''
        lv = LiveVisitor.objects.filter(session_key=sk).only(
            'ime', 'email', 'grad',
        ).first() if sk else None
        notify_offer_accepted(
            ime=(lv.ime if lv else '') or '',
            email=(lv.email if lv else '') or '',
            grad=(lv.grad if lv else '') or '',
            session_key=sk,
            product_name=product.naziv or '',
            discount_percent=percent,
            source='AI prodaja',
        )
    except Exception:
        pass

    label = f'{product.naziv}' + (f' — {variation.naziv}' if variation else '')
    pct = _percent_display(percent)
    return True, f'"{label}" je dodato u korpu s popustom od {pct}%.'


def dismiss_browse_interest_offer(request):
    """Zatvori ponudu — crveni status u staff analitici."""
    state = _get_state(request)
    if state.get('active'):
        _complete_active_offer(request, state, outcome='dismiss')
    else:
        if int(state.get('offers_done') or 0) == 0 and state.get('first_product_ts'):
            state['offers_done'] = 1
            _save_state(request, state)
    request.session.modified = True
