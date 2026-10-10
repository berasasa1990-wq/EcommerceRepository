"""Identity from the existing singleton SiteSettings; no separate configuration store."""
from urllib.parse import urlsplit
from django.conf import settings
from django.db import DatabaseError
from django.templatetags.static import static


def site_identity():
    from .models import SiteSettings
    try:
        site = SiteSettings.load()
    except DatabaseError:
        site = SiteSettings()
    return {
        'google_analytics_id': settings.GOOGLE_ANALYTICS_ID,
        'google_ads_id': settings.GOOGLE_ADS_ID,
        'google_tag_id': settings.GOOGLE_ANALYTICS_ID or settings.GOOGLE_ADS_ID,
        'facebook_domain_verification': settings.FACEBOOK_DOMAIN_VERIFICATION,
        'media_origin': ('{0.scheme}://{0.netloc}'.format(urlsplit(settings.MEDIA_URL))
                         if urlsplit(settings.MEDIA_URL).scheme in ('http', 'https') else ''),
        'brand_logo_url': site.logo.url if site.logo else static('img/webshop-logo.svg'),
        'shop_name': site.seo_organizacija_naziv or settings.SITE_NAME,
        'company_name': site.company_name or site.seo_organizacija_naziv or settings.SITE_NAME,
        'contact_email': site.seo_email or settings.STORE_EMAIL or settings.DEFAULT_FROM_EMAIL,
        'contact_phone': site.kontakt_telefon or settings.STORE_PHONE,
        'company_address': site.company_address,
        'public_site_url': settings.SITE_URL,
        'facebook_url': site.seo_facebook_url,
        'instagram_url': site.seo_instagram_url,
    }


def shop_name():
    return site_identity()['shop_name']


def branding_context(request):
    return site_identity()
