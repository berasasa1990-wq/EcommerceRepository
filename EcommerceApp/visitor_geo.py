import ipaddress
import logging

import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)

GEO_CACHE_TTL = 60 * 60 * 24
GEO_CACHE_MISS_TTL = 60 * 5
BOSNIA_HERZEGOVINA_COUNTRY_CODES = frozenset({'BA'})


def _is_bosnia_herzegovina_country(country_code):
    return (country_code or '').strip().upper() in BOSNIA_HERZEGOVINA_COUNTRY_CODES


def get_client_ip(request):
    cf_ip = (request.META.get('HTTP_CF_CONNECTING_IP') or '').strip()
    if cf_ip:
        return cf_ip
    forwarded = (request.META.get('HTTP_X_FORWARDED_FOR') or '').strip()
    if forwarded:
        return forwarded.split(',')[0].strip()
    real_ip = (request.META.get('HTTP_X_REAL_IP') or '').strip()
    if real_ip:
        return real_ip
    return (request.META.get('REMOTE_ADDR') or '').strip()


def _country_from_headers(request):
    for header in (
        'HTTP_CF_IPCOUNTRY',
        'HTTP_X_COUNTRY_CODE',
        'HTTP_CLOUDFRONT_VIEWER_COUNTRY',
    ):
        country = (request.META.get(header) or '').strip().upper()
        if country and country not in ('', 'XX', 'T1'):
            return country
    return ''


def _city_from_headers(request):
    if not _is_bosnia_herzegovina_country(_country_from_headers(request)):
        return ''
    for header in ('HTTP_CF_IPCITY', 'HTTP_X_CITY'):
        city = (request.META.get(header) or '').strip()
        if city and city.lower() not in ('', 'unknown'):
            return city
    return ''


def _is_public_ip(ip):
    if not ip:
        return False
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_reserved
    )


def _lookup_geo_api(ip):
    cache_key = f'visitor_geo:bih:v4:{ip}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    result = {'country': '', 'city': ''}

    providers = (
        lambda: _fetch_ipwho_is(ip),
        lambda: _fetch_ipapi_co(ip),
        lambda: _fetch_ip_api_com(ip),
    )
    for fetch in providers:
        try:
            fetched = fetch()
        except Exception:
            logger.debug('Geo provider failed for %s', ip, exc_info=True)
            continue
        if fetched.get('country'):
            result = fetched
            break

    cache_ttl = GEO_CACHE_TTL if result['country'] else GEO_CACHE_MISS_TTL
    cache.set(cache_key, result, cache_ttl)
    return result


def _normalize_geo_result(country_code, city=''):
    country_code = (country_code or '').strip().upper()
    city = (city or '').strip()
    if not _is_bosnia_herzegovina_country(country_code):
        city = ''
    return {'country': country_code, 'city': city}


def _fetch_ipwho_is(ip):
    response = requests.get(f'https://ipwho.is/{ip}', timeout=3)
    response.raise_for_status()
    data = response.json()
    if not data.get('success'):
        return {'country': '', 'city': ''}
    return _normalize_geo_result(data.get('country_code'), data.get('city'))


def _fetch_ipapi_co(ip):
    response = requests.get(f'https://ipapi.co/{ip}/json/', timeout=3)
    response.raise_for_status()
    data = response.json()
    if data.get('error'):
        return {'country': '', 'city': ''}
    return _normalize_geo_result(data.get('country_code'), data.get('city'))


def _fetch_ip_api_com(ip):
    response = requests.get(
        f'http://ip-api.com/json/{ip}',
        params={'fields': 'status,city,countryCode'},
        timeout=3,
    )
    response.raise_for_status()
    data = response.json()
    if data.get('status') != 'success':
        return {'country': '', 'city': ''}
    return _normalize_geo_result(data.get('countryCode'), data.get('city'))


def resolve_visitor_country(request, *, ip=None):
    country = _country_from_headers(request)
    if country:
        return country

    ip = ip or get_client_ip(request)
    if not _is_public_ip(ip):
        return ''
    return _lookup_geo_api(ip)['country']


def is_known_foreign_visitor(request, *, ip=None):
    country = resolve_visitor_country(request, ip=ip)
    return bool(country) and not _is_bosnia_herzegovina_country(country)


def is_visitor_from_bosnia_herzegovina(request, *, ip=None):
    return not is_known_foreign_visitor(request, ip=ip)


def resolve_visitor_city(request, *, ip=None):
    header_city = _city_from_headers(request)
    if header_city:
        return header_city

    ip = ip or get_client_ip(request)
    if not _is_public_ip(ip):
        return ''
    return _lookup_geo_api(ip)['city']