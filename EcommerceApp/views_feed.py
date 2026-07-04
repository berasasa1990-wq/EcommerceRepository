from django.db.models import Prefetch
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.views.decorators.cache import cache_page

from .models import Product, ProductVariation


def _category_breadcrumb(category, cache):
    if not category:
        return ''
    if category.pk in cache:
        return cache[category.pk]
    parts = []
    current = category
    while current:
        parts.append(current.naziv)
        current = current.roditelj
    path = ' > '.join(reversed(parts))
    cache[category.pk] = path
    return path


def _feed_description(product):
    raw = (product.opis or product.seo_description or product.naziv).strip()
    text = strip_tags(raw).replace('\n', ' ').strip()
    return text[:5000]


def _build_feed_item(request, product, variation, category_cache):
    if variation:
        item_id = variation.sifra or product.sifra or f'p{product.pk}-v{variation.pk}'
        title = f'{product.naziv} — {variation.naziv}'
        price = variation.prikazna_cijena
        availability = 'in stock' if variation.na_stanju else 'out of stock'
        mpn = variation.sifra or product.sifra or ''
    else:
        item_id = product.sifra or f'p{product.pk}'
        title = product.naziv
        price = product.prikazna_cijena
        availability = 'in stock' if product.na_stanju else 'out of stock'
        mpn = product.sifra or ''

    image = product.prikazna_slika
    return {
        'id': item_id,
        'title': title,
        'description': _feed_description(product),
        'availability': availability,
        'condition': 'new',
        'price': f'{price:.2f} BAM',
        'brand': product.brend.naziv if product.brend_id else '',
        'link': request.build_absolute_uri(product.get_absolute_url()),
        'image_link': request.build_absolute_uri(image.url) if image else '',
        'product_type': _category_breadcrumb(product.kategorija, category_cache),
        'mpn': mpn,
    }


def _feed_items(request):
    products = (
        Product.objects.filter(aktivan=True)
        .select_related('kategorija', 'kategorija__roditelj', 'brend')
        .prefetch_related(
            Prefetch(
                'varijacije',
                queryset=ProductVariation.objects.order_by('redoslijed', 'id'),
            ),
        )
        .order_by('id')
    )
    category_cache = {}
    items = []
    for product in products:
        variations = list(product.varijacije.all())
        if variations:
            for variation in variations:
                items.append(_build_feed_item(request, product, variation, category_cache))
        else:
            items.append(_build_feed_item(request, product, None, category_cache))
    return items


@cache_page(60 * 15)
def facebook_feed(request):
    xml = render_to_string(
        'feeds/facebook_feed.xml',
        {'feed_items': _feed_items(request)},
        request=request,
    )
    return HttpResponse(xml, content_type='application/xml; charset=utf-8')