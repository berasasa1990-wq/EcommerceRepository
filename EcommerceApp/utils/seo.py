"""
SEO helperi za webshop — PageSEO, entity SEO, auto defaults, JSON-LD.

Best practice (kratko):
- Unique title (50–60 znakova) i description (140–160) po URL-u
- Jedan H1 po stranici, relevantan ključnim riječima
- Unique SEO tekst na kategorijama (ne duplicate thin content)
- Product JSON-LD (cijena, stock, brand, sku) za rich results
- Organization + WebSite + SearchAction na početnoj
- Canonical na čist URL; noindex na korpi/checkout/nalogu
- Sitemap samo indexabilne stranice
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

from django.conf import settings
from django.db import DatabaseError
from django.urls import reverse
from django.utils.html import strip_tags

from EcommerceApp.models import PageSEO

SITE_BRAND = 'opremazaribolov.ba'
SHOP_PHRASE = 'Oprema za ribolov'


def get_page_seo(page_key: str) -> PageSEO | None:
    if not page_key:
        return None
    try:
        return PageSEO.objects.filter(page_key=page_key).first()
    except DatabaseError:
        return None


def page_seo_context(
    page_key: str,
    *,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Context keys:
      seo_title, seo_description, seo_h1, seo_tekst_iznad, seo_tekst_ispod
    """
    defaults = defaults or {}
    seo = get_page_seo(page_key)
    if not seo:
        return {
            'seo_title': defaults.get('seo_title') or '',
            'seo_description': defaults.get('seo_description') or '',
            'seo_h1': defaults.get('seo_h1') or '',
            'seo_tekst_iznad': defaults.get('seo_tekst_iznad') or '',
            'seo_tekst_ispod': defaults.get('seo_tekst_ispod') or '',
        }
    return {
        'seo_title': (seo.seo_title or '').strip() or (defaults.get('seo_title') or ''),
        'seo_description': (seo.meta_description or '').strip()
        or (defaults.get('seo_description') or ''),
        'seo_h1': (seo.h1_naslov or '').strip() or (defaults.get('seo_h1') or ''),
        'seo_tekst_iznad': (seo.seo_tekst_iznad or '').strip()
        or (defaults.get('seo_tekst_iznad') or ''),
        'seo_tekst_ispod': (seo.seo_tekst_ispod or '').strip()
        or (defaults.get('seo_tekst_ispod') or ''),
    }


def entity_seo_context(
    *,
    meta_title: str = '',
    meta_description: str = '',
    h1_naslov: str = '',
    seo_tekst_iznad: str = '',
    seo_tekst_ispod: str = '',
    default_title: str = '',
    default_description: str = '',
    default_h1: str = '',
) -> dict[str, Any]:
    return {
        'seo_title': (meta_title or '').strip() or (default_title or ''),
        'seo_description': (meta_description or '').strip() or (default_description or ''),
        'seo_h1': (h1_naslov or '').strip() or (default_h1 or default_title or ''),
        'seo_tekst_iznad': (seo_tekst_iznad or '').strip(),
        'seo_tekst_ispod': (seo_tekst_ispod or '').strip(),
    }


def _clip(text: str, max_len: int) -> str:
    text = re.sub(r'\s+', ' ', (text or '').strip())
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 1]
    if ' ' in cut:
        cut = cut.rsplit(' ', 1)[0]
    return cut.rstrip(' ,;:-') + '…'


def _title_suffix(site_settings=None) -> str:
    if site_settings is not None:
        s = (getattr(site_settings, 'seo_title_suffix', None) or '').strip()
        if s:
            return s
    return SITE_BRAND


def with_title_suffix(title: str, site_settings=None) -> str:
    """Dodaj brand sufiks ako već nije u title-u."""
    title = (title or '').strip()
    if not title:
        return _title_suffix(site_settings)
    suffix = _title_suffix(site_settings)
    if not suffix:
        return title
    low = title.casefold()
    if suffix.casefold() in low or SITE_BRAND.casefold() in low:
        return title
    joined = f'{title} | {suffix}'
    if len(joined) <= 70:
        return joined
    # skratiti lijevi dio
    max_left = 70 - len(suffix) - 3
    if max_left < 20:
        return _clip(title, 70)
    return f'{_clip(title, max_left)} | {suffix}'


def auto_product_seo_title(product, site_settings=None) -> str:
    """
    Naziv | Brend — ili skraćeno.
    Google: primarne riječi ispred, brand na kraju.
    """
    name = (getattr(product, 'naziv', None) or '').strip()
    brand = ''
    try:
        if getattr(product, 'brend', None) and product.brend.naziv:
            brand = product.brend.naziv.strip()
    except Exception:
        brand = ''
    if brand and brand.casefold() not in name.casefold():
        core = f'{name} | {brand}'
    else:
        core = name
    return with_title_suffix(core, site_settings)


