import io
import re
import secrets
import time
from decimal import Decimal
from urllib.parse import quote

from django.contrib.auth.models import User
from django.db.models import Q, Sum

from .models import Coupon, LoyaltyCard, LoyaltyPurchase, Order, UserProfile

# Session key za pending OTP pri evidentiranju kupovine
LOYALTY_PURCHASE_OTP_SESSION_KEY = 'loyalty_purchase_otp'
LOYALTY_PURCHASE_OTP_TTL_SEC = 10 * 60  # 10 min
LOYALTY_PURCHASE_OTP_MAX_ATTEMPTS = 5


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

TIER_COLORS = {
    'bronza': ('#8B5E3C', '#C49A6C', '#5C3A21'),
    'srebrna': ('#6B7280', '#D1D5DB', '#374151'),
    'zlatna': ('#B45309', '#FBBF24', '#78350F'),
    'platinum': ('#1F2937', '#9CA3AF', '#0B1220'),
}


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


def search_loyalty_cards(query, *, limit=30):
    """
    Pretraga loyalty kupaca: kod, barkod, email, telefon, ime.
    Dijakritici: ž≈z, š≈s, č/ć≈c (u oba smjera).
    """
    q = (query or '').strip()
    if not q:
        return []

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

    filter_q = (
        Q(kod__icontains=q)
        | Q(barkod__icontains=q)
        | Q(user__email__icontains=q)
    )
    if name_q:
        filter_q |= name_q
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
    if len(results) < limit and fold_q and len(fold_q) >= 2:
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


def validiraj_ba_mobilni(telefon):
    """
    Validacija za izdavanje kartice.
    Vraća (local_06, e164) ili diže ValueError s porukom.
    """
    raw = (telefon or '').strip()
    if not raw:
        raise ValueError('Telefon je obavezan.')
    local = ba_mobile_local(raw)
    e164 = ba_mobile_e164(raw)
    if not local or not e164 or not local.startswith('06'):
        raise ValueError(
            'Unesite ispravan mobilni broj koji počinje sa 06 '
            '(npr. 061 123 456). Isti broj u formatu +387… ili 00387… '
            'također se prihvata i tretira kao isti.'
        )
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


def viber_chat_url(telefon, text=''):
    """
    Deep link Viber chata (BA brojevi).
    Napomena: Viber često ne podržava prefill teksta u 1:1 chatu —
    za poruku s kodom koristi whatsapp_chat_url (text= radi pouzdano).
    """
    digits = _to_e164_digits(telefon)
    if not digits:
        return ''
    # number bez + u nekim klijentima radi bolje s %2B
    return f'viber://chat?number=%2B{digits}'


def whatsapp_chat_url(telefon, text=''):
    """
    Deep link WhatsApp — poruka se unaprijed popuni (staff samo klikne Pošalji).
    Koristi api.whatsapp.com (pouzdanije od wa.me na desktopu).
    """
    digits = _to_e164_digits(telefon)
    if not digits:
        return ''
    # api.whatsapp.com/send bolje puni tekst na desktop + mobilnom
    url = f'https://api.whatsapp.com/send?phone={digits}'
    if text:
        url = f'{url}&text={quote(text)}'
    return url


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


