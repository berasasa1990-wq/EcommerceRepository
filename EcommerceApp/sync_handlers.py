import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import transaction

from .loyalty import sync_loyalty_coupon, tier_info
from .models import Coupon, LoyaltyCard, Order, OrderItem, Product, ProductVariation, UserProfile


def _decimal(value, default='0'):
    return Decimal(str(value or default))


def serialize_korisnik(user):
    profil = getattr(user, 'profil', None)
    card = getattr(user, 'loyalty_kartica', None)
    payload = {
        'email': user.email.strip().lower(),
        'ime_prezime': user.get_full_name() or user.first_name or '',
        'telefon': profil.telefon if profil else '',
        'adresa': profil.adresa if profil else '',
        'grad': profil.grad if profil else '',
        'postanski_broj': profil.postanski_broj if profil else '',
        'password_hash': user.password,
        'loyalty': None,
    }
    if card:
        tier = tier_info(card.nivo)
        payload['loyalty'] = {
            'kod': card.kod,
            'barkod': card.barkod or card.kod,
            'nivo': card.nivo,
            'ukupna_potrosnja': str(card.ukupna_potrosnja),
            'postotak': str(tier['postotak']),
        }
        payload['loyalty_postotak'] = str(tier['postotak'])
    return payload


def serialize_narudzba(order):
    payload = {
        'broj': order.broj,
        'email': order.email.strip().lower(),
        'ime_prezime': order.ime_prezime,
        'telefon': order.telefon,
        'adresa': order.adresa,
        'grad': order.grad,
        'postanski_broj': order.postanski_broj,
        'napomena': order.napomena,
        'medjuzbir': str(order.medjuzbir),
        'dostava': str(order.dostava),
        'popust': str(order.popust),
        'kupon_kod': order.kupon_kod,
        'ukupno': str(order.ukupno),
        'status': order.status,
        'kreirana': order.kreirana.isoformat(),
        'stavke': [
            {
                'naziv': item.naziv,
                'product_naziv': item.product_naziv,
                'varijacija_naziv': item.varijacija_naziv,
                'sifra': item.sifra,
                'cijena': str(item.cijena),
                'kolicina': item.kolicina,
                'product_slug': item.artikal.slug if item.artikal_id else '',
                'variation_id': item.varijacija_id,
            }
            for item in order.stavke.all()
        ],
    }
    # Pokušaj attach-ovati loyalty podatke (i za guest narudžbe po emailu)
    # jer remote loyalty pretraga očekuje loyalty kod uz narudžbu
    if 'loyalty' not in payload:
        user = None
        if order.korisnik_id:
            user = order.korisnik
        else:
            user = User.objects.filter(email__iexact=order.email).first()
        if user:
            card = getattr(user, 'loyalty_kartica', None)
            if card:
                tier = tier_info(card.nivo)
                payload['loyalty'] = {
                    'kod': card.kod,
                    'barkod': card.barkod or card.kod,
                    'nivo': card.nivo,
                    'ukupna_potrosnja': str(card.ukupna_potrosnja),
                    'postotak': str(tier['postotak']),
                }
                payload['loyalty_postotak'] = str(tier['postotak'])
    return payload