def auto_product_seo_description(product) -> str:
    """
    Unique-ish default description:
    Naziv (+ brand) + benefit + CTA. Max ~155–160.
    """
    name = (getattr(product, 'naziv', None) or '').strip()
    brand = ''
    try:
        if getattr(product, 'brend', None) and product.brend.naziv:
            brand = product.brend.naziv.strip()
    except Exception:
        brand = ''
    cat = ''
    try:
        if getattr(product, 'kategorija', None) and product.kategorija.naziv:
            cat = product.kategorija.naziv.strip()
    except Exception:
        cat = ''

    # Prefer product description snippet if short & useful
    opis = ''
    raw = getattr(product, 'opis', None) or ''
    if raw:
        opis = strip_tags(str(raw))
        opis = re.sub(r'\s+', ' ', opis).strip()
        if len(opis) < 40:
            opis = ''

    if opis:
        base = f'{name}. {opis}'
    else:
        lead = name
        if brand:
            lead = f'{name} ({brand})'
        if cat:
            lead = f'{lead} — {cat}'
        base = (
            f'{lead}. Kupite online u BiH — brza dostava i garancija kvaliteta. '
            f'Štapovi, mašinice, varalice i pribor na {SITE_BRAND}.'
        )
    return _clip(base, 158)


def auto_category_seo_title(category, site_settings=None) -> str:
    name = (getattr(category, 'naziv', None) or '').strip()
    core = f'{name} | {SHOP_PHRASE}'
    return with_title_suffix(core, site_settings)


def auto_category_seo_description(category) -> str:
    name = (getattr(category, 'naziv', None) or '').strip()
    return _clip(
        f'{name} — širok izbor kvalitete opreme za ribolov. '
        f'Provjereni brendovi, povoljne cijene i brza dostava širom Bosne i Hercegovine. '
        f'Naručite online na {SITE_BRAND}.',
        158,
    )


def site_url_base() -> str:
    return (getattr(settings, 'SITE_URL', '') or '').rstrip('/')


def absolute_url(path_or_url: str) -> str:
    if not path_or_url:
        return site_url_base() + '/'
    if path_or_url.startswith('http://') or path_or_url.startswith('https://'):
        return path_or_url
    base = site_url_base()
    if not path_or_url.startswith('/'):
        path_or_url = '/' + path_or_url
    return urljoin(base + '/', path_or_url.lstrip('/'))


def json_ld(data: dict | list) -> str:
    """Safe JSON for <script type=application/ld+json>."""
    return json.dumps(data, ensure_ascii=False, separators=(',', ':'))


def organization_json_ld(site_settings) -> dict:
    base = site_url_base()
    name = (
        (getattr(site_settings, 'seo_organizacija_naziv', None) or '').strip()
        or SITE_BRAND
    )
    logo = ''
    if getattr(site_settings, 'logo', None):
        try:
            logo = absolute_url(site_settings.logo.url)
        except Exception:
            logo = ''
    if not logo:
        logo = absolute_url('/static/img/logo.png')

    same_as = []
    for attr in ('seo_facebook_url', 'seo_instagram_url'):
        url = (getattr(site_settings, attr, None) or '').strip()
        if url:
            same_as.append(url)

    email = (getattr(site_settings, 'seo_email', None) or '').strip()
    phone = (getattr(site_settings, 'kontakt_telefon', None) or '').strip()

    org: dict[str, Any] = {
        '@context': 'https://schema.org',
        '@type': 'Organization',
        'name': name,
        'url': base + '/',
        'logo': logo,
    }
    if same_as:
        org['sameAs'] = same_as
    contact: dict[str, Any] = {
        '@type': 'ContactPoint',
        'contactType': 'customer service',
        'availableLanguage': ['bs', 'hr', 'sr'],
    }
    if email:
        contact['email'] = email
    if phone:
        contact['telephone'] = phone
    org['contactPoint'] = [contact]

    grad = (getattr(site_settings, 'seo_grad', None) or '').strip()
    drzava = (getattr(site_settings, 'seo_drzava', None) or '').strip() or 'BA'
    if grad:
        org['address'] = {
            '@type': 'PostalAddress',
            'addressLocality': grad,
            'addressCountry': drzava,
        }
    return org


