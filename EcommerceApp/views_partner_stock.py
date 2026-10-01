"""Isolated, read-only product and availability API for an external partner."""
import hmac
from functools import wraps

from django.conf import settings
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .magacin import recorded_stock_qs
from .models import Product, WarehouseStock


def partner_access(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        expected = getattr(settings, 'PARTNER_STOCK_API_KEY', '').strip()
        internal_keys = [getattr(settings, name, '') for name in ('SYNC_API_KEY', 'CATALOG_SYNC_API_KEY')]
        if not expected or any(key and hmac.compare_digest(expected.encode(), key.encode()) for key in internal_keys):
            return JsonResponse({'error': 'Partner API nije konfigurisan.'}, status=503)
        auth = request.headers.get('Authorization', '').split()
        if len(auth) != 2 or auth[0].lower() != 'bearer' or not hmac.compare_digest(auth[1].encode(), expected.encode()):
            response = JsonResponse({'error': 'Neispravan pristupni token.'}, status=401)
            response['WWW-Authenticate'] = 'Bearer'
            return response
        return view(request, *args, **kwargs)
    return never_cache(wrapped)


def _products():
    return Product.objects.filter(aktivan=True, sakriven_do_stanja=False).prefetch_related('varijacije').order_by('pk')


def _serialize(products):
    ids = [product.pk for product in products]
    managed = set(WarehouseStock.objects.filter(product_id__in=ids).values_list('product_id', flat=True))
    quantities = {}
    totals = {}
    for stock in recorded_stock_qs().filter(product_id__in=ids).values('product_id', 'variation_id', 'kolicina', 'rezervisano'):
        qty = max(0, stock['kolicina'] - max(0, stock['rezervisano']))
        key = (stock['product_id'], stock['variation_id'])
        quantities[key] = quantities.get(key, 0) + qty
        totals[stock['product_id']] = totals.get(stock['product_id'], 0) + qty
    result = []
    for product in products:
        warehouse_managed = product.pk in managed or product.magacin_sync_at is not None
        def available(variation=None):
            if warehouse_managed:
                return quantities.get((product.pk, variation.pk), 0) if variation else totals.get(product.pk, 0)
            item = variation or product
            return max(0, int(item.stanje or 0)) if item.na_stanju else 0
        result.append({
            'id': product.pk, 'name': product.naziv, 'sku': product.sifra or '',
            'quantity': available(),
            'variants': [{'id': v.pk, 'name': v.naziv, 'sku': v.sifra or '', 'quantity': available(v)}
                         for v in sorted(product.varijacije.all(), key=lambda v: v.pk)],
        })
    return result


@partner_access
@require_GET
def products(request):
    try:
        page = int(request.GET.get('page', '1'))
        page_size = int(request.GET.get('page_size', '100'))
    except (ValueError, TypeError):
        return JsonResponse({'error': 'page i page_size moraju biti brojevi.'}, status=400)
    if page < 1 or not 1 <= page_size <= 100:
        return JsonResponse({'error': 'page mora biti >= 1, page_size između 1 i 100.'}, status=400)
    queryset = _products()
    if request.GET.get('sku'):
        queryset = queryset.filter(sifra=request.GET['sku'])
    paginator = Paginator(queryset, page_size)
    if page > paginator.num_pages:
        return JsonResponse({'error': 'Stranica ne postoji.'}, status=404)
    current = paginator.page(page)
    return JsonResponse({'count': paginator.count, 'page': page, 'page_size': page_size,
                         'next_page': page + 1 if current.has_next() else None,
                         'results': _serialize(list(current.object_list))})


@partner_access
@require_GET
def product_detail(request, pk):
    product = _products().filter(pk=pk).first()
    if product is None:
        return JsonResponse({'error': 'Artikal nije pronađen.'}, status=404)
    return JsonResponse(_serialize([product])[0])
