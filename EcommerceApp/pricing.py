from decimal import ROUND_HALF_UP, Decimal

from .cart import izracunaj_pdv
from .loyalty import validiraj_kupon
from .models import Order, SiteSettings


def _kvantiziraj(value):
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _postotni_popust(osnovica, postotak):
    return _kvantiziraj(osnovica * postotak / Decimal('100'))


def _stavka_snizena_za_loyalty(item):
    """Loyalty popust ne vrijedi na snižene artikle ni na stavke sa % umanjenjem."""
    cijena = item.get('cijena_decimal')
    if cijena is None:
        cijena = Decimal(str(item.get('cijena', '0')))
    bazna = item.get('bazna_cijena_decimal')
    if bazna is None:
        bazna = Decimal(str(item.get('bazna_cijena', '0')))

    if item.get('na_akciji') or item.get('upsell') or item.get('timer_akcija'):
        return True
    return cijena < bazna


def _loyalty_osnovica_iz_korpe(cart_items):
    """Zbroj stavki po punoj cijeni — isključuje akcije, upsell i deal sniženja."""
    if not cart_items:
        return Decimal('0.00')

    eligible = Decimal('0.00')
    for item in cart_items:
        cijena = item.get('cijena_decimal')
        if cijena is None:
            cijena = Decimal(str(item.get('cijena', '0')))
        qty = int(item.get('quantity', 0))
        if qty <= 0 or _stavka_snizena_za_loyalty(item):
            continue

        deal_info = item.get('deal_info')
        if deal_info and deal_info.get('has_discount'):
            full_count = int(deal_info.get('full_price_count', qty))
            eligible += _kvantiziraj(cijena * full_count)
            continue

        if item.get('discounted_unit_price') is not None:
            if qty <= 1:
                continue
            eligible += _kvantiziraj(cijena * (qty - 1))
            continue

        eligible += _kvantiziraj(cijena * qty)

    return eligible


def korisnik_ima_pogodnosti(user):
    if not user or not user.is_authenticated:
        return False
    return not Order.objects.filter(korisnik=user).exists()


def izracunaj_sazetak(
    medjuzbir,
    user=None,
    coupon_code='',
    cart_items=None,
    recovery_discount_percent=None,
):
    postavke = SiteSettings.load()
    medjuzbir = _kvantiziraj(medjuzbir)

    dostava_cijena = _kvantiziraj(postavke.dostava_cijena)
    prag_besplatne = _kvantiziraj(postavke.besplatna_dostava_od)
    dostava = dostava_cijena
    popust = Decimal('0.00')
    pogodnosti = []
    ima_novu_pogodnost = korisnik_ima_pogodnosti(user)

    if medjuzbir >= prag_besplatne:
        dostava = Decimal('0.00')

    if ima_novu_pogodnost:
        if postavke.novi_korisnik_besplatna_dostava:
            dostava = Decimal('0.00')
            pogodnosti.append('Besplatna dostava za novog korisnika')

    # Cijene artikala i dostave su maloprodajne (sa PDV-om).
    ukupno_sa_pdvom = _kvantiziraj(medjuzbir + dostava)

    if ima_novu_pogodnost:
        if postavke.novi_korisnik_popust_postotak:
            postotak = _kvantiziraj(postavke.novi_korisnik_popust_postotak)
            iznos = _postotni_popust(ukupno_sa_pdvom, postotak)
            popust += iznos
            pogodnosti.append(f'Popust {postotak}% za novog korisnika')
        if postavke.novi_korisnik_popust_km:
            iznos = _kvantiziraj(postavke.novi_korisnik_popust_km)
            popust += iznos
            pogodnosti.append(f'Popust {iznos} KM za novog korisnika')

    recovery_popust = Decimal('0.00')
    if recovery_discount_percent:
        recovery_percent = _kvantiziraj(recovery_discount_percent)
        if recovery_percent > 0:
            recovery_popust = _postotni_popust(medjuzbir, recovery_percent)
            popust += recovery_popust
            pct_display = (
                int(recovery_percent)
                if recovery_percent == int(recovery_percent)
                else recovery_percent
            )
            pogodnosti.append(f'Poseban popust {pct_display}% na korpu')

    kupon = None
    kupon_popust = Decimal('0.00')
    if coupon_code:
        kupon, _ = validiraj_kupon(coupon_code, user)
        if kupon:
            if kupon.automatski:
                loyalty_osnovica = _loyalty_osnovica_iz_korpe(cart_items)
                kupon_popust = _postotni_popust(loyalty_osnovica, kupon.postotak)
            else:
                kupon_popust = _postotni_popust(ukupno_sa_pdvom, kupon.postotak)
            popust += kupon_popust
            pogodnosti.append(f'Loyalty kupon {kupon.postotak}% ({kupon.kod})')

    popust = min(popust, ukupno_sa_pdvom)
    ukupno = _kvantiziraj(ukupno_sa_pdvom - popust)
    preostalo = _kvantiziraj(max(Decimal('0.00'), prag_besplatne - medjuzbir))
    pdv_artikli = izracunaj_pdv(medjuzbir)

    return {
        'medjuzbir': medjuzbir,
        'pdv_artikli': pdv_artikli,
        'popust': popust,
        'kupon_popust': kupon_popust,
        'recovery_popust': recovery_popust,
        'ostali_popust': _kvantiziraj(popust - kupon_popust),
        'kupon_primijenjen': bool(kupon),
        'pogodnosti': pogodnosti,
        'ima_novu_pogodnost': ima_novu_pogodnost,
        'pogodnosti_dostupne_gostu': bool(
            postavke.novi_korisnik_besplatna_dostava
            or postavke.novi_korisnik_popust_postotak
            or postavke.novi_korisnik_popust_km
        ),
        'dostava': dostava,
        'dostava_naziv': postavke.dostava_naziv,
        'besplatna_dostava': dostava == Decimal('0.00'),
        'besplatna_dostava_od': prag_besplatne,
        'preostalo_do_besplatne': preostalo,
        'ukupno_prije_popusta': ukupno_sa_pdvom,
        'ukupno': ukupno,
        'pdv': izracunaj_pdv(ukupno),
        'kupon_kod': kupon.kod if kupon else '',
        'kupon_postotak': kupon.postotak if kupon else None,
    }