def purchase_otp_message(code, *, iznos=None):
    """Tekst poruke za Viber/WhatsApp (samo kod + iznos)."""
    lines = [
        'opremazaribolov.ba — potvrda kupovine',
        f'Vaš kod: {code}',
    ]
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
):
    """Upiši kupovinu + ažuriraj potrošnju/nivo kartice."""
    try:
        iznos_d = Decimal(str(iznos)).quantize(Decimal('0.01'))
    except Exception as exc:
        raise ValueError('Neispravan iznos.') from exc
    if iznos_d <= 0:
        raise ValueError('Iznos mora biti veći od 0.')

    purchase = LoyaltyPurchase.objects.create(
        kartica=card,
        iznos=iznos_d,
        napomena=(napomena or '')[:200],
        verifikacija=verifikacija,
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
        raise ValueError(
            'Ovaj broj telefona je već registrovan na loyalty karticu — '
            'dupli telefon nije dozvoljen (uključujući +387 / 00387 format).'
        )
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


def _generisi_kod(user):
    suffix = secrets.token_hex(3).upper()
    return f'OZ{user.pk:05d}{suffix}'


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
        return preracunaj_potrosnju_kartice(card)
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


def loyalty_kontekst(card):
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

    # Kratki tekst uz sliku kartice (npr. Viber caption)
    if next_tier and preostalo is not None:
        next_line = (
            f'Još {preostalo.quantize(Decimal("0.01"))} KM do nivoa '
            f'{next_tier["label"]} ({next_tier["postotak"]}%)'
        )
    else:
        next_line = 'Najviši nivo — maksimalni popust'

    viber_caption = (
        f'Vaša loyalty kartica opremazaribolov.ba\n'
        f'Nivo: {tier["label"]} · Popust: {tier["postotak"]}%\n'
        f'{next_line}\n'
        f'Broj kartice: {card.kod}'
    )

    return {
        'kartica': card,
        'tier': tier,
        'next_tier': next_tier,
        'preostalo_do_sljedeceg': preostalo,
        'barkod_trake': _barkod_iz_koda(card.barkod or card.kod),
        'tiers': LOYALTY_TIERS,
        'telefon': telefon,
        'viber_url': viber_chat_url(telefon),
        'viber_caption': viber_caption,
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


def generisi_loyalty_card_image(card, *, cardholder_name=None):
    """Generiše PNG sliku loyalty kartice: nivo, % popusta, do sljedećeg nivoa."""
    from PIL import Image, ImageDraw

    card = osiguraj_loyalty_karticu(card.user)
    ctx = loyalty_kontekst(card)
    tier = ctx['tier']
    next_tier = ctx['next_tier']
    preostalo = ctx['preostalo_do_sljedeceg']

    bg_hex, accent_hex, dark_hex = TIER_COLORS.get(card.nivo, TIER_COLORS['bronza'])
    bg = _hex_to_rgb(bg_hex)
    accent = _hex_to_rgb(accent_hex)
    dark = _hex_to_rgb(dark_hex)
    name = (
        cardholder_name
        or card.user.get_full_name()
        or (card.user.email or '').strip().lower()
        or 'Kupac'
    ).strip()
    name_is_email = '@' in name
    name_display = (name.lower() if name_is_email else name.upper())[:40]
    kod = card.kod
    barkod = card.barkod or card.kod
    potrosnja = card.ukupna_potrosnja

    if next_tier and preostalo is not None:
        next_text = (
            f'Još {preostalo.quantize(Decimal("1"))} KM do {next_tier["label"]} '
            f'({next_tier["postotak"]}%)'
        )
    else:
        next_text = 'Najviši nivo — maksimalni popust'

    width, height = 980, 620
    img = Image.new('RGB', (width, height), bg)
    draw = ImageDraw.Draw(img)

    for y in range(height):
        ratio = y / max(height - 1, 1)
        color = tuple(int(bg[i] * (1 - ratio * 0.35) + dark[i] * (ratio * 0.35)) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)

    draw.rectangle([0, 0, width, 12], fill=accent)
    draw.rectangle([0, height - 12, width, height], fill=accent)

    font_brand = _load_font(22, bold=True)
    font_small = _load_font(16)
    font_name = _load_font(28 if name_is_email else 34, bold=True)
    font_code = _load_font(26, bold=True)
    font_label = _load_font(13)
    font_pct = _load_font(48, bold=True)
    font_tier = _load_font(20, bold=True)

    draw.text((40, 34), 'OZ  opremazaribolov.ba', fill='white', font=font_brand)
    draw.text((width - 220, 38), tier['label'].upper(), fill=accent, font=font_tier)

    draw.text((40, 88), 'VLASNIK KARTICE', fill=(230, 230, 230), font=font_label)
    draw.text((40, 110), name_display, fill='white', font=font_name)

    # Nivo + popust + do sljedećeg
    draw.rounded_rectangle([40, 170, 500, 300], radius=16, fill=(0, 0, 0))
    draw.text((64, 186), 'VAŠ NIVO', fill=(200, 200, 200), font=font_label)
    draw.text((64, 208), tier['label'].upper(), fill=accent, font=font_tier)
    draw.text((64, 242), f'{tier["postotak"]}%', fill='white', font=font_pct)
    draw.text((200, 268), 'POPUSTA', fill=(220, 220, 220), font=font_small)

    draw.rounded_rectangle([520, 170, 940, 300], radius=16, fill=(0, 0, 0))
    draw.text((544, 186), 'DO SLJEDEĆEG NIVOA', fill=(200, 200, 200), font=font_label)
    draw.text((544, 220), next_text, fill='white', font=font_small)
    draw.text(
        (544, 258),
        f'Potrošnja: {potrosnja.quantize(Decimal("0.01"))} KM',
        fill=accent,
        font=font_small,
    )

    # Broj kartice + barkod
    draw.rounded_rectangle([40, 320, 640, 580], radius=18, fill=(0, 0, 0))
    draw.text((64, 342), 'BROJ KARTICE', fill=(200, 200, 200), font=font_label)
    draw.text((64, 368), kod, fill='white', font=font_code)
    draw.text(
        (64, 408),
        f'{tier["label"]} · {tier["postotak"]}%  ·  LOYALTY',
        fill=accent,
        font=font_small,
    )

    try:
        barcode_img = _barcode_image(barkod)
        max_w = 530
        ratio = max_w / max(barcode_img.width, 1)
        new_h = max(48, int(barcode_img.height * ratio))
        barcode_img = barcode_img.resize((max_w, new_h))
        img.paste(barcode_img, (64, 450))
        draw = ImageDraw.Draw(img)
        draw.text((64, 520), barkod, fill=(210, 210, 210), font=font_label)
    except Exception:
        draw.text((64, 470), f'BARKOD: {barkod}', fill='white', font=font_small)

    # QR panel
    qr = _qr_image(kod, box_size=7, border=2).resize((190, 190))
    draw.rounded_rectangle([670, 320, 940, 580], radius=18, fill='white')
    img.paste(qr, (710, 360))
    draw = ImageDraw.Draw(img)
    draw.text((745, 332), 'QR KOD', fill='#111111', font=font_label)
    draw.text((715, 560), 'Skeniraj za kod', fill='#444444', font=font_label)

    buffer = io.BytesIO()
    img.save(buffer, format='PNG', optimize=True)
    return buffer.getvalue()
