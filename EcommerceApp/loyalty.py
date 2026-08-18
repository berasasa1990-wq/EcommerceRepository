import hashlib
import hmac
import io
import re
import secrets
import time
from decimal import Decimal
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth.models import User
from django.db.models import Q, Sum

from .models import Coupon, LoyaltyCard, LoyaltyPurchase, Order, UserProfile

# Session key za pending OTP pri evidentiranju kupovine
LOYALTY_PURCHASE_OTP_SESSION_KEY = 'loyalty_purchase_otp'
LOYALTY_PURCHASE_OTP_TTL_SEC = 10 * 60  # 10 min
LOYALTY_PURCHASE_OTP_MAX_ATTEMPTS = 5
LOYALTY_OPEN_OTP_SESSION_KEY = 'loyalty_open_otp'
LOYALTY_OPEN_OTP_TTL_SEC = 5 * 60


LOYALTY_TIERS = (
    {
        'nivo': 'bronza',
        'label': 'Bronza',
        'postotak': Decimal('3'),
        'od': Decimal('0'),
        'do': Decimal('300'),
    },
    {
        'nivo': 'srebrna',
        'label': 'Srebrna',
        'postotak': Decimal('5'),
        'od': Decimal('301'),
        'do': Decimal('600'),
    },
    {
        'nivo': 'zlatna',
        'label': 'Zlatna',
        'postotak': Decimal('7'),
        'od': Decimal('601'),
        'do': Decimal('900'),
    },
    {
        'nivo': 'platinum',
        'label': 'Platinum',
        'postotak': Decimal('10'),
        'od': Decimal('901'),
        'do': None,
    },
)

# (accent, accent_soft, accent_text) — premium kartica je uvijek crna kao site header
TIER_COLORS = {
    'bronza': ('#C49A6C', '#8B5E3C', '#E8D5B5'),
    'srebrna': ('#D1D5DB', '#9CA3AF', '#F3F4F6'),
    'zlatna': ('#FBBF24', '#D97706', '#FDE68A'),
    'platinum': ('#A5B4FC', '#818CF8', '#E0E7FF'),
}

# Brend boje sajta (header)
LOYALTY_CARD_BG = '#0A0A0A'
LOYALTY_CARD_BG_MID = '#111111'
LOYALTY_CARD_GREEN = '#5BB805'
LOYALTY_REVIEW_URL = 'https://g.page/r/CXurB2BnmyVdEBM/review'


def _normalizuj_telefon(telefon):
    """Samo cifre (bez +, razmaka, crtica…)."""
    return re.sub(r'\D', '', telefon or '')


# BH/HR dijakritici → ASCII za pretragu imena (Božan ≈ Bozan)
_LOYALTY_DIACRITIC_MAP = str.maketrans({
    'š': 's', 'đ': 'd', 'č': 'c', 'ć': 'c', 'ž': 'z',
    'Š': 's', 'Đ': 'd', 'Č': 'c', 'Ć': 'c', 'Ž': 'z',
})


def loyalty_search_fold(value):
    """casefold + bez dijakritika (ž→z, š→s, č/ć→c)."""
    if not value:
        return ''
    return str(value).casefold().translate(_LOYALTY_DIACRITIC_MAP)


def _loyalty_name_query_variants(term, *, max_variants=48):
    """
    Varijante upita za SQL icontains: bozan ↔ božan, cosic ↔ čosić…
    Ograničeno da ne eksplodira broj OR grana.
    """
    term = (term or '').strip()
    if not term:
        return []
    folded = loyalty_search_fold(term)
    variants = {term, term.casefold(), folded}
    # Proširi ASCII slova s mogućim dijakriticima
    expand = {
        's': 'sš', 'š': 'sš',
        'z': 'zž', 'ž': 'zž',
        'c': 'cčć', 'č': 'cčć', 'ć': 'cčć',
        'd': 'dđ', 'đ': 'dđ',
    }
    base = folded
    # generiši zamjene pozicija po jednoj (brzo, dovoljno za imena)
    chars = list(base)
    for i, ch in enumerate(chars):
        opts = expand.get(ch)
        if not opts:
            continue
        for alt in opts:
            if alt == ch:
                continue
            trial = ''.join(chars[:i] + [alt] + chars[i + 1:])
            variants.add(trial)
            variants.add(trial.casefold())
    # ako je upit kratak, pokušaj i dvostruke zamjene na prva 2 “osjetljiva” mjesta
    sens = [i for i, ch in enumerate(chars) if ch in expand]
    if len(sens) >= 2 and len(variants) < max_variants:
        i, j = sens[0], sens[1]
        for a in expand[chars[i]]:
            for b in expand[chars[j]]:
                trial = list(chars)
                trial[i], trial[j] = a, b
                variants.add(''.join(trial))
                if len(variants) >= max_variants:
                    break
            if len(variants) >= max_variants:
                break
    out = []
    seen = set()
    for v in variants:
        v = (v or '').strip()
        if len(v) < 2:
            continue
        key = v.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
        if len(out) >= max_variants:
            break
    return out


def search_loyalty_cards(query, *, limit=30, mode='code'):
    """
    Pretraga loyalty kupaca.
    mode=code: broj kartice, barkod, telefon.
    mode=name: ime i prezime (dijakritici ž≈z, š≈s, č/ć≈c).
    """
    q = (query or '').strip()
    if not q:
        return []
    mode = (mode or 'code').strip().lower()
    if mode not in {'code', 'name', 'any'}:
        mode = 'code'

    name_q = Q()
    for v in _loyalty_name_query_variants(q):
        name_q |= Q(user__first_name__icontains=v) | Q(user__last_name__icontains=v)

    phone_digits = _normalizuj_telefon(q)
    phone_q = Q()
    if len(phone_digits) >= 6:
        # lokalni / međunarodni dijelovi
        phone_q = (
            Q(user__profil__telefon__icontains=phone_digits)
            | Q(user__profil__telefon__icontains=phone_digits[-8:])
        )
        local = ba_mobile_local(q)
        if local:
            phone_q |= Q(user__profil__telefon__icontains=local)
            phone_q |= Q(user__profil__telefon__icontains=local[1:])  # bez 0

    if mode == 'name':
        filter_q = name_q if name_q else Q(pk__in=[])
    elif mode == 'any':
        filter_q = Q(kod__icontains=q) | Q(barkod__icontains=q)
        if name_q:
            filter_q |= name_q
        if phone_q:
            filter_q |= phone_q
    else:
        filter_q = Q(kod__icontains=q) | Q(barkod__icontains=q)
        if phone_q:
            filter_q |= phone_q

    cards_qs = list(
        LoyaltyCard.objects.select_related('user', 'user__profil')
        .filter(filter_q)
        .order_by('-azurirana')[: max(limit * 3, 60)]
    )

    fold_q = loyalty_search_fold(q)
    results = []
    seen_ids = set()

    def _card_match_rank(card):
        user = card.user
        full = f'{user.first_name or ""} {user.last_name or ""}'.strip()
        email = user.email or ''
        phone = ''
        profil = getattr(user, 'profil', None)
        if profil:
            phone = profil.telefon or ''
        hay_fold = loyalty_search_fold(
            f'{card.kod} {card.barkod} {email} {full} {phone}'
        )
        ql = q.casefold()
        raw_ok = (
            ql in (card.kod or '').casefold()
            or ql in (card.barkod or '').casefold()
            or ql in email.casefold()
            or ql in full.casefold()
            or (phone_digits and phone_digits in _normalizuj_telefon(phone))
        )
        fold_ok = bool(fold_q and fold_q in hay_fold)
        if raw_ok or fold_ok:
            return 0
        return 1

    for card in cards_qs:
        if card.pk in seen_ids:
            continue
        results.append((_card_match_rank(card), card))
        seen_ids.add(card.pk)

    # Dopuna: sken imena s dijakriticima (upit bez ž, ime s ž — i obrnuto)
    if mode in {'name', 'any'} and len(results) < limit and fold_q and len(fold_q) >= 2:
        for card in (
            LoyaltyCard.objects.select_related('user', 'user__profil')
            .order_by('-azurirana')[:500]
        ):
            if card.pk in seen_ids:
                continue
            user = card.user
            full = f'{user.first_name or ""} {user.last_name or ""}'.strip()
            if fold_q in loyalty_search_fold(full):
                results.append((0, card))
                seen_ids.add(card.pk)
            if len(results) >= limit * 2:
                break

    results.sort(
        key=lambda row: (
            row[0],
            -(row[1].azurirana.timestamp() if row[1].azurirana else 0),
        ),
    )
    return [c for _, c in results[:limit]]