def sazetak_iz_narudzbe(order):
    postavke = SiteSettings.load()
    ukupno_prije_popusta = _kvantiziraj(order.medjuzbir + order.dostava)
    return {
        'medjuzbir': order.medjuzbir,
        'pdv_artikli': izracunaj_pdv(order.medjuzbir),
        'popust': order.popust,
        'kupon_popust': Decimal('0.00'),
        'ostali_popust': order.popust,
        'kupon_primijenjen': bool(order.kupon_kod),
        'kupon_postotak': None,
        'pogodnosti': [],
        'ima_novu_pogodnost': False,
        'pogodnosti_dostupne_gostu': False,
        'dostava': order.dostava,
        'dostava_naziv': postavke.dostava_naziv,
        'besplatna_dostava': order.dostava == Decimal('0.00'),
        'besplatna_dostava_od': postavke.besplatna_dostava_od,
        'preostalo_do_besplatne': Decimal('0.00'),
        'ukupno_prije_popusta': ukupno_prije_popusta,
        'ukupno': order.ukupno,
        'pdv': order.pdv_pregled,
        'kupon_kod': order.kupon_kod,
    }


def pripremi_stavke_za_racun(order):
    """Pripremi listu dictova za prikaz stavki na računu (email, staff, nalog).
    Osigurava da se za AKCIJA popust prikaže stvarni iznos (sniženo na 1 kom.)
    i da linijski ukupno bude tačan (popust samo na 1 komad).
    """
    from decimal import Decimal
    import re

    deal_pattern = re.compile(
        r'\((\d+\+\d+):\s*(\d+)\s*kom\.\s*sniženo za ([\d.]+)%\s*-\s*sniženo na ([\d.,]+)\s*KM\)',
        re.I,
    )
    akcija_pattern = re.compile(r'sniženo na ([\d.,]+)\s*KM', re.I)

    stavke = []
    for oi in order.stavke.all():
        naziv = oi.naziv or ''
        is_akcija = 'popust iz akcije' in naziv.lower()
        is_deal = bool(deal_pattern.search(naziv))
        orig = oi.cijena
        disc = None
        disc_qty = 0
        deal_pct = None
        deal_vrsta = None
        charged = (orig * oi.kolicina).quantize(Decimal('0.01'))

        deal_match = deal_pattern.search(naziv)
        if deal_match:
            try:
                deal_vrsta = deal_match.group(1)
                disc_qty = int(deal_match.group(2))
                deal_pct = deal_match.group(3)
                disc = Decimal(deal_match.group(4).replace(',', '.')).quantize(Decimal('0.01'))
                full_qty = max(0, oi.kolicina - disc_qty)
                charged = (orig * full_qty + disc * disc_qty).quantize(Decimal('0.01'))
            except (ValueError, ArithmeticError):
                disc = None
                disc_qty = 0
                charged = (orig * oi.kolicina).quantize(Decimal('0.01'))
        elif is_akcija:
            m = akcija_pattern.search(naziv)
            if m:
                try:
                    disc = Decimal(m.group(1).replace(',', '.')).quantize(Decimal('0.01'))
                    charged = (orig * (oi.kolicina - 1) + disc).quantize(Decimal('0.01'))
                    disc_qty = 1
                except (ValueError, ArithmeticError):
                    disc = None
                    charged = (orig * oi.kolicina).quantize(Decimal('0.01'))

        display_naziv = re.sub(
            r'\s*\([^)]*(?:\d+\+\d+|popust iz akcije)[^)]*\)\s*$',
            '',
            oi.product_naziv or naziv or '',
        ).strip()

        stavke.append({
            'naziv': naziv,
            'product_naziv': display_naziv or oi.product_naziv or oi.naziv or '',
            'varijacija_naziv': oi.varijacija_naziv,
            'sifra': oi.sifra,
            'kolicina': oi.kolicina,
            'cijena': orig,
            'ukupno': charged,
            'is_akcija_promo': is_akcija,
            'is_deal_promo': is_deal,
            'discounted_unit_price': disc,
            'discounted_qty': disc_qty,
            'deal_pct': deal_pct,
            'deal_vrsta': deal_vrsta,
        })
    return stavke