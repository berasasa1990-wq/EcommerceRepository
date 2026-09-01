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
    free_shipping_reward=False,
    prize_discount_percent=None,
    prize_discount_km=None,
    prize_free_shipping=False,
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

    # Uživo ponuda / registracija: besplatna dostava samo na prvu kupovinu
    if free_shipping_reward:
        if not user or not getattr(user, 'is_authenticated', False) or ima_novu_pogodnost:
            dostava = Decimal('0.00')
            if 'Besplatna dostava na prvu kupovinu' not in pogodnosti:
                pogodnosti.append('Besplatna dostava na prvu kupovinu')

    # Nagradni točak: besplatna dostava (uvijek, jednokratno)
    if prize_free_shipping:
        dostava = Decimal('0.00')
        if 'Nagradni točak: besplatna dostava' not in pogodnosti:
            pogodnosti.append('Nagradni točak: besplatna dostava')

    if ima_novu_pogodnost:
        if postavke.novi_korisnik_besplatna_dostava:
            dostava = Decimal('0.00')
            if 'Besplatna dostava za novog korisnika' not in pogodnosti:
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

    # Nagradni točak — jednokratni % ili KM popust na narudžbu/korpu
    prize_popust = Decimal('0.00')
    if prize_discount_percent:
        prize_percent = _kvantiziraj(prize_discount_percent)
        if prize_percent > 0:
            prize_popust = _postotni_popust(medjuzbir, prize_percent)
            popust += prize_popust
            pct_display = (
                int(prize_percent)
                if prize_percent == int(prize_percent)
                else prize_percent
            )
            pogodnosti.append(f'Nagradni točak: {pct_display}% na narudžbu')
    if prize_discount_km:
        prize_km = _kvantiziraj(prize_discount_km)
        if prize_km > 0:
            iznos = min(prize_km, medjuzbir)
            prize_popust = _kvantiziraj(prize_popust + iznos)
            popust += iznos
            pogodnosti.append(f'Nagradni točak: -{iznos} KM')

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
            if kupon.naziv == 'Registracijski popust (uživo)':
                pct = kupon.postotak
                pct_label = int(pct) if pct == int(pct) else pct
                pogodnosti.append(f'Registracijski popust {pct_label}% (jednokratno)')
            elif kupon.automatski or kupon.loyalty_kartica_id:
                pogodnosti.append(f'Loyalty kupon {kupon.postotak}% ({kupon.kod})')
            else:
                pogodnosti.append(f'Kupon {kupon.postotak}% ({kupon.kod})')

    popust = min(popust, ukupno_sa_pdvom)
    ukupno = _kvantiziraj(ukupno_sa_pdvom - popust)
    preostalo = _kvantiziraj(max(Decimal('0.00'), prag_besplatne - medjuzbir))
    if prag_besplatne > 0:
        napredak_besplatne = min(
            Decimal('100'),
            (medjuzbir / prag_besplatne * Decimal('100')).quantize(Decimal('1')),
        )
    else:
        napredak_besplatne = Decimal('100')
    pdv_artikli = izracunaj_pdv(medjuzbir)

    return {
        'medjuzbir': medjuzbir,
        'pdv_artikli': pdv_artikli,
        'popust': popust,
        'kupon_popust': kupon_popust,
        'recovery_popust': recovery_popust,
        'prize_popust': prize_popust,
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
        'napredak_besplatne_postotak': napredak_besplatne,
        'ukupno_prije_popusta': ukupno_sa_pdvom,
        'ukupno': ukupno,
        'pdv': izracunaj_pdv(ukupno),
        'kupon_kod': kupon.kod if kupon else '',
        'kupon_postotak': kupon.postotak if kupon else None,
    }


def _standardna_dostava(medjuzbir, postavke=None):
    """11 KM ispod praga, 0 KM preko 250 KM (ili postavke sajta)."""
    postavke = postavke or SiteSettings.load()
    cijena = _kvantiziraj(postavke.dostava_cijena)
    prag = _kvantiziraj(postavke.besplatna_dostava_od)
    goods = _kvantiziraj(medjuzbir)
    if prag > 0 and goods >= prag:
        return Decimal('0.00'), True, cijena, prag
    return cijena, False, cijena, prag