def normalizuj_email(email):
    return (email or '').strip().lower()


def _ba_national_digits(telefon):
    """
    Nacionalni dio BA broja BEZ vodeće 0.
    Primjeri (svi → 65666666):
      065666666, 0038765666666, +38765666666, 38765666666
    """
    digits = _normalizuj_telefon(telefon)
    if not digits:
        return ''
    if digits.startswith('00'):
        digits = digits[2:]
    if digits.startswith('387'):
        return digits[3:]
    if digits.startswith('0'):
        return digits[1:]
    # Već nacionalni bez 0 (npr. 65666666)
    return digits


def ba_mobile_e164(telefon):
    """
    Kanonski ključ za usporedbu: 387 + nacionalni (npr. 38765666666).
    Prazno ako nije valjan BA mobilni (06…).
    """
    national = _ba_national_digits(telefon)
    if not national:
        return ''
    # Mobilni: lokalno 06X… → nacionalni počinje s 6 (60–67)
    if not national.startswith('6'):
        return ''
    # Standard BA mobilni: 8–9 cifara nacionalno (06X XXX XXX)
    if len(national) < 8 or len(national) > 9:
        return ''
    return '387' + national


def ba_mobile_local(telefon):
    """
    Lokalni prikaz za spremanje: 06XXXXXXXX
    (isti broj kao +387 / 00387).
    """
    e164 = ba_mobile_e164(telefon)
    if not e164 or not e164.startswith('387'):
        return ''
    return '0' + e164[3:]


def validiraj_ba_mobilni(telefon, *, required=True):
    """
    Unos telefona: samo 06 + cifre, bez razmaka.
    Vraća (local_06, e164) ili diže ValueError.
    """
    raw = '' if telefon is None else str(telefon)
    if re.search(r'\s', raw):
        raise ValueError('Telefon ne smije imati razmake. Unesite 06 i broj, npr. 061234567.')
    raw = raw.strip()
    if not raw:
        if required:
            raise ValueError('Telefon je obavezan.')
        return '', ''
    if re.search(r'[^0-9]', raw):
        raise ValueError('Telefon smije sadržavati samo cifre, bez razmaka i znakova. Unesite 06 i broj.')
    if not raw.startswith('06'):
        raise ValueError('Telefon mora počinjati sa 06, npr. 061234567.')
    if not re.fullmatch(r'06\d{7,8}', raw):
        raise ValueError('Unesite 06 i zatim 7 ili 8 cifara, bez razmaka (npr. 061234567).')
    local = ba_mobile_local(raw)
    e164 = ba_mobile_e164(raw)
    if not local or not e164 or not local.startswith('06'):
        raise ValueError('Unesite ispravan mobilni broj koji počinje sa 06, npr. 061234567.')
    return local, e164


def _pronadji_korisnika_po_telefonu(telefon):
    """Pronađi user-a po telefonu — svi formati istog broja se tretiraju kao isti."""
    key = ba_mobile_e164(telefon) or _to_e164_digits(telefon)
    if not key or len(key) < 10:
        return None
    for profil in UserProfile.objects.select_related('user').exclude(telefon=''):
        other = ba_mobile_e164(profil.telefon) or _to_e164_digits(profil.telefon)
        if other and other == key:
            return profil.user
    return None


def telefon_vec_registrovan(telefon, *, exclude_user_id=None):
    user = _pronadji_korisnika_po_telefonu(telefon)
    if not user:
        return False
    if exclude_user_id and user.pk == exclude_user_id:
        return False
    return True


def email_vec_registrovan(email, *, exclude_user_id=None):
    email = normalizuj_email(email)
    if not email:
        return False
    qs = User.objects.filter(email__iexact=email)
    if exclude_user_id:
        qs = qs.exclude(pk=exclude_user_id)
    return qs.exists()


def _to_e164_digits(telefon):
    """
    Normalizuj BA broj u cifre s pozivnim (387…), bez +.
    Za mobilne koristi ba_mobile_e164; ovo je širi fallback (i fiksni).
    """
    mobile = ba_mobile_e164(telefon)
    if mobile:
        return mobile
    digits = _normalizuj_telefon(telefon)
    if not digits:
        return ''
    if digits.startswith('00'):
        digits = digits[2:]
    if digits.startswith('387'):
        pass
    elif digits.startswith('0') and len(digits) >= 8:
        digits = '387' + digits[1:]
    elif len(digits) in (8, 9) and not digits.startswith('387'):
        digits = '387' + digits.lstrip('0')
    if len(digits) < 10:
        return ''
    return digits


def _viber_draft_text(text):
    """
    Viber u composeru prekine draft na znaku %.
    Isti sadržaj kao WhatsApp, samo % → ' posto'.
    """
    return (text or '').replace('%', ' posto')


def viber_chat_url(telefon, text=''):
    """
    Deep link Viber chata na tačan broj (BA).
    draft= puni polje za poruku — staff samo klikne Pošalji.
    Broj ide kao %2B387… (plus mora biti enkodiran, inače Viber ga pročita kao razmak).
    Otvarati preko <a href> bez target=_blank — window.open / novi tab gubi draft.
    """
    digits = _to_e164_digits(telefon)
    if not digits:
        return ''
    url = f'viber://chat?number=%2B{digits}'
    if text:
        url = f'{url}&draft={quote(_viber_draft_text(text), safe="")}'
    return url


def whatsapp_chat_url(telefon, text=''):
    """
    WhatsApp web/app link — wa.me otvara instaliranu app ili WhatsApp Web.
    """
    digits = _to_e164_digits(telefon)
    if not digits:
        return ''
    if text:
        return f'https://wa.me/{digits}?text={quote(text)}'
    return f'https://wa.me/{digits}'


def whatsapp_app_url(telefon, text=''):
    """Native WhatsApp shema (kao viber://) — ne treba popup."""
    digits = _to_e164_digits(telefon)
    if not digits:
        return ''
    if text:
        return f'whatsapp://send?phone={digits}&text={quote(text)}'
    return f'whatsapp://send?phone={digits}'


def loyalty_from_phone():
    """Službeni broj s kojeg se šalju loyalty verifikacijski kodovi."""
    return (getattr(settings, 'LOYALTY_VIBER_FROM_PHONE', '') or '').strip()


def loyalty_from_phone_display():
    return format_loyalty_phone(loyalty_from_phone()) or loyalty_from_phone() or '—'


def sms_chat_url(telefon, text=''):
    """SMS deep link s prefilled body (mobilni)."""
    digits = _to_e164_digits(telefon)
    if not digits:
        return ''
    # iOS: sms:+387...&body=  / Android: sms:+387...?body=
    body = quote(text) if text else ''
    if body:
        return f'sms:+{digits}?&body={body}'
    return f'sms:+{digits}'


def _generate_otp_code():
    """4-cifreni kod (1000–9999)."""
    return f'{secrets.randbelow(9000) + 1000}'


