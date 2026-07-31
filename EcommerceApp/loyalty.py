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
    return re.sub(r'\D', '', telefon or '')


def normalizuj_email(email):
    return (email or '').strip().lower()


def _pronadji_korisnika_po_telefonu(telefon):
    digits = _normalizuj_telefon(telefon)
    if len(digits) < 8:
        return None
    for profil in UserProfile.objects.select_related('user').exclude(telefon=''):
        if _normalizuj_telefon(profil.telefon) == digits:
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
    """Normalizuj BA broj u cifre s pozivnim (387…), bez +."""
    digits = _normalizuj_telefon(telefon)
    if not digits:
        return ''
    if digits.startswith('00'):
        digits = digits[2:]
    # BA: 061… → 38761… ; 387… ostaje
    if digits.startswith('387'):
        pass
    elif digits.startswith('0') and len(digits) >= 8:
        digits = '387' + digits[1:]
    elif len(digits) in (8, 9) and not digits.startswith('387'):
        digits = '387' + digits.lstrip('0')
    if len(digits) < 10:
        return ''
    return digits


def viber_chat_url(telefon):
    """
    Deep link za otvaranje Viber chata s kupcem (BA brojevi).
    Koristi se u staff loyalty — otvara chat tačno na uneseni broj.
    """
    digits = _to_e164_digits(telefon)
    if not digits:
        return ''
    return f'viber://chat?number=%2B{digits}'


def whatsapp_chat_url(telefon, text=''):
    """
    Deep link WhatsApp chata (BA brojevi).
    text se prefill-uje u poruci (wa.me podržava text=).
    """
    digits = _to_e164_digits(telefon)
    if not digits:
        return ''
    url = f'https://wa.me/{digits}'
    if text:
        url = f'{url}?text={quote(text)}'
    return url


def _generate_otp_code():
    """4-cifreni kod (1000–9999)."""
    return f'{secrets.randbelow(9000) + 1000}'


def purchase_otp_message(code, *, iznos=None):
    """Tekst poruke za Viber/WhatsApp."""
    lines = [
        'opremazaribolov.ba — potvrda kupovine',
        f'Vaš kod: {code}',
    ]
    if iznos is not None:
        try:
            lines.append(f'Iznos: {Decimal(str(iznos)).quantize(Decimal("0.01"))} KM')
        except Exception:
            lines.append(f'Iznos: {iznos} KM')
    lines.append('Recite kod osoblju u prodavnici da se kupovina evidentira na loyalty karticu.')
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
        'viber_url': viber_chat_url(telefon),
        'whatsapp_url': whatsapp_chat_url(telefon, msg),
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
    card.ukupna_potrosnja = (card.ukupna_potrosnja or Decimal('0')) + iznos_d
    card.save(update_fields=['ukupna_potrosnja'])
    azuriraj_loyalty_karticu(card)
    return purchase


def izdaj_loyalty_karticu(ime, prezime, telefon, email=''):
    """
    Registruje kupca i izdaje loyalty karticu.
    Telefon je obavezan; email opcionalan.
    Nikad ne dozvoli dupli telefon ili (ako je unesen) dupli email.
    """
    ime = (ime or '').strip()
    prezime = (prezime or '').strip()
    telefon = (telefon or '').strip()
    email = normalizuj_email(email)

    if not ime or not prezime:
        raise ValueError('Ime i prezime su obavezni.')
    if not telefon or len(_normalizuj_telefon(telefon)) < 8:
        raise ValueError('Unesite ispravan broj telefona.')

    # Duplikati: zavisno šta se unosi
    if telefon_vec_registrovan(telefon):
        raise ValueError(
            'Ovaj broj telefona je već registrovan na loyalty karticu — '
            'dupli telefon nije dozvoljen.'
        )
    if email:
        if email_vec_registrovan(email):
            raise ValueError(
                'Ovaj email je već registrovan na loyalty karticu — '
                'dupli email nije dozvoljen.'
            )
        # username = email na nekim nalogima
        if User.objects.filter(username__iexact=email).exists():
            raise ValueError(
                'Ovaj email je već u upotrebi — dupli email nije dozvoljen.'
            )

    digits = _normalizuj_telefon(telefon) or secrets.token_hex(4)
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
    UserProfile.objects.create(user=user, telefon=telefon)

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


def ukupna_potrosnja_korisnika(user):
    if not user or not user.is_authenticated:
        return Decimal('0')
    total = (
        Order.objects.filter(korisnik=user)
        .exclude(status=Order.Status.OTKAZANA)
        .aggregate(total=Sum('ukupno'))['total']
    )
    return Decimal(total or 0)


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
        return azuriraj_loyalty_karticu(card)
    return kreiraj_loyalty_karticu(user)


def azuriraj_loyalty_nakon_narudzbe(order):
    if not order.korisnik_id:
        return
    kod = _generisi_kod(order.korisnik)
    card, _ = LoyaltyCard.objects.get_or_create(
        user=order.korisnik,
        defaults={
            'kod': kod,
            'barkod': kod,
            'nivo': 'bronza',
        },
    )
    if not card.barkod:
        card.barkod = card.kod
    card.ukupna_potrosnja = ukupna_potrosnja_korisnika(order.korisnik)
    card.save(update_fields=['ukupna_potrosnja', 'barkod'])
    azuriraj_loyalty_karticu(card)


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