def _medjuzbir_za_racun(order):
    """Međuzbir za fakturu: pokupljena količina ako je picking smanjio stavku."""
    items = list(order.stavke.all())
    if any(item.kolicina_pokupljeno is not None for item in items):
        total = Decimal('0.00')
        for item in items:
            total += _kvantiziraj(item.cijena * item.kolicina_faktura)
        return total
    return _kvantiziraj(order.medjuzbir)


def order_paid_by_card(order):
    method = getattr(order, 'placeno_karticom', None)
    if callable(method):
        return method()
    return False


def order_waived_shipping(order):
    if order_paid_by_card(order):
        return True
    for row in (getattr(order, 'popust_detalji', None) or []):
        if not isinstance(row, dict):
            continue
        if row.get('bez_dostave'):
            return True
        if str(row.get('opis') or '').casefold().startswith('bez dostave'):
            return True
    return False


def sazetak_iz_narudzbe(order):
    from .models import Order

    postavke = SiteSettings.load()
    dostava = _kvantiziraj(order.dostava)
    popust = _kvantiziraj(order.popust)
    medjuzbir = _medjuzbir_za_racun(order)
    ukupno = _kvantiziraj(order.ukupno)
    goods = _kvantiziraj(medjuzbir - popust)
    std_dostava, std_free, dostava_cijena, prag = _standardna_dostava(goods, postavke)
    from .magacin import is_vp_order

    # Ručne Magacin narudžbe: na računu uvijek 11 KM / besplatno preko 250 KM
    # osim ako je dostava ručno skinuta. VP narudžbe nemaju dostavu.
    if (
        getattr(order, 'izvor', '') == Order.Izvor.MAGACIN
        and dostava == Decimal('0.00')
        and not std_free
        and not is_vp_order(order)
        and not order_waived_shipping(order)
    ):
        dostava = std_dostava
        ukupno = _kvantiziraj(goods + dostava)
    elif dostava > 0:
        std_free = False
    else:
        std_free = dostava == Decimal('0.00')
    if any(item.kolicina_pokupljeno is not None for item in order.stavke.all()):
        ukupno = _kvantiziraj(goods + dostava)
    ukupno_prije_popusta = _kvantiziraj(medjuzbir + dostava)
    pogodnosti = []
    if order_paid_by_card(order):
        pogodnosti.append('Plaćeno karticom')
    loyalty_info = None
    loyalty_fn = getattr(order, 'loyalty_popust_info', None)
    if callable(loyalty_fn):
        loyalty_info = loyalty_fn()
    popust_opis = (loyalty_info or {}).get('opis') or ''
    if not popust_opis and popust:
        popust_opis = 'Popust'
    return {
        'medjuzbir': medjuzbir,
        'pdv_artikli': izracunaj_pdv(medjuzbir),
        'popust': order.popust,
        'popust_opis': popust_opis,
        'loyalty_label': (loyalty_info or {}).get('label') or '',
        'loyalty_postotak': (loyalty_info or {}).get('postotak') or '',
        'kupon_popust': Decimal('0.00'),
        'ostali_popust': order.popust,
        'kupon_primijenjen': bool(order.kupon_kod),
        'kupon_postotak': (loyalty_info or {}).get('postotak') or None,
        'pogodnosti': pogodnosti,
        'ima_novu_pogodnost': False,
        'pogodnosti_dostupne_gostu': False,
        'dostava': dostava,
        'dostava_naziv': postavke.dostava_naziv,
        'dostava_cijena': dostava_cijena,
        'besplatna_dostava': std_free,
        'besplatna_dostava_od': prag,
        'preostalo_do_besplatne': Decimal('0.00'),
        'ukupno_prije_popusta': ukupno_prije_popusta,
        'ukupno': ukupno,
        'pdv': order.pdv_pregled,
        'kupon_kod': order.kupon_kod,
    }