def loyalty_desk_params(*, q='', mode='code', nivo='', extra=None):
    """GET parametri za loyalty desk — uvijek isti, dijeljiv URL."""
    params = {}
    q = (q or '').strip()
    mode = (mode or 'code').strip().lower()
    nivo = (nivo or '').strip().lower()
    if q:
        params['q'] = q
    if mode and mode not in {'code', ''}:
        params['mode'] = mode
    if nivo:
        params['nivo'] = nivo
    if extra:
        for key, value in extra.items():
            if value in (None, '', False):
                continue
            params[key] = '1' if value is True else str(value)
    return params


def loyalty_desk_url(path='/nalog/loyalty/', *, q='', mode='code', nivo='', extra=None):
    from urllib.parse import urlencode

    params = loyalty_desk_params(q=q, mode=mode, nivo=nivo, extra=extra)
    if not params:
        return path
    return f'{path}?{urlencode(params)}'


def loyalty_member_url(kod):
    from django.urls import reverse

    return reverse('staff_loyalty_member', kwargs={'kod': kod})


def open_card_otp_message(code):
    from_label = loyalty_from_phone_display()
    lines = [
        'opremazaribolov.ba — otvaranje kartice',
        f'Vaš 6-cifreni kod: {code}',
    ]
    if from_label and from_label != '—':
        lines.append(f'Poruka sa broja: {from_label}')
    return '\n'.join(lines)


def _generate_open_otp_code():
    return f'{secrets.randbelow(900000) + 100000}'


def start_open_card_otp(request, card, *, channel=''):
    if not request or not card:
        raise ValueError('Kartica nije dostupna.')
    profil = getattr(card.user, 'profil', None)
    telefon = (profil.telefon if profil else '') or ''
    if not _to_e164_digits(telefon):
        raise ValueError('Kartica nema ispravan telefon.')
    now = time.time()
    code = _generate_open_otp_code()
    payload = {
        'card_id': card.pk,
        'kod': card.kod,
        'code': code,
        'telefon': telefon,
        'channel': channel or '',
        'sent_ts': now,
        'exp': now + LOYALTY_OPEN_OTP_TTL_SEC,
        'attempts': 0,
    }
    request.session[LOYALTY_OPEN_OTP_SESSION_KEY] = payload
    request.session.modified = True
    msg = open_card_otp_message(code)
    return {
        **payload,
        'message': msg,
        'from_phone': loyalty_from_phone(),
        'from_phone_fmt': loyalty_from_phone_display(),
        'viber_url': viber_chat_url(telefon, msg),
        'whatsapp_url': whatsapp_chat_url(telefon, msg),
        'whatsapp_app_url': whatsapp_app_url(telefon, msg),
    }


def get_pending_open_card_otp(request):
    data = (request.session.get(LOYALTY_OPEN_OTP_SESSION_KEY) or {}) if request else {}
    if not data:
        return None
    if time.time() > float(data.get('exp') or 0):
        clear_pending_open_card_otp(request)
        return None
    return data


def clear_pending_open_card_otp(request):
    if request and LOYALTY_OPEN_OTP_SESSION_KEY in request.session:
        del request.session[LOYALTY_OPEN_OTP_SESSION_KEY]
        request.session.modified = True


def verify_open_card_otp(request, entered_code):
    data = get_pending_open_card_otp(request)
    if not data:
        return False, 'Kod je istekao. Pošaljite novi.'
    attempts = int(data.get('attempts') or 0) + 1
    data['attempts'] = attempts
    request.session[LOYALTY_OPEN_OTP_SESSION_KEY] = data
    request.session.modified = True
    if attempts > 5:
        clear_pending_open_card_otp(request)
        return False, 'Previše pokušaja. Pošaljite novi kod.'
    if str(entered_code or '').strip() != str(data.get('code') or ''):
        return False, 'Kod nije tačan.'
    return True, data


def loyalty_desk_stats():
    qs = LoyaltyCard.objects.select_related('user')
    total = qs.count()
    active = qs.filter(user__is_active=True).count()
    potrosnja = qs.aggregate(s=Sum('ukupna_potrosnja'))['s'] or Decimal('0.00')
    avg = Decimal('0')
    if total:
        weighted = Decimal('0')
        for tier in LOYALTY_TIERS:
            n = qs.filter(nivo=tier['nivo']).count()
            weighted += Decimal(n) * Decimal(str(tier['postotak']))
        avg = (weighted / Decimal(total)).quantize(Decimal('0.1'))
    tiers = []
    for tier in LOYALTY_TIERS:
        od = tier['od']
        do = tier['do']
        raspon = f'{int(od)} – {int(do)} bodova' if do else f'{int(od)}+ bodova'
        tiers.append({
            **tier,
            'raspon': raspon,
            'popust_label': f'{int(tier["postotak"])}% popusta',
            'en_label': {
                'bronza': 'BRONZE',
                'srebrna': 'SILVER',
                'zlatna': 'GOLD',
                'platinum': 'PLATINUM',
            }.get(tier['nivo'], (tier['nivo'] or '').upper()),
            'count': qs.filter(nivo=tier['nivo']).count(),
        })
    top_card = (
        qs.exclude(ukupna_potrosnja__lte=0)
        .order_by('-ukupna_potrosnja', '-azurirana')
        .first()
    )
    top_spender = None
    if top_card:
        top_name = (top_card.user.get_full_name() or '').strip() or top_card.kod
        top_spender = {
            'name': top_name,
            'kod': top_card.kod,
            'url': loyalty_member_url(top_card.kod),
            'spend': top_card.ukupna_potrosnja,
            'spend_fmt': format_ba_money(top_card.ukupna_potrosnja),
        }
    return {
        'active': active,
        'total': total,
        'potrosnja': potrosnja,
        'bodovi': int(potrosnja),
        'avg_discount': avg,
        'active_fmt': format_ba_int(active),
        'bodovi_fmt': format_ba_int(int(potrosnja)),
        'potrosnja_fmt': format_ba_money(potrosnja),
        'avg_discount_fmt': format_ba_pct(avg),
        'tiers': tiers,
        'top_spender': top_spender,
    }


def _loyalty_year_bounds(year):
    from datetime import datetime

    from django.utils import timezone as dj_tz

    tz = dj_tz.get_current_timezone()
    start = dj_tz.make_aware(datetime(int(year), 1, 1), tz)
    end = dj_tz.make_aware(datetime(int(year) + 1, 1, 1), tz)
    return start, end


def loyalty_desk_purchase_years():
    from django.db.models.functions import ExtractYear

    years = set(
        LoyaltyPurchase.objects.annotate(y=ExtractYear('kreirano'))
        .values_list('y', flat=True)
        .distinct()
    )
    user_ids = LoyaltyCard.objects.values_list('user_id', flat=True)
    years.update(
        Order.objects.exclude(status=Order.Status.OTKAZANA)
        .filter(korisnik_id__in=user_ids)
        .annotate(y=ExtractYear('kreirana'))
        .values_list('y', flat=True)
        .distinct()
    )
    years.discard(None)
    return sorted((int(y) for y in years), reverse=True)


