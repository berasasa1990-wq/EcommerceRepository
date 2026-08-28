"""
Catalog Sync API — read-only JSON za partnera koji želi sinkronizovati svoj sajt.

Auth (jedan od headera):
  X-Api-Key: <CATALOG_SYNC_API_KEY>
  X-Sync-Key: <CATALOG_SYNC_API_KEY>
  Authorization: Bearer <CATALOG_SYNC_API_KEY>

Endpointi:
  GET /api/v1/ping/
  GET /api/v1/products/?page=1&page_size=50&updated_since=2026-01-01T00:00:00
  GET /api/v1/products/<slug>/
  GET /api/v1/categories/
  GET /api/v1/brands/
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET

from .models import Brand, Category, Product


def _api_key_expected() -> str:
    return (
        (getattr(settings, 'CATALOG_SYNC_API_KEY', None) or '').strip()
        or (getattr(settings, 'SYNC_API_KEY', None) or '').strip()
    )


def _extract_api_key(request) -> str:
    key = (request.headers.get('X-Api-Key') or request.headers.get('X-Sync-Key') or '').strip()
    if key:
        return key
    auth = (request.headers.get('Authorization') or '').strip()
    if auth.lower().startswith('bearer '):
        return auth[7:].strip()
    # query ?api_key= samo za brzi test (ne preporučeno u produkciji)
    return (request.GET.get('api_key') or '').strip()


def _auth_ok(request) -> bool:
    expected = _api_key_expected()
    if not expected:
        return False
    provided = _extract_api_key(request)
    return bool(provided) and provided == expected


def _unauthorized():
    return JsonResponse(
        {
            'ok': False,
            'error': 'Neautorizovan. Pošalji validan API ključ (X-Api-Key ili Authorization: Bearer …).',
        },
        status=401,
    )


def _service_unavailable():
    return JsonResponse(
        {
            'ok': False,
            'error': 'Catalog Sync API nije konfigurisan. Postavi CATALOG_SYNC_API_KEY u .env.',
        },
        status=503,
    )


def _require_catalog_auth(view_fn):
    def wrapper(request, *args, **kwargs):
        if not _api_key_expected():
            return _service_unavailable()
        if not _auth_ok(request):
            return _unauthorized()
        return view_fn(request, *args, **kwargs)

    wrapper.__name__ = getattr(view_fn, '__name__', 'catalog_api_view')
    wrapper.__doc__ = getattr(view_fn, '__doc__', None)
    return wrapper


def _site_base(request) -> str:
    try:
        return request.build_absolute_uri('/').rstrip('/')
    except Exception:
        base = (getattr(settings, 'SITE_URL', None) or '').rstrip('/')
        return base or 'http://localhost:8000'


def _abs_url(request, file_field) -> str | None:
    if not file_field:
        return None
    try:
        url = file_field.url
    except Exception:
        return None
    if not url:
        return None
    if url.startswith('http://') or url.startswith('https://'):
        return url
    base = _site_base(request)
    if not url.startswith('/'):
        url = '/' + url
    return f'{base}{url}'


def _dec(val) -> str | None:
    if val is None:
        return None
    if isinstance(val, Decimal):
        return format(val, 'f')
    return str(val)


def _serialize_category(cat: Category | None) -> dict | None:
    if not cat:
        return None
    parent = cat.roditelj
    return {
        'id': cat.pk,
        'naziv': cat.naziv,
        'slug': cat.slug,
        'roditelj_id': parent.pk if parent else None,
        'roditelj_slug': parent.slug if parent else None,
        'roditelj_naziv': parent.naziv if parent else None,
        'aktivan': bool(cat.aktivan),
    }


def _serialize_brand(brand: Brand | None, request=None) -> dict | None:
    if not brand:
        return None
    data = {
        'id': brand.pk,
        'naziv': brand.naziv,
        'slug': brand.slug,
    }
    if request is not None and getattr(brand, 'slika', None):
        data['slika'] = _abs_url(request, brand.slika)
    return data


def _serialize_variation(var, request) -> dict:
    return {
        'id': var.pk,
        'naziv': var.naziv,
        'sifra': var.sifra or '',
        'cijena': _dec(getattr(var, 'bazna_cijena', None) or getattr(var, 'cijena', None)),
        'prikazna_cijena': _dec(getattr(var, 'prikazna_cijena', None)),
        'na_stanju': bool(getattr(var, 'na_stanju', True)),
        'pakovanje_komada': getattr(var, 'pakovanje_komada', None),
        'slika': _abs_url(request, getattr(var, 'slika', None)),
    }


def _serialize_product(product: Product, request, *, detail: bool = False) -> dict:
    prikazna = product.prikazna_cijena
    bazna = product.bazna_cijena
    na_akciji = bool(product.na_akciji)
    images = []
    main = _abs_url(request, product.prikazna_slika if hasattr(product, 'prikazna_slika') else product.slika)
    if main:
        images.append({'url': main, 'glavna': True})
    if detail:
        for img in product.dodatne_slike.all():
            u = _abs_url(request, img.slika)
            if u:
                images.append({'url': u, 'glavna': False, 'redoslijed': img.redoslijed})

    data = {
        'id': product.pk,
        'naziv': product.naziv,
        'slug': product.slug,
        'sifra': product.sifra or '',
        'barkod': product.barkod or '',
        'url': f"{_site_base(request)}{product.get_absolute_url()}",
        'aktivan': bool(product.aktivan),
        'na_stanju': bool(product.na_stanju),
        # stanje = raspoloživo za prodaju (magacin + maloprodaja).
        # Ako nije na sajtu, šaljemo 0 da partneri ne povuku staro stanje od 1 kom.
        'stanje': int(product.stanje or 0) if product.na_stanju else 0,
        'dostupno': int(product.stanje or 0) if product.na_stanju else 0,
        'cijena': _dec(bazna),
        'prikazna_cijena': _dec(prikazna),
        'na_akciji': na_akciji,
        'akcija_postotak': _dec(product.akcija_postotak) if na_akciji else None,
        'akcijska_cijena': _dec(product.akcijska_cijena) if na_akciji else None,
        'akcija_do': product.akcija_do.isoformat() if product.akcija_do else None,
        'pakovanje_komada': product.pakovanje_komada,
        'je_novitet': bool(product.je_novitet),
        'je_hit': bool(product.je_hit),
        'kategorija': _serialize_category(product.kategorija),
        'brend': _serialize_brand(product.brend, request),
        'slika': main,
        'slike': images,
        'azuriran': product.azuriran.isoformat() if getattr(product, 'azuriran', None) else None,
        'kreiran': product.kreiran.isoformat() if getattr(product, 'kreiran', None) else None,
    }
    if detail:
        data['opis'] = product.opis or ''
        data['tagovi'] = [
            {'id': t.pk, 'naziv': t.naziv, 'slug': t.slug}
            for t in product.tagovi.all()
        ]
        data['varijacije'] = [
            _serialize_variation(v, request)
            for v in product.varijacije.all()
        ]
    return data


def _page_params(request):
    try:
        page = max(1, int(request.GET.get('page') or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(request.GET.get('page_size') or request.GET.get('limit') or 50)
    except (TypeError, ValueError):
        page_size = 50
    page_size = max(1, min(page_size, 100))
    return page, page_size


@require_GET
@_require_catalog_auth
def catalog_api_ping(request):
    return JsonResponse({
        'ok': True,
        'service': 'catalog-sync',
        'version': '1',
        'time': timezone.now().isoformat(),
    })


@require_GET
@_require_catalog_auth
def catalog_api_products(request):
    qs = (
        Product.objects
        .filter(aktivan=True)
        .select_related('kategorija', 'kategorija__roditelj', 'brend')
        .order_by('id')
    )

    # filteri
    if request.GET.get('na_stanju') in ('1', 'true', 'yes'):
        qs = qs.filter(na_stanju=True)
    if request.GET.get('na_stanju') in ('0', 'false', 'no'):
        qs = qs.filter(na_stanju=False)
    brand = (request.GET.get('brend') or request.GET.get('brand') or '').strip()
    if brand:
        qs = qs.filter(brend__slug=brand) if not brand.isdigit() else qs.filter(brend_id=int(brand))
    cat = (request.GET.get('kategorija') or request.GET.get('category') or '').strip()
    if cat:
        qs = qs.filter(kategorija__slug=cat) if not cat.isdigit() else qs.filter(kategorija_id=int(cat))
    q = (request.GET.get('q') or '').strip()
    if q:
        from django.db.models import Q
        qs = qs.filter(
            Q(naziv__icontains=q)
            | Q(sifra__icontains=q)
            | Q(barkod__icontains=q)
            | Q(slug__icontains=q)
        )

    updated_since = (request.GET.get('updated_since') or request.GET.get('since') or '').strip()
    if updated_since:
        dt = parse_datetime(updated_since)
        if dt is None:
            try:
                dt = datetime.fromisoformat(updated_since.replace('Z', '+00:00'))
            except ValueError:
                dt = None
        if dt is not None:
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_current_timezone())
            qs = qs.filter(azuriran__gte=dt)

    page, page_size = _page_params(request)
    paginator = Paginator(qs, page_size)
    page_obj = paginator.get_page(page)

    results = [_serialize_product(p, request, detail=False) for p in page_obj.object_list]
    return JsonResponse({
        'ok': True,
        'count': paginator.count,
        'page': page_obj.number,
        'page_size': page_size,
        'total_pages': paginator.num_pages,
        'has_next': page_obj.has_next(),
        'has_previous': page_obj.has_previous(),
        'next_page': page_obj.next_page_number() if page_obj.has_next() else None,
        'results': results,
    })


@require_GET
@_require_catalog_auth
def catalog_api_product_detail(request, slug):
    product = (
        Product.objects
        .filter(aktivan=True, slug=slug)
        .select_related('kategorija', 'kategorija__roditelj', 'brend')
        .prefetch_related('dodatne_slike', 'tagovi', 'varijacije')
        .first()
    )
    if not product:
        return JsonResponse({'ok': False, 'error': 'Artikal nije pronađen.'}, status=404)
    return JsonResponse({
        'ok': True,
        'product': _serialize_product(product, request, detail=True),
    })


@require_GET
@_require_catalog_auth
def catalog_api_categories(request):
    qs = Category.objects.select_related('roditelj').order_by('redoslijed', 'naziv', 'id')
    if request.GET.get('aktivan', '1') not in ('0', 'false', 'no'):
        qs = qs.filter(aktivan=True)
    results = [_serialize_category(c) for c in qs]
    return JsonResponse({'ok': True, 'count': len(results), 'results': results})


@require_GET
@_require_catalog_auth
def catalog_api_brands(request):
    qs = Brand.objects.order_by('naziv', 'id')
    results = [_serialize_brand(b, request) for b in qs]
    return JsonResponse({'ok': True, 'count': len(results), 'results': results})
