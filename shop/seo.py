import json
import re
from urllib.parse import urlsplit, urljoin
from xml.etree.ElementTree import Element, SubElement, tostring
from django.http import HttpResponse, Http404
from django.core.paginator import Paginator, EmptyPage
from django.urls import reverse
from django.utils.html import strip_tags
from .models import SEOSettings, Product, Category, Brand

PUBLIC_ROUTES = {'home', 'product_detail', 'category', 'brand', 'sales', 'new_arrivals', 'bestsellers'}


def settings_for(request):
    if not hasattr(request, '_seo_settings'):
        request._seo_settings = SEOSettings.objects.filter(pk=1).first() or SEOSettings()
    return request._seo_settings


def base_url(request):
    return settings_for(request).canonical_domain or request.build_absolute_uri('/').rstrip('/')


def indexable(request):
    route = request.resolver_match.url_name if request.resolver_match else None
    return route in PUBLIC_ROUTES and not request.GET.get('q') and settings_for(request).allow_indexing


class SEORobotsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.get('Content-Type', '').startswith('text/html'):
            if response.status_code != 200 or not indexable(request):
                response['X-Robots-Tag'] = 'noindex, nofollow'
        return response


def clean_text(value):
    return re.sub(r'\s+', ' ', strip_tags(value or '')).strip()


def metadata(context):
    request = context['request']
    cfg = settings_for(request)
    name = cfg.site_name or 'BERA'
    product = context.get('product')
    category = context.get('selected_category')
    brand = context.get('selected_brand')
    route = request.resolver_match.url_name if request.resolver_match else ''
    title = cfg.home_title or f'{name} | Online trgovina'
    description = cfg.description
    image = cfg.social_image.url if cfg.social_image else ''
    if product:
        title = product.seo_title or f'{product.name} | {name}'
        description = product.seo_description or clean_text(product.description)[:160] or cfg.description
        image = product.image.url if product.image else image
    elif category:
        title = category.seo_title or f'{category.name} | {name}'
        description = category.seo_description or cfg.description
    elif brand:
        title = f'{brand.name} | {name}'
    else:
        labels = {'sales': 'Akcijska ponuda', 'new_arrivals': 'Noviteti', 'bestsellers': 'Najprodavanije', 'cart': 'Korpa', 'checkout': 'Checkout', 'wishlist': 'Lista želja', 'account': 'Moj nalog', 'customer_login': 'Prijava', 'customer_register': 'Registracija', 'loyalty': 'Loyalty program'}
        if route in labels:
            title = f'{labels[route]} | {name}'
        if request.GET.get('q'):
            title = f'Pretraga: {request.GET["q"][:100]} | {name}'
    base = base_url(request)
    url = base + request.path
    image = urljoin(base + '/', image) if image else ''
    schema = []
    if cfg.structured_data and indexable(request):
        if product:
            item = {'@context': 'https://schema.org', '@type': 'Product', 'name': product.name,
                    'sku': str(product.pk), 'url': url,
                    'offers': {'@type': 'Offer', 'url': url, 'priceCurrency': 'BAM', 'price': format(product.price, '.2f'),
                        'availability': 'https://schema.org/OutOfStock' if product.is_out_of_stock else 'https://schema.org/InStock'}}
            if description:
                item['description'] = description
            if product.image:
                item['image'] = [image]
            if product.brand:
                item['brand'] = {'@type': 'Brand', 'name': product.brand.name}
            schema.append(item)
        elif route == 'home' and not category and not brand:
            organization = {'@context': 'https://schema.org', '@type': 'Organization', 'name': name, 'url': base + '/'}
            if cfg.contact_email:
                organization['email'] = cfg.contact_email
            if cfg.contact_phone:
                organization['telephone'] = cfg.contact_phone
            store = context.get('site_settings')
            if store and store.logo:
                organization['logo'] = urljoin(base + '/', store.logo.url)
            schema.extend([organization, {'@context': 'https://schema.org', '@type': 'WebSite', 'name': name, 'url': base + '/'}])
        if product or category or brand:
            crumbs = [{'@type': 'ListItem', 'position': 1, 'name': 'Početna', 'item': base + '/'}]
            if product and product.category:
                crumbs.append({'@type': 'ListItem', 'position': 2, 'name': product.category.name, 'item': base + product.category.get_absolute_url()})
            crumbs.append({'@type': 'ListItem', 'position': len(crumbs) + 1, 'name': product.name if product else category.name if category else brand.name, 'item': url})
            schema.append({'@context': 'https://schema.org', '@type': 'BreadcrumbList', 'itemListElement': crumbs})
    # Escaping '<' prevents user-entered descriptions from breaking out of JSON scripts.
    schema_json = json.dumps(schema, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026') if schema else ''
    homepage = route == 'home' and not request.GET.get('q')
    return {'title': title, 'description': description, 'canonical': url if route in PUBLIC_ROUTES and not request.GET.get('q') else '',
            'robots': 'index, follow, max-image-preview:large' if indexable(request) else 'noindex, nofollow',
            'og_title': cfg.social_title if homepage and cfg.social_title else title,
            'og_description': cfg.social_description if homepage and cfg.social_description else description,
            'image': image, 'url': url, 'type': 'product' if product else 'website', 'config': cfg, 'schema_json': schema_json}


def robots(request):
    cfg = settings_for(request)
    lines = ['User-agent: *', 'Allow: /', 'Disallow: /admin/', 'Disallow: /panel', 'Disallow: /bera-control/']
    if cfg.sitemap_enabled and cfg.allow_indexing:
        lines.append('Sitemap: ' + base_url(request) + reverse('seo_sitemap'))
    if not cfg.allow_indexing:
        lines.append('# Indexing disabled through page meta tags and X-Robots-Tag; crawling remains allowed to read noindex.')
    return HttpResponse('\n'.join(lines) + '\n', content_type='text/plain; charset=utf-8')


def sitemap_sources():
    return {'pages': [reverse(name) for name in ('home', 'sales', 'new_arrivals', 'bestsellers')],
            'products': Product.objects.filter(is_active=True).order_by('pk'),
            'categories': Category.objects.filter(product__is_active=True).distinct().order_by('pk'),
            'brands': Brand.objects.filter(product__is_active=True).distinct().order_by('pk')}


def sitemap(request, section=None, page=1):
    cfg = settings_for(request)
    if not cfg.sitemap_enabled or not cfg.allow_indexing:
        raise Http404
    base = base_url(request)
    sources = sitemap_sources()
    ns = 'http://www.sitemaps.org/schemas/sitemap/0.9'
    if section is None:
        root = Element('sitemapindex', xmlns=ns)
        for name, source in sources.items():
            paginator = Paginator(source, 1000)
            if not paginator.count:
                continue
            for number in paginator.page_range:
                entry = SubElement(root, 'sitemap')
                SubElement(entry, 'loc').text = base + reverse('seo_sitemap_section', kwargs={'section': name, 'page': number})
    else:
        if section not in sources:
            raise Http404
        try:
            entries = Paginator(sources[section], 1000).page(page)
        except EmptyPage:
            raise Http404
        root = Element('urlset', xmlns=ns)
        for entry in entries:
            if section == 'pages':
                path = entry
            elif section == 'brands':
                path = reverse('brand', kwargs={'brand_pk': entry.pk})
            else:
                path = entry.get_absolute_url()
            SubElement(SubElement(root, 'url'), 'loc').text = base + path
    return HttpResponse(tostring(root, encoding='utf-8', xml_declaration=True), content_type='application/xml; charset=utf-8')
