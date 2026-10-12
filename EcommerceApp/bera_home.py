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
        if re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            result['bera_button_color'] = color
            result['bera_button_text'] = '#ffffff'
    return result


def homepage_context(context, request):
    from .views import _home_product_queryset
    result = storefront_context()
    result['home_css_optimized'] = not context.get('filters_active')
    result['home_new_offer_products'] = list(_home_product_queryset(request).filter(pk__in=HomeNovoProduct.objects.filter(aktivan=True).values('artikal_id')).order_by('noviteti_na_pocetnoj__redoslijed', 'noviteti_na_pocetnoj__id').filter(noviteti_na_pocetnoj__aktivan=True).distinct()[:6]) if not context.get('filters_active') else []

    # Reuse the optional bestseller selection table when already present in the
    # destination schema; old installations continue using their existing selection.
    if not context.get('filters_active'):
        table = 'EcommerceApp_homebestsellerproduct'
        if table in schema_tables():
            with connection.cursor() as cursor:
                cursor.execute('SELECT artikal_id FROM ' + connection.ops.quote_name(table) + ' WHERE aktivan = %s ORDER BY redoslijed, id', [True])
                ids = [row[0] for row in cursor.fetchall()]
            if ids:
                products = {product.pk: product for product in _home_product_queryset(request).filter(pk__in=ids)}
                result['latest_products'] = [products[pk] for pk in ids if pk in products][:6]
    if not context.get('filters_active'):
        from .utils.seo import get_page_seo
        seo = get_page_seo('home')
        if seo:
            result['seo_tekst_iznad'] = seo.seo_tekst_iznad or ''
            result['seo_tekst_ispod'] = seo.seo_tekst_ispod or ''
    return result


def render_storefront(request, template, context):
    """Render the active storefront while retaining existing view logic."""
    from django.shortcuts import render
    if template not in {'home.html', 'cart.html', 'checkout.html', 'product_detail.html', 'category.html', 'category_subcategories.html', 'brands.html'} and request.user.is_superuser and request.GET.get('izgled') == 'prethodni':
        return render(request, template, context)
    context.update(storefront_context())
    return render(request, 'bera_reference/' + template, context)