def website_json_ld(site_settings) -> dict:
    """WebSite + SearchAction — sitelinks search box u Googleu."""
    base = site_url_base()
    name = (
        (getattr(site_settings, 'seo_organizacija_naziv', None) or '').strip()
        or SITE_BRAND
    )
    # Pretraga je na početnoj: /?q=
    search_target = base + '/?q={search_term_string}'
    return {
        '@context': 'https://schema.org',
        '@type': 'WebSite',
        'name': name,
        'url': base + '/',
        'inLanguage': 'bs-BA',
        'potentialAction': {
            '@type': 'SearchAction',
            'target': {
                '@type': 'EntryPoint',
                'urlTemplate': search_target,
            },
            'query-input': 'required name=search_term_string',
        },
    }


def product_json_ld(product, *, canonical_url: str, site_settings=None) -> dict:
    """Product + Offer — rich results (cijena, stock, brand)."""
    base = site_url_base()
    images = []
    try:
        if product.prikazna_slika:
            images.append(absolute_url(product.prikazna_slika.url))
    except Exception:
        pass
    try:
        for img in product.dodatne_slike.all()[:5]:
            if img.slika:
                images.append(absolute_url(img.slika.url))
    except Exception:
        pass
    if not images:
        images = [absolute_url('/static/img/placeholder.png')]

    # availability: product or any variation in stock
    in_stock = bool(getattr(product, 'na_stanju', False))
    try:
        if not in_stock:
            in_stock = any(v.na_stanju for v in product.varijacije.all())
    except Exception:
        pass

    price = getattr(product, 'prikazna_cijena', None)
    try:
        price_str = f'{price:.2f}' if price is not None else '0.00'
    except Exception:
        price_str = str(price or '0')

    data: dict[str, Any] = {
        '@context': 'https://schema.org/',
        '@type': 'Product',
        'name': product.naziv,
        'description': product.seo_description,
        'image': images if len(images) > 1 else images[0],
        'url': canonical_url or absolute_url(product.get_absolute_url()),
        'offers': {
            '@type': 'Offer',
            'url': canonical_url or absolute_url(product.get_absolute_url()),
            'priceCurrency': 'BAM',
            'price': price_str,
            'availability': (
                'https://schema.org/InStock'
                if in_stock
                else 'https://schema.org/OutOfStock'
            ),
            'itemCondition': 'https://schema.org/NewCondition',
            'seller': {
                '@type': 'Organization',
                'name': (
                    (getattr(site_settings, 'seo_organizacija_naziv', None) or '').strip()
                    or SITE_BRAND
                ),
            },
        },
    }
    if getattr(product, 'brend', None):
        data['brand'] = {
            '@type': 'Brand',
            'name': product.brend.naziv,
        }
    if getattr(product, 'sifra', None):
        data['sku'] = product.sifra
        data['mpn'] = product.sifra
    barkod = (getattr(product, 'barkod', None) or '').strip()
    if barkod and barkod.isdigit():
        if len(barkod) == 13:
            data['gtin13'] = barkod
        elif len(barkod) == 12:
            data['gtin12'] = barkod
        elif len(barkod) == 8:
            data['gtin8'] = barkod
        else:
            data['gtin'] = barkod
    if getattr(product, 'kategorija', None):
        data['category'] = product.kategorija.naziv
    return data


def breadcrumb_json_ld(items: list[dict[str, str]]) -> dict:
    """
    items: [{'name': '...', 'url': 'https://...'}, ...]
    """
    elements = []
    for i, item in enumerate(items, start=1):
        elements.append({
            '@type': 'ListItem',
            'position': i,
            'name': item['name'],
            'item': item['url'],
        })
    return {
        '@context': 'https://schema.org',
        '@type': 'BreadcrumbList',
        'itemListElement': elements,
    }


def collection_page_json_ld(
    *,
    name: str,
    description: str,
    url: str,
) -> dict:
    return {
        '@context': 'https://schema.org',
        '@type': 'CollectionPage',
        'name': name,
        'description': description or '',
        'url': url,
        'isPartOf': {
            '@type': 'WebSite',
            'name': SITE_BRAND,
            'url': site_url_base() + '/',
        },
    }


