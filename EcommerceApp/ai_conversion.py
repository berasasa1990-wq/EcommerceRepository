"""
AI prodaja — prati kupca i automatski šalje % na artikal koji će najvjerovatnije kupiti.

Max popust: 10%. Staff se obavještava kad kupac prihvati.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.utils import timezone

from .live_visitors import _normalize_category_views, _normalize_product_views

# Pragovi namjere
INTENT_HOT = 75
INTENT_WARM = 55
INTENT_MILD = 35

# Auto-ponuda: brže kad je intent visok
HIGH_INTENT_SECONDS = 40
NORMAL_INTENT_SECONDS = 120
FOLLOWUP_SECONDS = 240

# AI prodaja hard cap
AI_MAX_DISCOUNT = Decimal('10')
AI_OFFER_CODE = 'AI-PRODAJA'
AI_SESSION_SENT_KEY = 'ai_prodaja_sent_ids'
AI_MAX_AUTO_OFFERS = 2


def _d(val, default=Decimal('0')):
    try:
        return Decimal(str(val if val is not None else default))
    except (InvalidOperation, TypeError, ValueError):
        return default


def score_visitor(visitor, request=None):
    """
    Skor 0–100 + nivo + razlozi.
    """
    if not visitor:
        return {
            'score': 0,
            'level': 'cold',
            'level_label': 'Hladan',
            'reasons': [],
            'suggested_discount': 10,
            'action': 'Prati gledanje — još nema signala',
            'action_code': 'watch',
        }

    score = 0
    reasons = []
    products = _normalize_product_views(getattr(visitor, 'pregledani_proizvodi', None))
    categories = _normalize_category_views(getattr(visitor, 'pregledane_kategorije', None))

    n_prod = len(products)
    if n_prod:
        pts = min(25, n_prod * 6)
        score += pts
        if n_prod >= 3:
            reasons.append(f'Gledao {n_prod} artikala')
        elif n_prod >= 1:
            reasons.append(f'Otvorio {n_prod} artikal' + ('a' if n_prod > 1 else ''))

    multi = [p for p in products if int(p.get('views') or 1) >= 2]
    if multi:
        score += min(25, len(multi) * 10)
        top = max(multi, key=lambda p: int(p.get('views') or 1))
        reasons.append(
            f"Povratak na „{(top.get('naziv') or 'artikal')[:40]}” "
            f"({int(top.get('views') or 1)}×)"
        )

    max_views = max((int(p.get('views') or 1) for p in products), default=0)
    if max_views >= 3:
        score += 12
    elif max_views >= 2:
        score += 6

    if categories:
        score += min(10, len(categories) * 3)
        top_c = max(categories, key=lambda c: int(c.get('views') or 1))
        if int(top_c.get('views') or 1) >= 2:
            reasons.append(f"Fokus kategorija: {top_c.get('naziv')}")

    # Skoro korpa
    almost = []
    try:
        from .almost_cart import get_almost_cart_products
        almost = get_almost_cart_products(visitor) or []
        if almost:
            score += 18
            reasons.append(
                f"Skoro korpa: {(almost[0].get('naziv') or '')[:36]} "
                f"({int(almost[0].get('hovers') or 0)}× hover)"
            )
            if int(almost[0].get('hovers') or 0) >= 2:
                score += 8
    except Exception:
        almost = []

    # Korpa
    cart_value = Decimal('0')
    cart_count = 0
    try:
        from .models import ActiveCartItem
        sk = getattr(visitor, 'session_key', None) or ''
        if sk:
            rows = list(
                ActiveCartItem.objects.filter(session_key=sk).only(
                    'kolicina', 'cijena',
                )[:40]
            )
            cart_count = len(rows)
            for r in rows:
                try:
                    cart_value += _d(r.cijena) * max(1, int(r.kolicina or 1))
                except Exception:
                    pass
            if cart_count:
                score += 12
                reasons.append(f'Korpa: {cart_count} stavke ({cart_value:.0f} KM)')
    except Exception:
        pass

    # Sada na artiklu
    path = (getattr(visitor, 'trenutna_putanja', None) or '')
    if '/artikal/' in path:
        score += 8
        if not any('Povratak' in r or 'artikal' in r.lower() for r in reasons):
            page = (getattr(visitor, 'trenutno_gleda', None) or 'Artikal')[:50]
            reasons.append(f'Sada gleda: {page}')
    elif '/korpa' in path or '/narudzba' in path or '/checkout' in path:
        score += 15
        reasons.append('U procesu kupovine (korpa/checkout)')

    # Vraćeni posjetilac
    visits = int(getattr(visitor, 'site_visit_count', 1) or 1)
    if visits >= 2:
        score += 8
        reasons.append(f'Vraćeni posjetilac ({visits}× na sajtu)')

    score = max(0, min(100, int(score)))

    if score >= INTENT_HOT:
        level, level_label = 'hot', 'Vruć'
        action = 'AI prodaja šalje ≤10% na top artikal'
        action_code = 'send_now'
        suggested = 10
    elif score >= INTENT_WARM:
        level, level_label = 'warm', 'Topao'
        action = 'AI prodaja spremna — do 10% na najgledaniji'
        action_code = 'send_offer'
        suggested = 10
    elif score >= INTENT_MILD:
        level, level_label = 'mild', 'Blag'
        action = 'AI prati — ponuda uskoro (≤10%)'
        action_code = 'nudge_soon'
        suggested = 8
    else:
        level, level_label = 'cold', 'Hladan'
        action = 'AI prati gledanje'
        action_code = 'wait'
        suggested = 8

    # Ako je skoro korpa — forsiraj hitniju akciju
    if almost and score >= INTENT_MILD:
        action = f'AI: −{suggested}% na skoro-korpu „{(almost[0].get("naziv") or "artikal")[:32]}”'
        action_code = 'close_almost_cart'

    if cart_count and score >= INTENT_WARM and '/korpa' not in path:
        action = 'AI: kupac ima korpu — blagi nudge'
        action_code = 'cart_nudge'

    return {
        'score': score,
        'level': level,
        'level_label': level_label,
        'reasons': reasons[:6],
        'suggested_discount': suggested,
        'action': action,
        'action_code': action_code,
        'products': products,
        'almost_cart': almost[:3],
        'cart_count': cart_count,
        'cart_value': str(cart_value.quantize(Decimal('0.01'))),
        'top_product_id': (
            int(almost[0]['id']) if almost and almost[0].get('id')
            else (int(products[0]['id']) if products and products[0].get('id') else None)
        ),
        'top_product_name': (
            (almost[0].get('naziv') if almost else None)
            or (products[0].get('naziv') if products else '')
            or ''
        ),
    }


def auto_offer_delay_seconds(intent_score):
    """Koliko sekundi čekati prije automatske personalizovane ponude."""
    if intent_score >= INTENT_HOT:
        return 35
    if intent_score >= INTENT_WARM:
        return HIGH_INTENT_SECONDS
    return NORMAL_INTENT_SECONDS


def auto_offer_discount(base_percent, intent_score):
    """Dinamički % prema intentu — nikad preko 10%."""
    base = min(AI_MAX_DISCOUNT, _d(base_percent, Decimal('10')))
    if intent_score >= INTENT_HOT:
        return min(AI_MAX_DISCOUNT, max(base, Decimal('10')))
    if intent_score >= INTENT_WARM:
        return min(AI_MAX_DISCOUNT, max(base, Decimal('8')))
    return min(AI_MAX_DISCOUNT, max(Decimal('5'), base))


def staff_ai_payload(visitor, request=None):
    """Payload za uživo analitiku / drawer."""
    data = score_visitor(visitor, request)
    return {
        'ai_score': data['score'],
        'ai_level': data['level'],
        'ai_level_label': data['level_label'],
        'ai_reasons': data['reasons'],
        'ai_action': data['action'],
        'ai_action_code': data['action_code'],
        'ai_suggested_discount': data['suggested_discount'],
        'ai_top_product_id': data.get('top_product_id'),
        'ai_top_product_name': data.get('top_product_name') or '',
        'ai_badge': f"{data['level_label']} {data['score']}",
    }


def product_conversion_boost(product, request=None):
    """
    Trust + free shipping hint na product page.
    Hint prati STVARNU korpu (međuzbir) — koliko još fali do besplatne dostave.
    """
    boost = {
        'trust_lines': [
            'Brza dostava širom BiH',
            'Sigurno plaćanje',
            'Povrat robe po politici trgovine',
        ],
        'free_shipping_hint': '',
        'cta_nudge': 'Dodaj u korpu — zalihe se brzo prazne',
        'cart_subtotal': None,
        'free_shipping_left': None,
        'free_shipping_threshold': None,
    }
    try:
        from .models import SiteSettings
        s = SiteSettings.load()
        threshold = getattr(s, 'besplatna_dostava_od', None)
        if threshold is None or _d(threshold) <= 0:
            return boost

        t = _d(threshold)
        boost['free_shipping_threshold'] = str(t.quantize(Decimal('0.01')))

        # Trenutni iznos u korpi (međuzbir bez dostave)
        cart_total = Decimal('0.00')
        if request is not None:
            try:
                from .cart import Cart
                cart = Cart(request)
                cart_total = _d(cart.ukupno)
            except Exception:
                cart_total = Decimal('0.00')
        boost['cart_subtotal'] = str(cart_total.quantize(Decimal('0.01')))

        left = (t - cart_total).quantize(Decimal('0.01'))
        if left <= 0:
            boost['free_shipping_left'] = '0.00'
            boost['free_shipping_hint'] = (
                f'Dostava je BESPLATNA — u korpi već imaš {cart_total:.2f} KM '
                f'(prag {t:.0f} KM).'
            )
        else:
            boost['free_shipping_left'] = str(left)
            if cart_total > 0:
                boost['free_shipping_hint'] = (
                    f'U korpi: {cart_total:.2f} KM — dodaj još '
                    f'<strong>{left:.2f} KM</strong> za BESPLATNU dostavu '
                    f'(prag {t:.0f} KM).'
                )
            else:
                boost['free_shipping_hint'] = (
                    f'Dodaj još <strong>{left:.2f} KM</strong> u korpu '
                    f'za BESPLATNU dostavu (prag {t:.0f} KM).'
                )
    except Exception:
        pass
    return boost


def _session_sent_ids(request):
    raw = request.session.get(AI_SESSION_SENT_KEY) if request else None
    ids = []
    if isinstance(raw, list):
        for x in raw:
            try:
                ids.append(int(x))
            except (TypeError, ValueError):
                continue
    return ids


def _mark_sent(request, product_id):
    if not request:
        return
    ids = _session_sent_ids(request)
    pid = int(product_id)
    if pid not in ids:
        ids.append(pid)
    request.session[AI_SESSION_SENT_KEY] = ids[:20]
    request.session.modified = True


def _pick_target_product(visitor, exclude_ids=None):
    """Artikal s najvećom šansom — skoro-korpa > najviše pregleda."""
    exclude = {int(x) for x in (exclude_ids or []) if x}
    try:
        from .almost_cart import get_almost_cart_products
        almost = get_almost_cart_products(visitor) or []
        for a in almost:
            try:
                pid = int(a.get('id') or 0)
            except (TypeError, ValueError):
                continue
            if pid and pid not in exclude:
                return pid, (a.get('naziv') or '')[:120]
    except Exception:
        pass
    products = _normalize_product_views(getattr(visitor, 'pregledani_proizvodi', None))
    # Sort by views desc
    ranked = sorted(products, key=lambda p: int(p.get('views') or 1), reverse=True)
    for p in ranked:
        try:
            pid = int(p.get('id') or 0)
        except (TypeError, ValueError):
            continue
        if pid and pid not in exclude:
            return pid, (p.get('naziv') or '')[:120]
    return None, ''


def maybe_run_ai_prodaja(request, visitor=None):
    """
    AI prodaja — jedan ulaz: delegira na browse_interest (1–2 artikla, max 2 popup-a).
    Ne šalje odvojene single LiveVisitorOffer da se ne dupliraju ponude.
    """
    try:
        from .browse_interest_offer import maybe_create_browse_interest_offer
        return maybe_create_browse_interest_offer(request, visitor)
    except Exception:
        return None