def loyalty_desk_purchase_ledger(year=None, page=1, per_page=15):
    """Sve loyalty kupovine (prodavnica + online) za desk, po godini."""
    from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
    from django.utils import timezone as dj_tz

    years = loyalty_desk_purchase_years()
    try:
        year = int(year or 0)
    except (TypeError, ValueError):
        year = 0
    if year not in years:
        year = years[0] if years else dj_tz.localdate().year

    start, end = _loyalty_year_bounds(year)
    rows = []
    for pur in (
        LoyaltyPurchase.objects.filter(kreirano__gte=start, kreirano__lt=end)
        .select_related('kartica', 'kartica__user')
        .order_by('-kreirano')
    ):
        card = pur.kartica
        user = getattr(card, 'user', None)
        name = ((user.get_full_name() if user else '') or '').strip() or (
            card.kod if card else '—'
        )
        rows.append({
            'date': pur.kreirano,
            'name': name,
            'kod': card.kod if card else '',
            'url': loyalty_member_url(card.kod) if card else '',
            'label': 'Evidentirano',
            'amount': pur.iznos,
            'amount_fmt': format_ba_money(pur.iznos),
            'points_fmt': format_ba_int(int(pur.iznos or 0)),
            'payment': (
                'Kartica' if pur.placanje == LoyaltyPurchase.Placanje.KARTICA
                else 'Gotovina'
            ),
            'channel': 'Prodavnica',
            'status': 'Završena',
        })

    kod_by_user = dict(LoyaltyCard.objects.values_list('user_id', 'kod'))
    for order in (
        Order.objects.exclude(status=Order.Status.OTKAZANA)
        .filter(korisnik_id__in=kod_by_user.keys(), kreirana__gte=start, kreirana__lt=end)
        .select_related('korisnik')
        .order_by('-kreirana')
    ):
        kod = kod_by_user.get(order.korisnik_id) or ''
        name = ''
        if order.korisnik:
            name = (order.korisnik.get_full_name() or '').strip()
        name = name or kod or '—'
        if order.status in ('zavrsena', 'poslana', 'potvrdjena'):
            status = 'Završena'
        elif hasattr(order, 'get_status_label'):
            status = order.get_status_label()
        else:
            status = order.status
        rows.append({
            'date': order.kreirana,
            'name': name,
            'kod': kod,
            'url': loyalty_member_url(kod) if kod else '',
            'label': f'#{order.broj}',
            'amount': order.ukupno,
            'amount_fmt': format_ba_money(order.ukupno),
            'points_fmt': format_ba_int(int(order.ukupno or 0)),
            'payment': 'Kartica',
            'channel': 'Web',
            'status': status,
        })

    rows.sort(key=lambda row: row.get('date') or start, reverse=True)
    total = sum((row.get('amount') or Decimal('0')) for row in rows)
    paginator = Paginator(rows, per_page)
    try:
        page_obj = paginator.get_page(page)
    except (EmptyPage, PageNotAnInteger):
        page_obj = paginator.get_page(1)
    return {
        'years': years,
        'year': year,
        'page': page_obj,
        'count': len(rows),
        'total_fmt': format_ba_money(total),
    }


def recent_loyalty_cards(limit=8):
    return list(
        LoyaltyCard.objects.select_related('user', 'user__profil')
        .order_by('-azurirana')[:limit]
    )


def format_ba_int(value):
    try:
        n = int(Decimal(str(value or 0)))
    except Exception:
        n = 0
    return f'{n:,}'.replace(',', '.')


def format_ba_money(value):
    try:
        d = Decimal(str(value or 0)).quantize(Decimal('0.01'))
    except Exception:
        d = Decimal('0.00')
    sign = '-' if d < 0 else ''
    d = abs(d)
    whole = int(d)
    frac = int((d - Decimal(whole)) * 100)
    return f"{sign}{whole:,}".replace(',', '.') + f',{frac:02d}'


def format_ba_pct(value):
    try:
        d = Decimal(str(value or 0)).quantize(Decimal('0.1'))
    except Exception:
        d = Decimal('0.0')
    return f'{d:.1f}'.replace('.', ',')


def loyalty_page_items(page):
    n = page.paginator.num_pages
    cur = page.number
    if n <= 7:
        return list(range(1, n + 1))
    if cur <= 4:
        return [1, 2, 3, 4, 5, '...', n]
    if cur >= n - 3:
        return [1, '...', n - 4, n - 3, n - 2, n - 1, n]
    return [1, '...', cur - 1, cur, cur + 1, '...', n]


def format_loyalty_phone(raw):
    local = ba_mobile_local(raw) or (raw or '').strip()
    digits = _to_e164_digits(local) or _normalizuj_telefon(local)
    if digits.startswith('387') and len(digits) >= 11:
        rest = digits[3:]
        return f'+387 {rest[:2]} {rest[2:5]} {rest[5:]}'.strip()
    return local or '—'


def loyalty_phone_local_display(raw):
    """Unos na desku: 06XXXXXXXX, bez razmaka."""
    return ba_mobile_local(raw) or ''


def purchase_otp_message(code, *, iznos=None):
    """Tekst poruke za Viber/WhatsApp — kupac izdiktira kod prodavcu."""
    from_label = loyalty_from_phone_display()
    lines = [
        'opremazaribolov.ba — potvrda kupovine',
        f'Vaš kod: {code}',
        'Recite ovaj kod prodavcu da se kupovina evidentira.',
    ]
    if from_label and from_label != '—':
        lines.append(f'Poruka sa broja: {from_label}')
    if iznos is not None:
        try:
            lines.append(f'Iznos: {Decimal(str(iznos)).quantize(Decimal("0.01"))} KM')
        except Exception:
            lines.append(f'Iznos: {iznos} KM')
    return '\n'.join(lines)


def start_purchase_otp(request, card, iznos, napomena=''):
    """
    Generiši OTP i sačuvaj pending kupovinu u sesiji.
    Vraća dict za UI (code se ne šalje u HTML u produkciji — samo za deep-link poruku staffu).
    """
    if not request or not card:
        raise ValueError('Kartica nije dostupna.')
    profil = getattr(card.user, 'profil', None)
    telefon = (profil.telefon if profil else '') or ''
    if not _to_e164_digits(telefon):
        raise ValueError(
            'Kartica nema ispravan telefon. Unesite telefon u ličnim podacima '
            'prije evidentiranja, ili koristite admin override.'
        )
    try:
        iznos_d = Decimal(str(iznos)).quantize(Decimal('0.01'))
    except Exception as exc:
        raise ValueError('Neispravan iznos.') from exc
    if iznos_d <= 0:
        raise ValueError('Iznos mora biti veći od 0.')

    code = _generate_otp_code()
    payload = {
        'card_id': card.pk,
        'iznos': str(iznos_d),
        'napomena': (napomena or '')[:200],
        'code': code,
        'created_ts': time.time(),
        'attempts': 0,
        'telefon': telefon,
    }
    request.session[LOYALTY_PURCHASE_OTP_SESSION_KEY] = payload
    request.session.modified = True

    msg = purchase_otp_message(code, iznos=iznos_d)
    return {
        'card_id': card.pk,
        'iznos': iznos_d,
        'napomena': payload['napomena'],
        'telefon': telefon,
        'message': msg,
        'viber_url': viber_chat_url(telefon, msg),
        'whatsapp_url': whatsapp_chat_url(telefon, msg),
        'sms_url': sms_chat_url(telefon, msg),
        'ttl_minutes': LOYALTY_PURCHASE_OTP_TTL_SEC // 60,
    }


def get_pending_purchase_otp(request, card=None):
    """Vrati pending OTP iz sesije (ili None ako nema / isteklo / pogrešna kartica)."""
    data = request.session.get(LOYALTY_PURCHASE_OTP_SESSION_KEY)
    if not isinstance(data, dict):
        return None
    try:
        created = float(data.get('created_ts') or 0)
    except (TypeError, ValueError):
        created = 0
    if not created or (time.time() - created) > LOYALTY_PURCHASE_OTP_TTL_SEC:
        clear_pending_purchase_otp(request)
        return None
    if card is not None:
        try:
            if int(data.get('card_id') or 0) != int(card.pk):
                return None
        except (TypeError, ValueError):
            return None
    return data


def clear_pending_purchase_otp(request):
    if LOYALTY_PURCHASE_OTP_SESSION_KEY in request.session:
        del request.session[LOYALTY_PURCHASE_OTP_SESSION_KEY]
        request.session.modified = True