def pripremi_stavke_za_racun(order):
    """Pripremi listu dictova za prikaz stavki na računu (email, staff, nalog).
    Osigurava da se za AKCIJA popust prikaže stvarni iznos (sniženo na 1 kom.)
    i da linijski ukupno bude tačan (popust samo na 1 komad).
    Uključuje izvor sniženja (AI dwell, akcija, deal…) za evidenciju.
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
        qty = oi.kolicina_faktura
        if qty <= 0:
            continue
        naziv = oi.naziv or ''
        is_akcija = 'popust iz akcije' in naziv.lower()
        is_deal = bool(deal_pattern.search(naziv))
        orig = oi.cijena
        disc = None
        disc_qty = 0
        deal_pct = None
        deal_vrsta = None
        charged = (orig * qty).quantize(Decimal('0.01'))

        deal_match = deal_pattern.search(naziv)
        if deal_match:
            try:
                deal_vrsta = deal_match.group(1)
                disc_qty = int(deal_match.group(2))
                deal_pct = deal_match.group(3)
                disc = Decimal(deal_match.group(4).replace(',', '.')).quantize(Decimal('0.01'))
                full_qty = max(0, qty - disc_qty)
                charged = (orig * full_qty + disc * disc_qty).quantize(Decimal('0.01'))
            except (ValueError, ArithmeticError):
                disc = None
                disc_qty = 0
                charged = (orig * qty).quantize(Decimal('0.01'))
        elif is_akcija:
            m = akcija_pattern.search(naziv)
            if m:
                try:
                    disc = Decimal(m.group(1).replace(',', '.')).quantize(Decimal('0.01'))
                    charged = (orig * max(0, qty - 1) + disc).quantize(Decimal('0.01'))
                    disc_qty = 1
                except (ValueError, ArithmeticError):
                    disc = None
                    charged = (orig * qty).quantize(Decimal('0.01'))

        display_naziv = re.sub(
            r'\s*\([^)]*(?:\d+\+\d+|popust iz akcije)[^)]*\)\s*$',
            '',
            oi.product_naziv or naziv or '',
        ).strip()

        bazna = getattr(oi, 'bazna_cijena', None)
        if bazna is None:
            bazna = orig
        popust_opis = (getattr(oi, 'popust_opis', None) or '').strip()
        popust_postotak = getattr(oi, 'popust_postotak', None)
        popust_iznos = getattr(oi, 'popust_iznos', None)
        if popust_iznos is None and bazna is not None and bazna > orig:
            popust_iznos = ((bazna - orig) * qty).quantize(Decimal('0.01'))
        # Legacy: izvuci izvor iz napomene u nazivu
        if not popust_opis:
            if is_deal and deal_vrsta:
                popust_opis = f'Deal {deal_vrsta}' + (f' (−{deal_pct}%)' if deal_pct else '')
            elif is_akcija:
                popust_opis = 'Uslov / akcija prodaja'
            elif bazna is not None and bazna > orig:
                popust_opis = 'Snižena cijena'

        ima_snizenje = bool(
            popust_opis
            or (popust_iznos and popust_iznos > 0)
            or (bazna is not None and bazna > orig)
            or is_akcija
            or is_deal
        )

        stavke.append({
            'naziv': naziv,
            'product_naziv': display_naziv or oi.product_naziv or oi.naziv or '',
            'varijacija_naziv': oi.varijacija_naziv,
            'sifra': oi.sifra,
            'kolicina': qty,
            'cijena': orig,
            'bazna_cijena': bazna,
            'ukupno': charged,
            'is_akcija_promo': is_akcija,
            'is_deal_promo': is_deal,
            'discounted_unit_price': disc,
            'discounted_qty': disc_qty,
            'deal_pct': deal_pct,
            'deal_vrsta': deal_vrsta,
            'popust_opis': popust_opis,
            'popust_postotak': popust_postotak,
            'popust_iznos': popust_iznos,
            'ima_snizenje': ima_snizenje,
        })
    return stavke