# —— Default SEO copy (seed) for key storefront pages ——
PAGE_SEO_DEFAULTS: dict[str, dict[str, str]] = {
    'home': {
        'seo_title': 'Oprema za ribolov | Online shop BiH — opremazaribolov.ba',
        'meta_description': (
            'Online shop opreme za ribolov u BiH: štapovi, mašinice, varalice, najloni i pribor '
            'poznatih brendova. Brza dostava, akcije i stručna podrška — opremazaribolov.ba.'
        ),
        'h1_naslov': 'Oprema za ribolov — online shop',
        'seo_tekst_iznad': '',
        'seo_tekst_ispod': (
            'opremazaribolov.ba je online trgovina ribolovačke opreme za bosanskohercegovačke '
            'ribare. U ponudi su štapovi, mašinice, varalice, najloni, hranilice i pribor '
            'provjerenih brendova. Naručite online — brza dostava širom BiH i savjeti pri kupovini.'
        ),
    },
    'akcija': {
        'seo_title': 'Akcija opreme za ribolov | Snižene cijene — opremazaribolov.ba',
        'meta_description': (
            'Akcijska ponuda ribolovačke opreme: snižene cijene na štapove, mašinice, varalice '
            'i pribor. Iskoristite popuste i brzu dostavu u BiH — opremazaribolov.ba.'
        ),
        'h1_naslov': 'Akcija — snižena oprema za ribolov',
        'seo_tekst_iznad': (
            'Pogledajte aktuelne akcije i snižene cijene. Zalihe su ograničene — naručite na vrijeme.'
        ),
        'seo_tekst_ispod': '',
    },
    'noviteti': {
        'seo_title': 'Noviteti opreme za ribolov | Novo u ponudi — opremazaribolov.ba',
        'meta_description': (
            'Novi artikli u ponudi: najnovija oprema za ribolov, brendovi i modeli. '
            'Otkrijte novitete i naručite online s brzim slanjem u BiH.'
        ),
        'h1_naslov': 'Noviteti — nova oprema za ribolov',
        'seo_tekst_iznad': 'Najnoviji proizvodi u našoj trgovini — redovno dodajemo nove modele i brendove.',
        'seo_tekst_ispod': '',
    },
    'about': {
        'seo_title': 'O nama | opremazaribolov.ba — oprema za ribolov iz prakse',
        'meta_description': (
            'Saznajte ko smo: dugogodišnje iskustvo u ribolovu i opremi, online shop za ribare '
            'u Bosni i Hercegovini. Kvalitet, savjet i pouzdana dostava.'
        ),
        'h1_naslov': 'O nama',
        'seo_tekst_iznad': '',
        'seo_tekst_ispod': '',
    },
    'payment': {
        'seo_title': 'Način plaćanja i dostava | opremazaribolov.ba',
        'meta_description': (
            'Plaćanje pouzećem, brza dostava poštom u roku do 48h i sigurno pakovanje. '
            'Sve o plaćanju i slanju na opremazaribolov.ba.'
        ),
        'h1_naslov': 'Način plaćanja i dostava',
        'seo_tekst_iznad': '',
        'seo_tekst_ispod': '',
    },
    'vlog': {
        'seo_title': 'Blog i vlog o ribolovu | Savjeti — opremazaribolov.ba',
        'meta_description': (
            'Blog i vlog: savjeti, priče i novosti iz svijeta ribolova. '
            'Korisni sadržaji za početnike i iskusne ribare — opremazaribolov.ba.'
        ),
        'h1_naslov': 'Blog i vlog',
        'seo_tekst_iznad': 'Savjeti, priče i novosti iz svijeta ribolova.',
        'seo_tekst_ispod': '',
    },
    'search': {
        'seo_title': 'Pretraga artikala | opremazaribolov.ba',
        'meta_description': 'Pronađite opremu za ribolov po nazivu, brendu ili šifri.',
        'h1_naslov': 'Rezultati pretrage',
        'seo_tekst_iznad': '',
        'seo_tekst_ispod': '',
    },
    'cart': {
        'seo_title': 'Korpa | opremazaribolov.ba',
        'meta_description': 'Pregled artikala u korpi prije narudžbe.',
        'h1_naslov': 'Korpa',
        'seo_tekst_iznad': '',
        'seo_tekst_ispod': '',
    },
    'checkout': {
        'seo_title': 'Narudžba | opremazaribolov.ba',
        'meta_description': 'Završite narudžbu — podaci za dostavu.',
        'h1_naslov': 'Narudžba',
        'seo_tekst_iznad': '',
        'seo_tekst_ispod': '',
    },
}


def apply_page_seo_defaults(only_empty: bool = True) -> int:
    """
    Upiši best-practice SEO defaults u PageSEO.
    only_empty=True: ne prepisuj polja koja su već unesena.
    Vraća broj ažuriranih redova.
    """
    updated = 0
    for key, defaults in PAGE_SEO_DEFAULTS.items():
        obj, _ = PageSEO.objects.get_or_create(page_key=key)
        changed = False
        for field, value in defaults.items():
            if not value:
                continue
            current = (getattr(obj, field, None) or '').strip()
            if only_empty and current:
                continue
            if current != value:
                setattr(obj, field, value)
                changed = True
        if changed:
            obj.save()
            updated += 1
    return updated
