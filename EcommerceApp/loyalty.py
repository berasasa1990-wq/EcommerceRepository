import secrets
from decimal import Decimal

from django.db.models import Sum

from .models import Coupon, LoyaltyCard, Order


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


def _generisi_kod(user):
    suffix = secrets.token_hex(3).upper()
    return f'OZ{user.pk:05d}{suffix}'


def _barkod_iz_koda(kod):
    bars = []
    for char in kod:
        bars.append((ord(char) % 4) + 1)
    return bars


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


def validiraj_kupon(kod, user):
    if not kod or not user or not user.is_authenticated:
        return None, 'Morate biti prijavljeni da koristite kupon.'

    coupon = Coupon.objects.filter(kod__iexact=kod.strip(), aktivan=True).select_related('vlasnik').first()
    if not coupon:
        return None, 'Kupon nije pronađen ili nije aktivan.'
    if coupon.vlasnik_id and coupon.vlasnik_id != user.pk:
        return None, 'Ovaj kupon možete koristiti samo vi kao vlasnik loyalty kartice.'
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

    return {
        'kartica': card,
        'tier': tier,
        'next_tier': next_tier,
        'preostalo_do_sljedeceg': preostalo,
        'barkod_trake': _barkod_iz_koda(card.barkod or card.kod),
        'tiers': LOYALTY_TIERS,
    }