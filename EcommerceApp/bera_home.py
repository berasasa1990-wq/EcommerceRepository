"""Adapt existing homepage data to imported BERA templates without replacing views."""
import json
from django.conf import settings
from urllib.parse import urlsplit
from .models import SiteSettings, HomeNovoProduct
from django.db import connection
from .request_performance import schema_tables


def storefront_context():
    site = SiteSettings.load()
    from .context_processors import _messenger_contact_url
    result = {
        'shop_name': 'CarpologijaBH' if 'opremazaribolov.ba' in (site.seo_organizacija_naziv or '').lower() else (site.seo_organizacija_naziv or getattr(settings, 'SITE_NAME', 'CarpologijaBH')),
        'public_site_url': settings.SITE_URL,
        'contact_email': site.seo_email or settings.STORE_EMAIL,
        'contact_phone': site.kontakt_telefon or settings.STORE_PHONE,
        'facebook_url': site.seo_facebook_url,
        'instagram_url': site.seo_instagram_url,
        'contact_messenger_url': _messenger_contact_url(site.kontakt_messenger) if site.kontakt_messenger else '',
        'facebook_domain_verification': getattr(settings, 'FACEBOOK_DOMAIN_VERIFICATION', ''),
        'google_analytics_id': getattr(settings, 'GOOGLE_ANALYTICS_ID', ''),
        'google_ads_id': getattr(settings, 'GOOGLE_ADS_ID', ''),
        'google_tag_id': getattr(settings, 'GOOGLE_ANALYTICS_ID', '') or getattr(settings, 'GOOGLE_ADS_ID', ''),
        'media_origin': ('https://' + urlsplit(settings.MEDIA_URL).netloc) if urlsplit(settings.MEDIA_URL).netloc else '',
    }
    content_path = settings.BASE_DIR / 'config/bera_home_content.json'
    result['bera_home_content'] = json.loads(content_path.read_text()) if content_path.exists() else {}
    from .models import BeraAppearance
    result['bera_appearance_ready'] = BeraAppearance._meta.db_table in schema_tables()
    if result['bera_appearance_ready']:
        color = BeraAppearance.objects.filter(pk=1).values_list('button_color', flat=True).first() or ''
        import re
        result['bera_button_orange'] = color == 'orange'
        if color == 'orange':
            color = '#fc730a'
            result['bera_button_background'] = 'linear-gradient(180deg, rgb(255, 148, 1) 0%, rgb(252, 115, 10) 50%, rgb(244, 92, 2) 100%)'
        if re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            result.setdefault('bera_button_background', color)
            result['bera_button_color'] = color
            result['bera_button_text'] = '#ffffff'
    return result


def homepage_bestseller_ids():
    """Read the optional public selection once, before hydrating fallback cards."""
    from .request_performance import request_read

    def load():
        table = 'EcommerceApp_homebestsellerproduct'
        if table not in schema_tables():
            return []
        with connection.cursor() as cursor:
            cursor.execute('SELECT artikal_id FROM ' + connection.ops.quote_name(table)
                           + ' WHERE aktivan = %s ORDER BY redoslijed, id', [True])
            return [row[0] for row in cursor.fetchall()]
    return request_read(('homepage-bestseller-ids', connection.alias), load)


def _selected_bestseller_products(ids, request):
    """Keep selection order (including duplicates), hydrate only visible cards."""
    from .views import _home_product_queryset
    if not ids:
        return []
    queryset = _home_product_queryset(request).filter(pk__in=ids)
    visible_ids = set(queryset.order_by().values_list('pk', flat=True))
    selected_ids = [pk for pk in ids if pk in visible_ids][:6]
    products = {product.pk: product for product in queryset.filter(pk__in=selected_ids)}
    return [products[pk] for pk in selected_ids]


def homepage_context(context, request):
    from .views import _home_product_queryset
    result = storefront_context()
    result['home_css_optimized'] = not context.get('filters_active')
    result['home_new_offer_products'] = list(_home_product_queryset(request).filter(pk__in=HomeNovoProduct.objects.filter(aktivan=True).values('artikal_id')).order_by('noviteti_na_pocetnoj__redoslijed', 'noviteti_na_pocetnoj__id').filter(noviteti_na_pocetnoj__aktivan=True).distinct()[:6]) if not context.get('filters_active') else []

    if not context.get('filters_active'):
        from .utils.seo import get_page_seo
        seo = get_page_seo('home')
        if seo:
            result['seo_tekst_iznad'] = seo.seo_tekst_iznad or ''
            result['seo_tekst_ispod'] = seo.seo_tekst_ispod or ''
        result.update(homepage_product_preload({**context, **result}, request))
    return result


def homepage_product_preload(context, request):
    """Match the first rendered product section after the curated selection."""
    if context.get('hero_slides') or context.get('filters_active'):
        return {}
    sections = ('featured_products', 'latest_products', 'home_new_offer_products')
    first = next((context[name][0] for name in sections if context.get(name)), None)
    result = {'lcp_image_url': None, 'lcp_image_srcset': None,
              'lcp_image_sizes': '(max-width: 768px) 50vw, 20vw',
              'eager_first_novo_image': not context.get('featured_products') and bool(context.get('latest_products')),
              'eager_first_new_offer_image': not context.get('featured_products') and not context.get('latest_products') and bool(context.get('home_new_offer_products'))}
    if first and first.prikazna_slika:
        image = first.prikazna_slika_responsive or {'src': first.prikazna_slika.url}
        result['lcp_image_url'] = request.build_absolute_uri(image['src'])
        result['lcp_image_srcset'] = image.get('srcset') or None
    return result


def render_storefront(request, template, context):
    """Render the active storefront while retaining existing view logic."""
    from django.shortcuts import render
    if template not in {'home.html', 'cart.html', 'checkout.html', 'product_detail.html', 'category.html', 'category_subcategories.html', 'brands.html'} and request.user.is_superuser and request.GET.get('izgled') == 'prethodni':
        return render(request, template, context)
    context.update(storefront_context())
    return render(request, 'bera_reference/' + template, context)