def verify_purchase_otp(request, entered_code, card):
    """
    Provjeri kod. Vraća (True, payload) ili (False, error_message).
    Ne kreira kupovinu — samo validira.
    """
    data = get_pending_purchase_otp(request, card=card)
    if not data:
        return False, 'Kod je istekao ili nije zatražen. Pošaljite novi kod.'
    try:
        attempts = int(data.get('attempts') or 0)
    except (TypeError, ValueError):
        attempts = 0
    if attempts >= LOYALTY_PURCHASE_OTP_MAX_ATTEMPTS:
        clear_pending_purchase_otp(request)
        return False, 'Previše pogrešnih pokušaja. Zatražite novi kod.'

    expected = str(data.get('code') or '').strip()
    got = re.sub(r'\D', '', str(entered_code or ''))
    if len(got) != 4 or got != expected:
        data['attempts'] = attempts + 1
        request.session[LOYALTY_PURCHASE_OTP_SESSION_KEY] = data
        request.session.modified = True
        left = LOYALTY_PURCHASE_OTP_MAX_ATTEMPTS - data['attempts']
        if left <= 0:
            clear_pending_purchase_otp(request)
            return False, 'Pogrešan kod. Previše pokušaja — zatražite novi kod.'
        return False, f'Pogrešan kod. Preostalo pokušaja: {left}.'
    return True, data


def commit_loyalty_purchase(
    card,
    iznos,
    *,
    napomena='',
    verifikacija=LoyaltyPurchase.Verifikacija.OTP,
    staff_user=None,
    placanje=LoyaltyPurchase.Placanje.GOTOVINA,
):
    """Upiši kupovinu + ažuriraj potrošnju/nivo kartice."""
    try:
        iznos_d = Decimal(str(iznos)).quantize(Decimal('0.01'))
    except Exception as exc:
        raise ValueError('Neispravan iznos.') from exc
    if iznos_d <= 0:
        raise ValueError('Iznos mora biti veći od 0.')
    if placanje not in {LoyaltyPurchase.Placanje.GOTOVINA, LoyaltyPurchase.Placanje.KARTICA}:
        placanje = LoyaltyPurchase.Placanje.GOTOVINA

    purchase = LoyaltyPurchase.objects.create(
        kartica=card,
        iznos=iznos_d,
        napomena=(napomena or '')[:200],
        verifikacija=verifikacija,
        placanje=placanje,
        kreirao=staff_user if getattr(staff_user, 'is_authenticated', False) else None,
    )
    # Preračunaj iz online + svih evidentiranih (uključujući ovu) — bez dvostrukog zbrajanja
    preracunaj_potrosnju_kartice(card)
    return purchase


def izdaj_loyalty_karticu(ime, prezime, telefon, email=''):
    """
    Registruje kupca i izdaje loyalty karticu.
    Telefon: obavezan BA mobilni (06…); +387 / 00387 se tretiraju kao isti broj.
    Email: opcionalan, bez duplikata.
    """
    ime = (ime or '').strip()
    prezime = (prezime or '').strip()
    email = normalizuj_email(email)

    if not ime or not prezime:
        raise ValueError('Ime i prezime su obavezni.')

    # Normalizuj na 06… i provjeri da je mobilni
    telefon_local, e164 = validiraj_ba_mobilni(telefon)

    # Duplikati: 065… == +38765… == 0038765…
    if telefon_vec_registrovan(telefon_local) or telefon_vec_registrovan(e164):
        raise ValueError('Ovaj broj telefona je već registrovan — isti telefon nije dozvoljen.')
    if email:
        if email_vec_registrovan(email):
            raise ValueError(
                'Ovaj email je već registrovan na loyalty karticu — '
                'dupli email nije dozvoljen.'
            )
        if User.objects.filter(username__iexact=email).exists():
            raise ValueError(
                'Ovaj email je već u upotrebi — dupli email nije dozvoljen.'
            )

    digits = e164 or _normalizuj_telefon(telefon_local) or secrets.token_hex(4)
    username = f'loy_{digits}'
    while User.objects.filter(username=username).exists():
        username = f'loy_{digits}_{secrets.token_hex(2)}'

    user = User.objects.create_user(
        username=username,
        email=email or '',
        password=secrets.token_urlsafe(32),
        first_name=ime,
        last_name=prezime,
        is_active=True,
    )
    # Uvijek spremi lokalni oblik 06…
    UserProfile.objects.create(user=user, telefon=telefon_local)

    card = osiguraj_loyalty_karticu(user)
    return card, user


def _generisi_kod(user=None):
    """Jedinstveni 6-cifreni broj kartice (100000–999999)."""
    for _ in range(60):
        kod = f'{secrets.randbelow(900000) + 100000}'
        taken = LoyaltyCard.objects.filter(Q(kod=kod) | Q(barkod=kod)).exists()
        if not taken and not Coupon.objects.filter(kod=kod).exists():
            return kod
    raise RuntimeError('Nije moguće generisati jedinstveni 6-cifreni broj kartice.')


def osiguraj_sestocifreni_kod(card):
    """Stare OZ… šifre pretvori u 6 cifara. Već 6-cifrene ostaju."""
    if not card:
        return card
    if re.fullmatch(r'\d{6}', (card.kod or '').strip()):
        return card
    old = card.kod
    new_kod = _generisi_kod(getattr(card, 'user', None))
    card.kod = new_kod
    fields = ['kod', 'azurirana']
    if not card.barkod or card.barkod == old:
        card.barkod = new_kod
        fields.append('barkod')
    card.save(update_fields=fields)
    sync_loyalty_coupon(card)
    return card


def _barkod_iz_koda(kod):
    """Generiše dovoljno crno/bijelih traka za vizuelni barkod (ne ovisno o dužini koda)."""
    text = (kod or 'OZ00000').upper()
    bars = []
    # Quiet zone + start-like pattern
    seed = sum(ord(c) for c in text) or 1
    pattern = []
    for i, ch in enumerate(text * 4):
        # alternirajuće širine 1–3
        w = ((ord(ch) + seed + i * 7) % 3) + 1
        pattern.append(w)
    # 40–56 traka za punu širinu
    while len(pattern) < 48:
        pattern.extend(pattern[:8])
    return pattern[:52]


def generisi_loyalty_barcode_png(data):
    """PNG bytes pravog Code128 barkoda (bijela pozadina)."""
    return _barcode_png_bytes(data)


def _barcode_png_bytes(data):
    from barcode import Code128
    from barcode.writer import ImageWriter
    from PIL import Image

    buffer = io.BytesIO()
    code = Code128(str(data or 'OZ'), writer=ImageWriter())
    code.write(
        buffer,
        options={
            'module_width': 0.4,
            'module_height': 14.0,
            'quiet_zone': 1.5,
            'font_size': 0,
            'text_distance': 1,
            'write_text': False,
            'background': 'white',
            'foreground': 'black',
        },
    )
    buffer.seek(0)
    img = Image.open(buffer).convert('RGB')
    # Ukloni višak praznine, ostavi pun barkod
    out = io.BytesIO()
    img.save(out, format='PNG', optimize=True)
    return out.getvalue()


def nivo_za_potrosnju(ukupno):
    ukupno = Decimal(ukupno)
    for tier in reversed(LOYALTY_TIERS):
        if ukupno >= tier['od']:
            return tier
    return LOYALTY_TIERS[0]


def tier_info(nivo):
    for tier in LOYALTY_TIERS:
        if tier['nivo'] == nivo:
            return tier
    return LOYALTY_TIERS[0]


def _orders_for_loyalty_user(user):
    """
    Online narudžbe koje pripadaju loyalty kupcu:
    - povezane preko korisnik FK
    - ili email (gost checkout s istim emailom)
    - ili telefon (isti BA mobilni, bilo koji format)
    Bez otkazanih. Distinct po id.
    """
    if not user or not getattr(user, 'pk', None):
        return Order.objects.none()

    base = Order.objects.exclude(status=Order.Status.OTKAZANA)
    q = Q(korisnik=user)
    email = normalizuj_email(getattr(user, 'email', '') or '')
    if email:
        q |= Q(email__iexact=email)

    order_ids = set(
        base.filter(q).values_list('pk', flat=True),
    )

    profil = getattr(user, 'profil', None)
    phone_key = ba_mobile_e164((profil.telefon if profil else '') or '')
    if phone_key and phone_key.startswith('387') and len(phone_key) >= 11:
        national = phone_key[3:]  # npr. 65666666
        candidates = (
            base.exclude(pk__in=order_ids)
            .exclude(telefon='')
            .filter(
                Q(telefon__icontains=national)
                | Q(telefon__icontains=f'0{national}')
            )
            .only('pk', 'telefon')
        )
        for order in candidates.iterator(chunk_size=200):
            other = ba_mobile_e164(order.telefon)
            if other and other == phone_key:
                order_ids.add(order.pk)

    if not order_ids:
        return Order.objects.none()
    return base.filter(pk__in=order_ids)


