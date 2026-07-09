import io
import re
import secrets
from decimal import Decimal

from django.contrib.auth.models import User
from django.db.models import Q, Sum

from .models import Coupon, LoyaltyCard, Order, UserProfile


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


def viber_chat_url(telefon):
    """Deep link za otvaranje Viber chata s kupcem (BA brojevi)."""
    digits = _normalizuj_telefon(telefon)
    if not digits:
        return ''
    if digits.startswith('00'):
        digits = digits[2:]
    if digits.startswith('0') and len(digits) >= 8:
        digits = '387' + digits[1:]
    elif len(digits) in (8, 9) and not digits.startswith('387'):
        digits = '387' + digits.lstrip('0')
    return f'viber://chat?number=%2B{digits}'


def izdaj_loyalty_karticu(ime, prezime, telefon, email):
    """Registruje kupca i izdaje loyalty karticu. Telefon i email moraju biti jedinstveni."""
    ime = (ime or '').strip()
    prezime = (prezime or '').strip()
    telefon = (telefon or '').strip()
    email = normalizuj_email(email)

    if not ime or not prezime:
        raise ValueError('Ime i prezime su obavezni.')
    if not telefon or len(_normalizuj_telefon(telefon)) < 8:
        raise ValueError('Unesite ispravan broj telefona.')
    if not email:
        raise ValueError('Email je obavezan.')

    if telefon_vec_registrovan(telefon):
        raise ValueError('Ovaj broj telefona je već registrovan na loyalty karticu.')
    if email_vec_registrovan(email):
        raise ValueError('Ovaj email je već registrovan na loyalty karticu.')

    digits = _normalizuj_telefon(telefon) or secrets.token_hex(4)
    username = f'loy_{digits}'
    while User.objects.filter(username=username).exists():
        username = f'loy_{digits}_{secrets.token_hex(2)}'

    user = User.objects.create_user(
        username=username,
        email=email,
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

    return {
        'kartica': card,
        'tier': tier,
        'next_tier': next_tier,
        'preostalo_do_sljedeceg': preostalo,
        'barkod_trake': _barkod_iz_koda(card.barkod or card.kod),
        'tiers': LOYALTY_TIERS,
        'telefon': telefon,
        'viber_url': viber_chat_url(telefon),
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
    """Generiše PNG sliku loyalty kartice s QR kodom i barkodom."""
    from PIL import Image, ImageDraw

    card = osiguraj_loyalty_karticu(card.user)
    tier = tier_info(card.nivo)
    bg_hex, accent_hex, dark_hex = TIER_COLORS.get(card.nivo, TIER_COLORS['bronza'])
    bg = _hex_to_rgb(bg_hex)
    accent = _hex_to_rgb(accent_hex)
    dark = _hex_to_rgb(dark_hex)
    name = (cardholder_name or card.user.get_full_name() or card.user.email or 'Kupac').strip()
    kod = card.kod
    barkod = card.barkod or card.kod

    width, height = 900, 560
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
    font_name = _load_font(34, bold=True)
    font_code = _load_font(26, bold=True)
    font_label = _load_font(13)

    draw.text((40, 34), 'OZ  opremazaribolov.ba', fill='white', font=font_brand)
    draw.text((width - 210, 38), tier['label'].upper(), fill=accent, font=font_small)
    draw.rounded_rectangle([40, 90, 118, 142], radius=8, fill=accent)

    draw.text((40, 168), 'VLASNIK KARTICE', fill=(230, 230, 230), font=font_label)
    draw.text((40, 190), name.upper()[:34], fill='white', font=font_name)

    # Left panel with code + barcode
    draw.rounded_rectangle([40, 260, 560, 520], radius=18, fill=(0, 0, 0))
    draw.text((64, 282), 'BROJ KARTICE / ONLINE KOD', fill=(200, 200, 200), font=font_label)
    draw.text((64, 308), kod, fill='white', font=font_code)
    draw.text(
        (64, 350),
        f'{tier["postotak"]}% POPUSTA  ·  LOYALTY PROGRAM',
        fill=accent,
        font=font_small,
    )

    try:
        barcode_img = _barcode_image(barkod)
        max_w = 460
        ratio = max_w / max(barcode_img.width, 1)
        new_h = max(48, int(barcode_img.height * ratio))
        barcode_img = barcode_img.resize((max_w, new_h))
        img.paste(barcode_img, (64, 390))
        draw = ImageDraw.Draw(img)
    except Exception:
        draw.text((64, 420), f'BARKOD: {barkod}', fill='white', font=font_small)

    # QR panel
    qr = _qr_image(kod, box_size=7, border=2).resize((190, 190))
    draw.rounded_rectangle([600, 260, 860, 520], radius=18, fill='white')
    img.paste(qr, (635, 300))
    draw = ImageDraw.Draw(img)
    draw.text((670, 272), 'QR KOD', fill='#111111', font=font_label)
    draw.text((640, 500), 'Skeniraj za kod', fill='#444444', font=font_label)

    buffer = io.BytesIO()
    img.save(buffer, format='PNG', optimize=True)
    return buffer.getvalue()
