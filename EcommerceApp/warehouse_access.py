"""Warehouse-only entitlements. Never grant Django staff/superuser privileges."""
from django.utils import timezone

OWNER_EMAIL = 'sasabera1990@gmail.com'
FEATURES = {
    'artikli': 'Artikli', 'narudzbe': 'Narudžbe', 'picking': 'Picking',
    'stampa_cijena': 'Štampaj cijenu', 'stampa_deklaracije': 'Štampaj deklaracije',
    'pregled': 'Pregled', 'brzi_unos': 'Brzi unos / Aktivacija', 'lokacije': 'Lokacije',
    'zalihe': 'Zalihe', 'rezervni': 'Rezervni dijelovi', 'mp_dnevno': 'Dnevno skidanje MP lagera',
    'transferi': 'Transferi', 'popis': 'Popis robe', 'provjera_lagera': 'Provjera lagera',
    'fali_na_sajtu': 'Fali u MP-u', 'uvoz': 'Uvoz', 'nivelacije': 'Nivelacije',
    'kupci': 'Kupci', 'ponude': 'Kreiraj ponudu', 'izvjestaji': 'Izvještaji',
    'podesavanja': 'Podešavanja', 'sync': 'Sinhronizacija', 'backup': 'Backup / Restore',
    'dobavljaci': 'Dobavljači', 'duguje': 'Duguje / Potražuje',
}
DEFAULT_FEATURES = {
    'basic': ['artikli', 'narudzbe', 'picking'],
    'premium': ['artikli', 'narudzbe', 'picking', 'stampa_cijena', 'stampa_deklaracije'],
    'ultimate': list(FEATURES),
}


def is_subscription_owner(user):
    if not user.is_authenticated or not user.is_active or user.email.strip().lower() != OWNER_EMAIL:
        return False
    if not hasattr(user, '_warehouse_owner'):
        from .models import MagacinSubscriptionOwner
        # Bind to an existing account, not an email someone can set on their profile.
        user._warehouse_owner = MagacinSubscriptionOwner.objects.filter(pk=1, user_id=user.pk).exists()
    return user._warehouse_owner


def subscription_for(user):
    if not user.is_authenticated:
        return None
    if not hasattr(user, '_warehouse_subscription'):
        from .models import MagacinSubscription
        user._warehouse_subscription = MagacinSubscription.objects.select_related('plan').filter(user_id=user.pk).first()
    return user._warehouse_subscription


def allowed_features(user):
    if not user.is_authenticated or not user.is_active:
        return set()
    if is_subscription_owner(user):
        return set(FEATURES)
    sub = subscription_for(user)
    if sub is None:
        # Also cover superusers created through bulk operations (no save signal).
        if not user.is_superuser:
            return set()
        from .models import MagacinPlan
        basic = MagacinPlan.objects.filter(code='basic').first()
        return (set(basic.features) & FEATURES.keys()) if basic else set()
    if not sub.active or (sub.expires_on and sub.expires_on < timezone.localdate()):
        return set()
    if sub.plan.code == 'ultimate':
        return set(FEATURES)
    return set(sub.plan.features) & FEATURES.keys()


def warehouse_user_required(user):
    return bool(allowed_features(user))


def route_features(name):
    """Return alternatives for shared helpers; empty means a non-warehouse route."""
    if name == 'staff_magacin_pretplate':
        return {'__owner__'}
    if name.startswith('staff_order_'):
        return {'picking'} if name == 'staff_order_packing' else {'narudzbe'}
    if not name.startswith('staff_magacin'):
        return set()
    suffix = name.removeprefix('staff_magacin').lstrip('_')
    shared = {
        '': set(FEATURES), 'artikli_lookup': {'artikli', 'narudzbe', 'picking', 'stampa_cijena', 'stampa_deklaracije', 'ponude', 'popis', 'duguje'},
        'lokacije_lookup': {'artikli', 'narudzbe', 'picking', 'lokacije', 'popis', 'transferi'},
        'kupci_lookup': {'narudzbe', 'kupci', 'ponude'}, 'kupci_save': {'narudzbe', 'kupci', 'ponude'},
        'loyalty_telefon': {'narudzbe'},
        'brzi_unos_novi': {'artikli', 'brzi_unos'},
        'artikal_stampa': {'stampa_cijena'}, 'artikal_stampa_barkod': {'stampa_cijena'},
        'istorija': {'artikli'}, 'narudzba_barkod': {'narudzbe', 'picking'},
        'pakovanje': {'picking'}, 'vp_narudzba': {'narudzbe'},
    }
    if suffix in shared:
        return shared[suffix]
    # Specific names precede broader feature families. Unknown routes fail closed.
    families = [
        ('stampa_deklaracije', 'stampa_deklaracije'), ('stampa_cijena', 'stampa_cijena'),
        ('artikal', 'artikli'), ('artikli', 'artikli'), ('brzi_unos', 'brzi_unos'),
        ('pakuj', 'picking'), ('narudzb', 'narudzbe'), ('brza_posta', 'narudzbe'),
        ('xexpress', 'narudzbe'), ('lokacij', 'lokacije'), ('rezervni', 'rezervni'),
        ('popis', 'popis'), ('ponud', 'ponude'),
    ]
    families += [(key, key) for key in FEATURES]
    for prefix, feature in families:
        if suffix.startswith(prefix):
            return {feature}
    return {'__unknown__'}


def can_access_route(user, name):
    required = route_features(name)
    if '__owner__' in required:
        return is_subscription_owner(user)
    return bool(required & allowed_features(user))


def warehouse_landing(user):
    features = allowed_features(user)
    route_suffixes = {'picking': 'pakuj', 'rezervni': 'rezervni_dijelovi', 'popis': 'popis_test', 'sync': 'sync_istorija'}
    for feature in FEATURES:
        if feature in features:
            return 'staff_magacin_' + route_suffixes.get(feature, feature)
    return 'magacin_planovi'


def assign_default_superuser_plan(sender, instance, raw=False, using='default', **kwargs):
    """Assign Basic on creation/promotion; never replace an explicit subscription."""
    if raw or not instance.is_superuser:
        return
    from .models import MagacinPlan, MagacinSubscription, MagacinSubscriptionOwner
    if MagacinSubscriptionOwner.objects.using(using).filter(pk=1, user_id=instance.pk).exists():
        return
    basic = MagacinPlan.objects.using(using).filter(code='basic').first()
    if basic:
        MagacinSubscription.objects.using(using).get_or_create(user_id=instance.pk, defaults={'plan_id': basic.pk})
    instance.__dict__.pop('_warehouse_subscription', None)
    instance.__dict__.pop('_warehouse_owner', None)