def ukupna_potrosnja_korisnika(user):
    """Samo online narudžbe (bez ručnih LoyaltyPurchase)."""
    if not user or not getattr(user, 'pk', None):
        return Decimal('0')
    total = _orders_for_loyalty_user(user).aggregate(total=Sum('ukupno'))['total']
    return Decimal(total or 0)


def ukupna_potrosnja_za_karticu(card):
    """
    Ukupna potrošnja kartice = online narudžbe (FK / email / telefon)
    + evidentirane prodavnica kupovine (LoyaltyPurchase).
    Ne ovisi o tome je li unesen loyalty kod / popust na narudžbi.
    """
    if not card:
        return Decimal('0')
    online = ukupna_potrosnja_korisnika(card.user)
    manual = (
        LoyaltyPurchase.objects.filter(kartica=card)
        .aggregate(total=Sum('iznos'))['total']
    )
    return Decimal(online or 0) + Decimal(manual or 0)


def online_orders_for_loyalty_card(card, *, limit=50):
    """Lista online narudžbi za timeline na loyalty kartici."""
    if not card:
        return []
    return list(
        _orders_for_loyalty_user(card.user)
        .prefetch_related('stavke')
        .order_by('-kreirana')[:limit]
    )


def pronadji_loyalty_karticu_za_narudzbu(order):
    """
    Pronađi postojeću loyalty karticu za narudžbu — BEZ uslova da je unesen kod.
    Redoslijed: korisnik → kupon/loyalty kod → email → telefon.
    Ne kreira novu karticu.
    """
    if not order:
        return None

    # 1) Prijavljeni korisnik
    if order.korisnik_id:
        card = getattr(order.korisnik, 'loyalty_kartica', None)
        if card:
            return card

    # 2) Unesen loyalty / kupon kod (ako postoji)
    kod = (getattr(order, 'kupon_kod', None) or '').strip()
    if kod:
        card = _pronadji_loyalty_karticu_po_kodu(kod)
        if card:
            return card
        coupon = (
            Coupon.objects
            .filter(kod__iexact=kod)
            .select_related('loyalty_kartica', 'loyalty_kartica__user')
            .first()
        )
        if coupon and coupon.loyalty_kartica_id:
            return coupon.loyalty_kartica

    # 3) Email
    email = normalizuj_email(getattr(order, 'email', '') or '')
    if email:
        user = (
            User.objects
            .filter(email__iexact=email)
            .select_related('loyalty_kartica')
            .first()
        )
        if user:
            card = getattr(user, 'loyalty_kartica', None)
            if card:
                return card

    # 4) Telefon
    phone_key = ba_mobile_e164(getattr(order, 'telefon', '') or '')
    if phone_key:
        for profil in UserProfile.objects.select_related(
            'user', 'user__loyalty_kartica',
        ).exclude(telefon=''):
            other = ba_mobile_e164(profil.telefon)
            if other and other == phone_key:
                card = getattr(profil.user, 'loyalty_kartica', None)
                if card:
                    return card
    return None


def povezi_narudzbu_sa_loyalty_korisnikom(order, card):
    """Ako je gost narudžba — veži na vlasnika kartice (za buduće sumiranje / timeline)."""
    if not order or not card or not card.user_id:
        return order
    if order.korisnik_id:
        return order
    order.korisnik = card.user
    order.save(update_fields=['korisnik'])
    return order


def sync_loyalty_coupon(card):
    tier = tier_info(card.nivo)
    Coupon.objects.update_or_create(
        loyalty_kartica=card,
        defaults={
            'kod': card.kod,
            'naziv': f'Loyalty {tier["label"]}',
            'postotak': tier['postotak'],
            'vlasnik': card.user,
            'aktivan': True,
            'automatski': True,
        },
    )


def azuriraj_loyalty_karticu(card):
    tier = nivo_za_potrosnju(card.ukupna_potrosnja)
    card.nivo = tier['nivo']
    card.save(update_fields=['nivo', 'azurirana'])
    sync_loyalty_coupon(card)
    return card


def preracunaj_potrosnju_kartice(card):
    """Preračunaj ukupnu potrošnju i nivo iz narudžbi + evidentiranih kupovina."""
    if not card:
        return None
    if not card.barkod:
        card.barkod = card.kod
    card.ukupna_potrosnja = ukupna_potrosnja_za_karticu(card)
    card.save(update_fields=['ukupna_potrosnja', 'barkod', 'azurirana'])
    return azuriraj_loyalty_karticu(card)


def kreiraj_loyalty_karticu(user):
    kod = _generisi_kod(user)
    while LoyaltyCard.objects.filter(kod=kod).exists() or Coupon.objects.filter(kod=kod).exists():
        kod = _generisi_kod(user)

    card, created = LoyaltyCard.objects.get_or_create(
        user=user,
        defaults={
            'kod': kod,
            'barkod': kod,
            'nivo': 'bronza',
            'ukupna_potrosnja': Decimal('0'),
        },
    )
    if not created:
        return card
    if not card.barkod:
        card.barkod = card.kod
        card.save(update_fields=['barkod'])

    sync_loyalty_coupon(card)
    return card


def osiguraj_loyalty_karticu(user):
    card = getattr(user, 'loyalty_kartica', None)
    if card:
        osiguraj_sestocifreni_kod(card)
        return card
    return kreiraj_loyalty_karticu(user)


def azuriraj_loyalty_nakon_narudzbe(order):
    """
    Evidentiraj online kupovinu na loyalty kartici iako kupac
    NIJE unio kod kartice i NIJE dobio popust.

    Kartica se pronalazi po: prijavljenom nalogu, kupon kodu, emailu ili telefonu.
    Ako kartica ne postoji — ne kreira se automatski (samo prijava / izdavanje).
    """
    if not order:
        return None
    card = pronadji_loyalty_karticu_za_narudzbu(order)
    if not card:
        # Prijavljeni korisnik bez kartice: izdaj karticu pa upiši potrošnju
        if order.korisnik_id:
            card = osiguraj_loyalty_karticu(order.korisnik)
        else:
            return None
    povezi_narudzbu_sa_loyalty_korisnikom(order, card)
    return preracunaj_potrosnju_kartice(card)


def _pronadji_loyalty_karticu_po_kodu(kod):
    """Traži karticu po broju (kod) ili barkodu."""
    kod = (kod or '').strip()
    if not kod:
        return None
    return (
        LoyaltyCard.objects
        .filter(Q(kod__iexact=kod) | Q(barkod__iexact=kod))
        .select_related('user')
        .first()
    )


