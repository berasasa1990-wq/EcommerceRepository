import random

from .models import Product

SERBIAN_PROFILES = (
    ('Marko', 'Brčkog'),
    ('Nikola', 'Banje Luke'),
    ('Stefan', 'Sarajeva'),
    ('Miloš', 'Bijeljine'),
    ('Petar', 'Trebinja'),
)

MUSLIM_PROFILES = (
    ('Amar', 'Tuzle'),
    ('Haris', 'Mostara'),
    ('Emir', 'Zenice'),
    ('Adnan', 'Travnika'),
    ('Kenan', 'Bihaća'),
)


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


def _pick_profile(request):
    session_key = getattr(request.session, 'session_key', None) or ''
    bucket = sum(ord(char) for char in session_key) % 2 if session_key else random.randint(0, 1)
    profiles = SERBIAN_PROFILES if bucket == 0 else MUSLIM_PROFILES
    return random.choice(profiles)


def _pick_product():
    product_ids = list(
        Product.objects.filter(aktivan=True, na_stanju=True)
        .order_by('?')
        .values_list('pk', flat=True)[:1],
    )
    if not product_ids:
        product_ids = list(
            Product.objects.filter(aktivan=True)
            .order_by('?')
            .values_list('pk', flat=True)[:1],
        )
    if not product_ids:
        return None
    return Product.objects.filter(pk=product_ids[0]).only('pk', 'naziv', 'slug').first()


def build_social_proof_context(request):
    if not _should_show_social_proof(request):
        return None

    product = _pick_product()
    if not product:
        return None

    name, city = _pick_profile(request)
    return {
        'name': name,
        'city': city,
        'product_name': product.naziv,
        'product_url': product.get_absolute_url(),
    }