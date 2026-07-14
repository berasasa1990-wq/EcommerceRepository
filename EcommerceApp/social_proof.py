import random

from .models import Product

SERBIAN_PROFILES = (
    ('Marko', 'Brčkog'),
    ('Nikola', 'Banje Luke'),
    ('Stefan', 'Sarajeva'),
    ('Miloš', 'Bijeljine'),
    ('Petar', 'Trebinja'),
    ('Jovan', 'Prijedora'),
    ('Luka', 'Doboja'),
)

MUSLIM_PROFILES = (
    ('Amar', 'Tuzle'),
    ('Haris', 'Mostara'),
    ('Emir', 'Zenice'),
    ('Adnan', 'Travnika'),
    ('Kenan', 'Bihaća'),
    ('Faruk', 'Cazina'),
    ('Edin', 'Goražda'),
)

# Interval između toastova „neko je kupio…” — češće = jači social proof
SOCIAL_PROOF_INTERVAL_MS = 2 * 60 * 1000
SOCIAL_PROOF_FIRST_DELAY_MS = 28 * 1000
SOCIAL_PROOF_VISIBLE_MS = 10000


def _should_show_social_proof(request):
    if getattr(request, 'user', None) and request.user.is_authenticated and request.user.is_superuser:
        return False
    path = request.path or ''
    skip_prefixes = (
        '/admin/',
        '/nalog/',
        '/priprema-pristup/',
    )
    return not any(path.startswith(prefix) for prefix in skip_prefixes)


def _pick_profile(request=None):
    session_key = ''
    if request is not None:
        session_key = getattr(request.session, 'session_key', None) or ''
    if session_key:
        bucket = sum(ord(char) for char in session_key) % 2
    else:
        bucket = random.randint(0, 1)
    profiles = SERBIAN_PROFILES if bucket == 0 else MUSLIM_PROFILES
    return random.choice(profiles)


def _pick_product(exclude_ids=None):
    exclude_ids = {int(x) for x in (exclude_ids or []) if x}
    qs = Product.objects.filter(aktivan=True, na_stanju=True)
    if exclude_ids:
        qs = qs.exclude(pk__in=exclude_ids)
    product_ids = list(qs.order_by('?').values_list('pk', flat=True)[:1])
    if not product_ids:
        qs = Product.objects.filter(aktivan=True)
        if exclude_ids:
            qs = qs.exclude(pk__in=exclude_ids)
        product_ids = list(qs.order_by('?').values_list('pk', flat=True)[:1])
    if not product_ids:
        return None
    return Product.objects.filter(pk=product_ids[0]).only('pk', 'naziv', 'slug').first()


def build_social_proof_payload(request=None, exclude_ids=None):
    """Jedan toast zapis: ime, grad, artikal."""
    product = _pick_product(exclude_ids=exclude_ids)
    if not product:
        return None
    name, city = _pick_profile(request)
    return {
        'name': name,
        'city': city,
        'product_name': product.naziv,
        'product_url': product.get_absolute_url(),
        'product_id': product.pk,
    }


def build_social_proof_context(request):
    if not _should_show_social_proof(request):
        return None

    payload = build_social_proof_payload(request)
    if not payload:
        return None

    return {
        **payload,
        'interval_ms': SOCIAL_PROOF_INTERVAL_MS,
        'first_delay_ms': SOCIAL_PROOF_FIRST_DELAY_MS,
        'visible_ms': SOCIAL_PROOF_VISIBLE_MS,
        'poll_url': '/drustveni-dokaz/',
    }