def validiraj_kupon(kod, user=None):
    """
    Validira kupon / broj loyalty kartice.

    Broj kartice (kod) se unosi u korpu i ostvaruje popust prema nivou kartice.
    Loyalty kartice rade i bez prijave — dovoljno je unijeti broj kartice.
    """
    kod = (kod or '').strip()
    if not kod:
        return None, 'Unesite broj kartice ili kupon kod.'

    coupon = (
        Coupon.objects
        .filter(kod__iexact=kod, aktivan=True)
        .select_related('vlasnik', 'loyalty_kartica')
        .first()
    )

    # Ako kupon ne postoji, pokušaj preko loyalty kartice (kod ili barkod)
    if not coupon:
        card = _pronadji_loyalty_karticu_po_kodu(kod)
        if card:
            azuriraj_loyalty_karticu(card)
            coupon = (
                Coupon.objects
                .filter(loyalty_kartica=card, aktivan=True)
                .select_related('vlasnik', 'loyalty_kartica')
                .first()
            )
            if not coupon:
                coupon = (
                    Coupon.objects
                    .filter(kod__iexact=card.kod, aktivan=True)
                    .select_related('vlasnik', 'loyalty_kartica')
                    .first()
                )

    if not coupon:
        return None, 'Broj kartice / kupon nije pronađen ili nije aktivan.'

    # Loyalty kartica: broj kartice u korpi = popust, bez obavezne prijave
    if coupon.automatski or coupon.loyalty_kartica_id:
        # Ažuriraj postotak prema trenutnom nivou kartice
        card = coupon.loyalty_kartica
        if card is None:
            card = _pronadji_loyalty_karticu_po_kodu(coupon.kod)
        if card:
            tier = tier_info(card.nivo)
            if coupon.postotak != tier['postotak'] or coupon.kod != card.kod:
                coupon.postotak = tier['postotak']
                coupon.kod = card.kod
                coupon.aktivan = True
                coupon.save(update_fields=['postotak', 'kod', 'aktivan'])
        return coupon, None

    # Ručni kupon s vlasnikom — samo vlasnik
    if coupon.vlasnik_id:
        if not user or not getattr(user, 'is_authenticated', False):
            return None, 'Morate biti prijavljeni da koristite ovaj kupon.'
        if coupon.vlasnik_id != user.pk:
            return None, 'Ovaj kupon možete koristiti samo vi.'

    return coupon, None


def loyalty_card_share_token(card):
    """HMAC token za javni link slike kartice (kupac vidi PNG bez logina)."""
    if not card or not getattr(card, 'pk', None):
        return ''
    secret = (getattr(settings, 'SECRET_KEY', '') or 'loyalty').encode('utf-8')
    payload = f'loyalty-card-img:{card.pk}:{card.kod}:{card.barkod or ""}'.encode('utf-8')
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()[:40]


def verify_loyalty_card_share_token(card, token):
    expected = loyalty_card_share_token(card)
    if not expected or not token:
        return False
    return hmac.compare_digest(str(token), str(expected))


def loyalty_card_caption(card, *, share_image_url=''):
    """
    Tekst poruke za WhatsApp / Viber.
    Podaci o kartici + link za Google recenziju.
    Slika se skida na računar, staff je priloži ručno.
    """
    del share_image_url  # namjerno se ne šalje u poruci
    tier = tier_info(card.nivo)
    next_tier = None
    for index, item in enumerate(LOYALTY_TIERS):
        if item['nivo'] == card.nivo and index + 1 < len(LOYALTY_TIERS):
            next_tier = LOYALTY_TIERS[index + 1]
            break
    preostalo = None
    if next_tier:
        preostalo = max(Decimal('0'), next_tier['od'] - card.ukupna_potrosnja)
    if next_tier and preostalo is not None:
        km = preostalo.quantize(Decimal('1') if preostalo == preostalo.to_integral() else Decimal('0.01'))
        next_line = f'Jos {km} KM do nivoa {next_tier["label"]} ({int(next_tier["postotak"])}%)'
    else:
        next_line = 'Najvisi nivo, maksimalni popust'
    return '\n'.join([
        'Vasa loyalty kartica - opremazaribolov.ba',
        f'Nivo: {tier["label"]} - Popust: {int(tier["postotak"])}%',
        next_line,
        f'Broj kartice: {card.kod}',
        '',
        'Ostavi recenziju:',
        LOYALTY_REVIEW_URL,
    ])



def loyalty_kontekst(card, *, share_image_url=''):
    tier = tier_info(card.nivo)
    next_tier = None
    for index, item in enumerate(LOYALTY_TIERS):
        if item['nivo'] == card.nivo and index + 1 < len(LOYALTY_TIERS):
            next_tier = LOYALTY_TIERS[index + 1]
            break

    preostalo = None
    if next_tier:
        preostalo = max(Decimal('0'), next_tier['od'] - card.ukupna_potrosnja)

    profil = getattr(card.user, 'profil', None)
    telefon = (profil.telefon if profil else '') or ''

    if next_tier and preostalo is not None:
        next_line = (
            f'Još {preostalo.quantize(Decimal("0.01"))} KM do nivoa '
            f'{next_tier["label"]} ({next_tier["postotak"]}%)'
        )
    else:
        next_line = 'Najviši nivo — maksimalni popust'

    card_caption = loyalty_card_caption(card, share_image_url=share_image_url)

    return {
        'kartica': card,
        'tier': tier,
        'next_tier': next_tier,
        'preostalo_do_sljedeceg': preostalo,
        'barkod_trake': _barkod_iz_koda(card.barkod or card.kod),
        'tiers': LOYALTY_TIERS,
        'telefon': telefon,
        'viber_url': viber_chat_url(telefon, card_caption),
        'whatsapp_url': whatsapp_chat_url(telefon, card_caption),
        'viber_caption': card_caption,
        'card_caption': card_caption,
        'share_image_url': share_image_url or '',
        'share_token': loyalty_card_share_token(card),
        'next_line': next_line,
    }


def _qr_image(data, box_size=6, border=1):
    import qrcode

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    return qr.make_image(fill_color='black', back_color='white').convert('RGB')


def _barcode_image(data):
    from PIL import Image

    return Image.open(io.BytesIO(_barcode_png_bytes(data))).convert('RGB')