@transaction.atomic
def upsert_korisnik(payload):
    email = payload['email'].strip().lower()
    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        user = User.objects.filter(username__iexact=email).first()
    if user is None:
        user = User(username=email, email=email)
    user.username = email
    user.email = email
    user.first_name = payload.get('ime_prezime', user.first_name)
    if payload.get('password_hash'):
        user.password = payload['password_hash']
    elif not user.pk:
        user.set_unusable_password()
    user.save()

    UserProfile.objects.update_or_create(
        user=user,
        defaults={
            'telefon': payload.get('telefon', ''),
            'adresa': payload.get('adresa', ''),
            'grad': payload.get('grad', ''),
            'postanski_broj': payload.get('postanski_broj', ''),
        },
    )

    # Poveži narudžbe koje su stigle ranije (kao gost ili prije sync korisnika)
    # tako da se na loyalty strani vide pod korisnikom / loyalty karticom
    Order.objects.filter(
        email__iexact=email,
        korisnik__isnull=True,
    ).update(korisnik=user)

    loyalty = payload.get('loyalty')
    if not loyalty:
        return {'ok': True, 'email': email, 'loyalty': False}

    kod = loyalty['kod']
    card = LoyaltyCard.objects.filter(kod=kod).select_related('user').first()
    if card and card.user_id != user.pk:
        card = None
    if card is None:
        card = getattr(user, 'loyalty_kartica', None)
    if card is None:
        card = LoyaltyCard(user=user)

    card.kod = kod
    card.barkod = loyalty.get('barkod') or kod
    card.nivo = loyalty.get('nivo', LoyaltyCard.Nivo.BRONZA)
    card.ukupna_potrosnja = _decimal(loyalty.get('ukupna_potrosnja'))
    card.save()
    sync_loyalty_coupon(card)

    return {
        'ok': True,
        'email': email,
        'loyalty': True,
        'kod': card.kod,
        'nivo': card.nivo,
        'postotak': str(tier_info(card.nivo)['postotak']),
    }


@transaction.atomic
def upsert_narudzba(payload):
    email = payload['email'].strip().lower()
    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        user = User.objects.filter(username__iexact=email).first()

    order, created = Order.objects.get_or_create(
        broj=payload['broj'],
        defaults={
            'korisnik': user,
            'ime_prezime': payload['ime_prezime'],
            'email': email,
            'telefon': payload['telefon'],
            'adresa': payload['adresa'],
            'grad': payload['grad'],
            'postanski_broj': payload.get('postanski_broj', ''),
            'napomena': payload.get('napomena', ''),
            'medjuzbir': _decimal(payload.get('medjuzbir')),
            'dostava': _decimal(payload.get('dostava')),
            'popust': _decimal(payload.get('popust')),
            'kupon_kod': payload.get('kupon_kod', ''),
            'ukupno': _decimal(payload.get('ukupno')),
            'status': payload.get('status', Order.Status.NOVA),
        },
    )
    if not created:
        order.korisnik = user
        order.ime_prezime = payload['ime_prezime']
        order.email = email
        order.telefon = payload['telefon']
        order.adresa = payload['adresa']
        order.grad = payload['grad']
        order.postanski_broj = payload.get('postanski_broj', '')
        order.napomena = payload.get('napomena', '')
        order.medjuzbir = _decimal(payload.get('medjuzbir'))
        order.dostava = _decimal(payload.get('dostava'))
        order.popust = _decimal(payload.get('popust'))
        order.kupon_kod = payload.get('kupon_kod', '')
        order.ukupno = _decimal(payload.get('ukupno'))
        order.status = payload.get('status', order.status)
        order.save()

    order.stavke.all().delete()
    for item in payload.get('stavke', []):
        product = None
        variation = None
        slug = item.get('product_slug')
        if slug:
            product = Product.objects.filter(slug=slug).first()
        variation_id = item.get('variation_id')
        if variation_id and product:
            variation = ProductVariation.objects.filter(pk=variation_id, artikal=product).first()

        OrderItem.objects.create(
            narudzba=order,
            artikal=product,
            varijacija=variation,
            naziv=item.get('naziv', ''),
            product_naziv=item.get('product_naziv', ''),
            varijacija_naziv=item.get('varijacija_naziv', ''),
            sifra=item.get('sifra', ''),
            cijena=_decimal(item.get('cijena')),
            kolicina=item.get('kolicina', 1),
        )

    if user:
        card = getattr(user, 'loyalty_kartica', None)
        if card:
            from .loyalty import ukupna_potrosnja_korisnika, azuriraj_loyalty_karticu
            card.ukupna_potrosnja = ukupna_potrosnja_korisnika(user)
            card.save(update_fields=['ukupna_potrosnja'])
            azuriraj_loyalty_karticu(card)

    return {'ok': True, 'broj': order.broj, 'created': created}


def parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None