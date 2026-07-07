import ipaddress
import logging

import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)

GEO_CACHE_TTL = 60 * 60 * 24


def get_client_ip(request):
    cf_ip = (request.META.get('HTTP_CF_CONNECTING_IP') or '').strip()
    if cf_ip:
        return cf_ip
    forwarded = (request.META.get('HTTP_X_FORWARDED_FOR') or '').strip()
    if forwarded:
        return forwarded.split(',')[0].strip()
    return (request.META.get('REMOTE_ADDR') or '').strip()


def _city_from_headers(request):
    city = (request.META.get('HTTP_CF_IPCITY') or '').strip()
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


def _lookup_city_api(ip):
    cache_key = f'visitor_geo_city:{ip}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    city = ''
    try:
        response = requests.get(
            f'http://ip-api.com/json/{ip}',
            params={'fields': 'status,city'},
            timeout=1.5,
        )
        response.raise_for_status()
        data = response.json()
        if data.get('status') == 'success':
            city = (data.get('city') or '').strip()
    except Exception:
        logger.debug('Geo lookup failed for %s', ip, exc_info=True)

    cache.set(cache_key, city, GEO_CACHE_TTL)
    return city


def resolve_visitor_city(request, *, ip=None):
    header_city = _city_from_headers(request)
    if header_city:
        return header_city

    ip = ip or get_client_ip(request)
    if not _is_public_ip(ip):
        return ''
    return _lookup_city_api(ip)