def _hex_to_rgb(value):
    value = value.lstrip('#')
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _load_font(size, bold=False):
    from PIL import ImageFont

    candidates = [
        '/System/Library/Fonts/Supplemental/Arial Bold.ttf' if bold else '/System/Library/Fonts/Supplemental/Arial.ttf',
        '/System/Library/Fonts/Helvetica.ttc',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _load_site_logo_rgba(max_w=280, max_h=72):
    """Učitaj trenutni site logo (boje brenda) — RGBA, skaliran."""
    from PIL import Image

    try:
        from .models import SiteSettings
        settings_obj = SiteSettings.objects.first()
        field = None
        if settings_obj:
            field = settings_obj.logo or getattr(settings_obj, 'logo_glavni_sajt', None)
        if not field:
            return None
        path = getattr(field, 'path', None)
        if path:
            logo = Image.open(path).convert('RGBA')
        else:
            field.open('rb')
            try:
                logo = Image.open(io.BytesIO(field.read())).convert('RGBA')
            finally:
                try:
                    field.close()
                except Exception:
                    pass
        try:
            resample = Image.Resampling.LANCZOS
        except AttributeError:
            resample = Image.LANCZOS
        logo.thumbnail((max_w, max_h), resample)
        return logo
    except Exception:
        return None


def _fill_card_bg(draw, width, height, bg_top, bg_bot, green):
    """Crna podloga + zeleni brend rub (boje loga/headera)."""
    for y in range(height):
        ratio = y / max(height - 1, 1)
        color = tuple(int(bg_top[i] * (1 - ratio) + bg_bot[i] * ratio) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)
    draw.rectangle([0, 0, width, 6], fill=green)
    draw.rectangle([0, height - 6, width, height], fill=green)
    # lijevi zeleni accent
    draw.rectangle([0, 0, 6, height], fill=green)


def _paste_logo(img, logo, xy):
    if not logo:
        return
    if logo.mode == 'RGBA':
        img.paste(logo, xy, logo)
    else:
        img.paste(logo, xy)


def _draw_front_side(card, *, cardholder_name=None):
    """
    Prednja strana: čista crna pozadina kao header/logo podloga,
    veliki logo centriran na sredini.
    """
    from PIL import Image, ImageDraw

    del cardholder_name  # na prednjoj strani nije potreban
    ctx = loyalty_kontekst(card)
    tier = ctx['tier']

    # Čista crna kao logo pozadina / site header (#0a0a0a)
    black = (10, 10, 10)
    green = _hex_to_rgb(LOYALTY_CARD_GREEN)
    muted = (140, 140, 140)

    width, height = 1000, 560
    img = Image.new('RGB', (width, height), black)
    draw = ImageDraw.Draw(img)

    font_brand = _load_font(26, bold=True)
    font_micro = _load_font(12)
    font_tier = _load_font(14, bold=True)

    # Veliki logo — centriran na crnoj pozadini (kao na headeru)
    logo = _load_site_logo_rgba(max_w=780, max_h=300)
    if logo:
        lx = (width - logo.width) // 2
        ly = (height - logo.height) // 2 - 16
        _paste_logo(img, logo, (lx, ly))
        draw = ImageDraw.Draw(img)
    else:
        draw.text((width // 2 - 170, height // 2 - 20), 'opremazaribolov.ba', fill='white', font=font_brand)

    # Minimalan footer na crnoj
    draw.text((width // 2 - 72, height - 48), 'LOYALTY', fill=green, font=font_tier)
    tier_label = tier['label'].upper()
    # nivo desno od LOYALTY
    draw.text((width // 2 + 20, height - 48), tier_label, fill=muted, font=font_tier)
    draw.text((width // 2 - 70, height - 26), 'opremazaribolov.ba', fill=(90, 90, 90), font=font_micro)

    return img


def _draw_back_side(card, *, cardholder_name=None):
    """
    Zadnja (jedina) strana za slanje: tanka, pregledna —
    vlasnik, %, broj, barkod, nivoi. Bez QR koda.
    """
    from PIL import Image, ImageDraw

    ctx = loyalty_kontekst(card)
    tier = ctx['tier']
    next_tier = ctx['next_tier']
    preostalo = ctx['preostalo_do_sljedeceg']
    kod = card.kod
    barkod = card.barkod or card.kod
    potrosnja = card.ukupna_potrosnja

    accent_hex, _soft, accent_text_hex = TIER_COLORS.get(card.nivo, TIER_COLORS['bronza'])
    accent = _hex_to_rgb(accent_hex)
    accent_text = _hex_to_rgb(accent_text_hex)
    green = _hex_to_rgb(LOYALTY_CARD_GREEN)
    bg_top = _hex_to_rgb(LOYALTY_CARD_BG)
    bg_bot = _hex_to_rgb(LOYALTY_CARD_BG_MID)
    panel = (18, 18, 18)
    panel_border = (40, 40, 40)
    muted = (150, 150, 150)
    soft = (220, 220, 220)

    name = (
        cardholder_name
        or card.user.get_full_name()
        or (card.user.email or '').strip().lower()
        or 'Kupac'
    ).strip()
    name_is_email = '@' in name
    name_display = (name.lower() if name_is_email else name.upper())[:36]
    pct = str(tier['postotak']).rstrip('0').rstrip('.') if '.' in str(tier['postotak']) else str(tier['postotak'])
    if not pct:
        pct = str(tier['postotak'])
    if next_tier and preostalo is not None:
        next_text = (
            f'Još {preostalo.quantize(Decimal("1"))} KM → {next_tier["label"]} '
            f'({next_tier["postotak"]}%)'
        )
    else:
        next_text = 'Najviši nivo'

    # Tanka kartica ~ omjer bankovne (≈ 1.586)
    width, height = 1000, 560
    img = Image.new('RGB', (width, height), bg_top)
    draw = ImageDraw.Draw(img)
    _fill_card_bg(draw, width, height, bg_top, bg_bot, green)

    font_small = _load_font(14)
    font_name = _load_font(20 if name_is_email else 24, bold=True)
    font_code = _load_font(26, bold=True)
    font_label = _load_font(11)
    font_pct = _load_font(44, bold=True)
    font_tier = _load_font(13, bold=True)
    font_micro = _load_font(11)

    # Header: vlasnik lijevo, % desno
    draw.text((28, 20), 'VLASNIK', fill=muted, font=font_label)
    draw.text((28, 36), name_display, fill='white', font=font_name)

    draw.rounded_rectangle([720, 16, 972, 120], radius=14, fill=panel, outline=green, width=1)
    draw.text((744, 28), 'POPUST', fill=muted, font=font_label)
    draw.text((744, 48), f'{pct}%', fill=green, font=font_pct)
    draw.text((744, 98), f'{tier["label"]}', fill=accent_text, font=font_tier)

    # Srednji red: potrošnja + broj
    draw.rounded_rectangle([28, 100, 700, 180], radius=12, fill=panel, outline=panel_border, width=1)
    draw.text((48, 114), 'BROJ KARTICE', fill=muted, font=font_label)
    draw.text((48, 136), kod, fill='white', font=font_code)
    draw.text(
        (48, 168),
        f'{next_text}  ·  potrošnja {potrosnja.quantize(Decimal("0.01"))} KM',
        fill=soft,
        font=font_micro,
    )

    # Barkod — puni širinu, bez QR
    draw.rounded_rectangle([28, 198, 972, 400], radius=12, fill=panel, outline=panel_border, width=1)
    draw.text((48, 210), 'BARKOD', fill=muted, font=font_label)
    try:
        barcode_img = _barcode_image(barkod)
        max_w = 880
        ratio = max_w / max(barcode_img.width, 1)
        new_h = max(56, min(90, int(barcode_img.height * ratio * 0.85)))
        barcode_img = barcode_img.resize((max_w, new_h))
        pad_x, pad_y = 14, 12
        frame = Image.new('RGB', (max_w + pad_x * 2, new_h + pad_y * 2), (255, 255, 255))
        frame.paste(barcode_img, (pad_x, pad_y))
        img.paste(frame, (48, 240))
        draw = ImageDraw.Draw(img)
        # centriran tekst barkoda
        draw.text((48, 240 + new_h + pad_y * 2 + 8), barkod, fill=soft, font=font_label)
    except Exception:
        draw.text((48, 280), barkod, fill='white', font=font_small)

    # Nivoi u jednom redu
    draw.rounded_rectangle([28, 420, 972, 530], radius=12, fill=panel, outline=panel_border, width=1)
    draw.text((48, 432), 'NIVOI', fill=muted, font=font_label)
    x = 48
    for t in LOYALTY_TIERS:
        active = t['nivo'] == card.nivo
        label = f'{t["label"]} {t["postotak"]}%'
        tw = max(110, 8 * len(label) + 20)
        fill = green if active else (32, 32, 32)
        outline = green if active else (55, 55, 55)
        draw.rounded_rectangle([x, 458, x + tw, 498], radius=10, fill=fill, outline=outline, width=1)
        draw.text((x + 10, 470), label, fill='white' if active else soft, font=font_tier)
        x += tw + 10
    draw.text(
        (48, 510),
        'Unesi broj u korpi za popust · ne vrijedi na akcije · opremazaribolov.ba',
        fill=muted,
        font=font_micro,
    )

    return img


def generisi_loyalty_card_image(card, *, cardholder_name=None, fmt='JPEG'):
    """
    JPG/PNG zadnje strane kartice (tanka, s barkodom, bez QR).
    fmt: 'JPEG' (default za slanje) ili 'PNG'.
    """
    card = osiguraj_loyalty_karticu(card.user)
    name = (
        cardholder_name
        or card.user.get_full_name()
        or (card.user.email or '').strip().lower()
        or 'Kupac'
    ).strip()

    img = _draw_back_side(card, cardholder_name=name)
    buffer = io.BytesIO()
    fmt_u = (fmt or 'JPEG').upper()
    if fmt_u in ('JPG', 'JPEG'):
        # JPEG ne voli RGBA
        if img.mode != 'RGB':
            img = img.convert('RGB')
        img.save(buffer, format='JPEG', quality=90, optimize=True)
    else:
        img.save(buffer, format='PNG', optimize=True)
    return buffer.getvalue()
