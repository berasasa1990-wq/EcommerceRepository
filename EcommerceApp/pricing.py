from decimal import ROUND_HALF_UP, Decimal

from .cart import izracunaj_pdv
from .loyalty import validiraj_kupon
from .models import Order, SiteSettings


def _kvantiziraj(value):
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _postotni_popust(osnovica, postotak):
    return _kvantiziraj(osnovica * postotak / Decimal('100'))


def korisnik_ima_pogodnosti(user):
    if not user or not user.is_authenticated:
        return False
    return not Order.objects.filter(korisnik=user).exists()


def izracunaj_sazetak(medjuzbir, user=None, coupon_code=''):
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

    kupon = None
    kupon_popust = Decimal('0.00')
    if coupon_code:
        kupon, _ = validiraj_kupon(coupon_code, user)
        if kupon:
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