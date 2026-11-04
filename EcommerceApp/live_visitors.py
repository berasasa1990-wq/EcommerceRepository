import calendar
import logging
import random
import re
from collections import Counter
from datetime import datetime, time, timedelta

from django.db.models import Count
from django.utils import timezone

logger = logging.getLogger(__name__)

from .models import ActiveCartItem, Category, LiveVisitor, Product
from .visitor_geo import (
    get_client_ip,
    is_known_foreign_visitor,
    resolve_visitor_city,
    resolve_visitor_country,
)

# „Trenutno na sajtu” — aktivnost u zadnjoj minuti; stariji idu u „zadnjih 30 min”
ONLINE_MINUTES = 1
# Staff toast: kraći prozor + heartbeat/leave, da se popup skloni čim kupac ode
STAFF_TOAST_ONLINE_SECONDS = 30
# Staff toast „Kupac na sajtu” — odmah (0 = čim se pojavi LiveVisitor red)
ONLINE_NOTIFY_AFTER_SECONDS = 0
ONLINE_NOTIFIED_CACHE_PREFIX = 'live_visitor_online_notified:'
ONLINE_NOTIFIED_CACHE_TTL = 48 * 3600
PRESENCE_CACHE_PREFIX = 'live_visitor_presence:'
LEFT_CACHE_PREFIX = 'live_visitor_left:'
PRESENCE_CACHE_TTL = STAFF_TOAST_ONLINE_SECONDS + 20
LEFT_CACHE_TTL = 300
WINDOW_MINUTES = 30
RETENTION_HOURS = 48
BOSNIA_HERZEGOVINA_COUNTRY_CODE = 'BA'
MAX_TRACKED_CATEGORIES = 8
MAX_TRACKED_PRODUCTS = 12

# Ključevi izvora (max ~20 znakova u starijim DB; migracija širi na 32)
SOURCE_DIRECT = 'direct'
SOURCE_GOOGLE = 'google'              # organska Google pretraga (legacy key)
SOURCE_GOOGLE_ADS = 'google_ads'
SOURCE_FACEBOOK = 'facebook'          # organski Facebook
SOURCE_FACEBOOK_ADS = 'facebook_ads'
SOURCE_INSTAGRAM = 'instagram'        # organski Instagram
SOURCE_INSTAGRAM_ADS = 'instagram_ads'
SOURCE_EMAIL = 'email'
SOURCE_YOUTUBE = 'youtube'
SOURCE_OTHER = 'other'

SOURCE_LABELS = {
    SOURCE_DIRECT: 'Direktno (ukucan sajt)',
    SOURCE_GOOGLE: 'Google pretraga',
    SOURCE_GOOGLE_ADS: 'Google Ads',
    SOURCE_FACEBOOK: 'Facebook (organski)',
    SOURCE_FACEBOOK_ADS: 'Facebook Ads',
    SOURCE_INSTAGRAM: 'Instagram (organski)',
    SOURCE_INSTAGRAM_ADS: 'Instagram Ads',
    SOURCE_EMAIL: 'Email',
    SOURCE_YOUTUBE: 'YouTube',
    SOURCE_OTHER: 'Ostalo',
}

# Redoslijed prikaza u analitici
SOURCE_DISPLAY_ORDER = (
    SOURCE_DIRECT,
    SOURCE_GOOGLE,
    SOURCE_GOOGLE_ADS,
    SOURCE_FACEBOOK,
    SOURCE_FACEBOOK_ADS,
    SOURCE_INSTAGRAM,
    SOURCE_INSTAGRAM_ADS,
    SOURCE_EMAIL,
    SOURCE_YOUTUBE,
    SOURCE_OTHER,
)

# Kratke oznake za chipove / uski UI
SOURCE_SHORT_LABELS = {
    SOURCE_DIRECT: 'Direktno',
    SOURCE_GOOGLE: 'Google',
    SOURCE_GOOGLE_ADS: 'Google Ads',
    SOURCE_FACEBOOK: 'Facebook',
    SOURCE_FACEBOOK_ADS: 'FB Ads',
    SOURCE_INSTAGRAM: 'Instagram',
    SOURCE_INSTAGRAM_ADS: 'IG Ads',
    SOURCE_EMAIL: 'Email',
    SOURCE_YOUTUBE: 'YouTube',
    SOURCE_OTHER: 'Ostalo',
}

_PAID_MEDIUMS = frozenset({
    'cpc', 'ppc', 'paid', 'paid_social', 'paidsocial', 'paid-social',
    'cpm', 'cpa', 'display', 'retargeting', 'remarketing', 'ads', 'ad',
    'performance_max', 'pmax',
})


def normalize_traffic_source(raw) -> str:
    """Mapiraj sirovi string na poznati ključ izvora."""
    key = (raw or '').strip().lower()
    if not key:
        return SOURCE_DIRECT
    if key in SOURCE_LABELS:
        return key
    # sinonimi / legacy
    aliases = {
        'fb': SOURCE_FACEBOOK,
        'meta': SOURCE_FACEBOOK,
        'fb_ad': SOURCE_FACEBOOK_ADS,
        'fbads': SOURCE_FACEBOOK_ADS,
        'facebook_ad': SOURCE_FACEBOOK_ADS,
        'ig': SOURCE_INSTAGRAM,
        'ig_ad': SOURCE_INSTAGRAM_ADS,
        'igads': SOURCE_INSTAGRAM_ADS,
        'instagram_ad': SOURCE_INSTAGRAM_ADS,
        'gads': SOURCE_GOOGLE_ADS,
        'adwords': SOURCE_GOOGLE_ADS,
        'googleads': SOURCE_GOOGLE_ADS,
        'google_ad': SOURCE_GOOGLE_ADS,
        'organic': SOURCE_GOOGLE,
        'seo': SOURCE_GOOGLE,
        'newsletter': SOURCE_EMAIL,
        'mail': SOURCE_EMAIL,
        'e-mail': SOURCE_EMAIL,
        'yt': SOURCE_YOUTUBE,
        'none': SOURCE_DIRECT,
        'typed': SOURCE_DIRECT,
    }
    if key in aliases:
        return aliases[key]
    return SOURCE_OTHER


def traffic_source_label(raw, *, short=False) -> str:
    key = normalize_traffic_source(raw)
    if short:
        return SOURCE_SHORT_LABELS.get(key, SOURCE_SHORT_LABELS[SOURCE_OTHER])
    return SOURCE_LABELS.get(key, SOURCE_LABELS[SOURCE_OTHER])


def detect_visitor_device(request) -> str:
    """Svedi user-agent na dvije jasne kategorije za live statistiku."""
    user_agent = (request.META.get('HTTP_USER_AGENT') or '').lower()
    mobile_markers = ('mobile', 'android', 'iphone', 'ipod', 'ipad', 'tablet', 'windows phone')
    return 'mobile' if any(marker in user_agent for marker in mobile_markers) else 'desktop'


def _display_name(user):
    if not user or not user.is_authenticated:
        return 'Gost'
    full = (user.get_full_name() or '').strip()
    if full:
        return full
    first = (user.first_name or '').strip()
    if first:
        return first
    email = (user.email or '').strip()
    if email:
        return email.split('@', 1)[0]
    return 'Registrovan korisnik'


def _display_email(user):
    if user and user.is_authenticated:
        return (user.email or '').strip()
    return ''


def _product_from_request(request):
    path = (request.path or '').rstrip('/') or '/'
    product_match = re.match(r'^/artikal/([^/]+)$', path)
    if not product_match:
        return None
    return Product.objects.filter(
        slug=product_match.group(1),
        aktivan=True,
    ).select_related('kategorija').only('pk', 'naziv', 'kategorija__naziv').first()


def _resolve_current_page(path='', *, query='', product=None):
    """
    Trenutna lokacija kupca — putanja + čitljiv label za staff „Sada:”.
    """
    raw_path = (path or '').strip() or '/'
    # Zadrži query u putanji samo ako treba prikazati pretragu
    path_only = raw_path.split('?', 1)[0]
    path_only = path_only.rstrip('/') or '/'
    q = (query or '').strip()[:80]

    # Poll/API putanje nisu „stranice” — ne prikazuj u Sada
    if is_background_request_path(path_only) or is_background_request_path(path_only + '/'):
        return '', ''

    # Fiksne stranice
    static_labels = {
        '/': 'Početna',
        '/korpa': 'Korpa',
        '/narudzba': 'Checkout / narudžba',
        '/checkout': 'Checkout / narudžba',
        '/registracija': 'Registracija',
        '/prijava': 'Prijava',
        '/nalog': 'Moj nalog',
        '/o-nama': 'O nama',
        '/nacin-placanja': 'Način plaćanja',
        '/vlog': 'Vlog',
    }
    if path_only in static_labels and not q:
        return path_only[:300], static_labels[path_only]
    if path_only in ('', '/') and q:
        return f'/?q={q}'[:300], f'Pretraga: {q[:48]}'

    product_match = re.match(r'^/artikal/([^/]+)$', path_only)
    if product_match:
        if product is None:
            product = Product.objects.filter(
                slug=product_match.group(1),
                aktivan=True,
            ).only('pk', 'naziv').first()
        name = (product.naziv if product else '') or product_match.group(1).replace('-', ' ')
        return path_only[:300], f'Artikal: {name}'[:200]

    category_match = re.match(r'^/kategorija/([^/]+)$', path_only)
    if category_match:
        category = Category.objects.filter(
            slug=category_match.group(1),
            aktivan=True,
        ).only('naziv').first()
        name = (category.naziv if category else '') or category_match.group(1).replace('-', ' ')
        return path_only[:300], f'Kategorija: {name}'[:200]

    vlog_match = re.match(r'^/vlog/([^/]+)$', path_only)
    if vlog_match:
        return path_only[:300], f'Vlog: {vlog_match.group(1).replace("-", " ")}'[:200]

    if path_only.startswith('/nalog'):
        return path_only[:300], 'Moj nalog'

    # Fallback — skraćena putanja
    label = path_only if path_only != '/' else 'Početna'
    if q:
        label = f'{label} · pretraga: {q[:32]}'
    return path_only[:300], label[:200]


def _current_page_from_request(request, product=None):
    path = getattr(request, 'path', '') or '/'
    query = ''
    try:
        query = (request.GET.get('q') or '').strip()
    except Exception:
        query = ''
    return _resolve_current_page(path, query=query, product=product)


def _product_from_path(path):
    """Product sa putanje /artikal/<slug> — za live „Sada” ponudu %."""
    path_only = ((path or '').strip().split('?', 1)[0]).rstrip('/') or '/'
    product_match = re.match(r'^/artikal/([^/]+)$', path_only)
    if not product_match:
        return None
    return Product.objects.filter(
        slug=product_match.group(1),
        aktivan=True,
    ).only('pk', 'naziv').first()


def _category_names_from_request(request, product=None):
    path = (request.path or '').rstrip('/') or '/'

    category_match = re.match(r'^/kategorija/([^/]+)$', path)
    if category_match:
        category = Category.objects.filter(
            slug=category_match.group(1),
            aktivan=True,
        ).only('naziv').first()
        if category:
            return [category.naziv]

    if product is None:
        product = _product_from_request(request)
    if product and product.kategorija_id and product.kategorija:
        return [product.kategorija.naziv]

    if path in ('', '/'):
        query = (request.GET.get('q') or '').strip()
        if query:
            return [f'Pretraga: {query[:48]}']

    return []


def _normalize_category_views(raw_categories):
    """
    Normalizuj istoriju kategorija.
    Podržava legacy listu stringova i novi format {naziv, views}.
    """
    history = []
    for item in (raw_categories or []):
        if isinstance(item, dict):
            name = str(item.get('naziv') or item.get('name') or '').strip()
            if not name:
                continue
            try:
                views = max(1, int(item.get('views') or 1))
            except (TypeError, ValueError):
                views = 1
            history.append({'naziv': name[:120], 'views': views})
        else:
            name = str(item or '').strip()
            if name:
                history.append({'naziv': name[:120], 'views': 1})
    return history


def _category_names_only(raw_categories):
    """Lista naziva (za staff UI / labele) — najnovije prvo."""
    return [c['naziv'] for c in _normalize_category_views(raw_categories) if c.get('naziv')]


def _merge_category_history(existing, new_items):
    """Ažuriraj listu pregledanih kategorija; povećaj views pri povratku na istu."""
    history = _normalize_category_views(existing)
    for item in new_items:
        name = (item or '').strip()[:120]
        if not name:
            continue
        matched = None
        for entry in history:
            if entry['naziv'] == name:
                matched = entry
                break
        if matched:
            matched['views'] = matched.get('views', 1) + 1
            history.remove(matched)
            history.insert(0, matched)
        else:
            history.insert(0, {'naziv': name, 'views': 1})
    return history[:MAX_TRACKED_CATEGORIES]


def _merge_product_history(existing, product):
    """Ažuriraj listu pregledanih proizvoda; povećaj views pri povratku na isti."""
    if not product:
        return list(existing or [])
    history = []
    for item in (existing or []):
        if not isinstance(item, dict):
            continue
        try:
            pid = int(item.get('id') or 0)
        except (TypeError, ValueError):
            continue
        if not pid:
            continue
        history.append({
            'id': pid,
            'naziv': str(item.get('naziv') or '')[:120],
            'views': max(1, int(item.get('views') or 1)),
        })

    matched = None
    for entry in history:
        if entry['id'] == product.pk:
            matched = entry
            break
    if matched:
        matched['views'] = matched.get('views', 1) + 1
        matched['naziv'] = (product.naziv or matched['naziv'])[:120]
        history.remove(matched)
        history.insert(0, matched)
    else:
        history.insert(0, {
            'id': product.pk,
            'naziv': (product.naziv or '')[:120],
            'views': 1,
        })
    return history[:MAX_TRACKED_PRODUCTS]


def detect_traffic_source(request):
    """
    Izvor dolaska s razlikom organsko vs ads:
      direct | google | google_ads | facebook | facebook_ads |
      instagram | instagram_ads | email | youtube | other

    Prioritet: click-id / UTM → HTTP Referer → direktno (ukucan URL).
    """
    get = request.GET
    utm_source = (get.get('utm_source') or get.get('source') or '').strip().lower()
    utm_medium = (get.get('utm_medium') or '').strip().lower()
    utm_campaign = (get.get('utm_campaign') or '').strip().lower()
    utm_content = (get.get('utm_content') or '').strip().lower()
    utm_blob = f'{utm_source} {utm_medium} {utm_campaign} {utm_content}'.strip()

    is_paid_medium = (
        utm_medium in _PAID_MEDIUMS
        or any(token in utm_medium for token in ('paid', 'cpc', 'ppc', 'cpm', 'ads'))
        or utm_source in (
            'fbads', 'facebook_ads', 'fb_ad', 'igads', 'instagram_ads',
            'googleads', 'google_ads', 'adwords', 'gads',
        )
    )

    has_gclid = bool(get.get('gclid') or get.get('gbraid') or get.get('wbraid'))
    has_fbclid = bool(get.get('fbclid'))
    has_igshid = bool(get.get('igshid'))
    has_msclkid = bool(get.get('msclkid'))
    has_ttclid = bool(get.get('ttclid'))

    if utm_source in ('email', 'newsletter', 'mail', 'e-mail') or utm_medium in ('email', 'newsletter'):
        return SOURCE_EMAIL

    def _is_ig_signal(text: str) -> bool:
        t = text or ''
        return (
            'instagram' in t
            or t in ('ig', 'ig_ad', 'igads', 'instagram_ads')
            or 'ig_' in t
        )

    def _is_fb_signal(text: str) -> bool:
        t = text or ''
        return (
            'facebook' in t
            or 'meta' in t
            or t in ('fb', 'fb_ad', 'fbads', 'facebook_ads')
            or t.startswith('fb')
        )

    def _is_google_signal(text: str) -> bool:
        t = text or ''
        return (
            'google' in t
            or t in ('adwords', 'gads', 'googleads', 'google_ads', 'youtube')
        )

    def _is_youtube_signal(text: str) -> bool:
        t = text or ''
        return 'youtube' in t or 'youtu.be' in t or t in ('yt', 'youtube')

    # --- Click IDs (najjači signal za plaćene kanale) ---
    if has_gclid:
        return SOURCE_GOOGLE_ADS
    if has_fbclid:
        if _is_ig_signal(utm_blob) or _is_ig_signal(utm_source):
            return SOURCE_INSTAGRAM_ADS
        return SOURCE_FACEBOOK_ADS
    if has_igshid and is_paid_medium:
        return SOURCE_INSTAGRAM_ADS
    if has_msclkid or has_ttclid:
        return SOURCE_OTHER

    # --- UTM ---
    if utm_source or utm_medium:
        if _is_youtube_signal(utm_source) or _is_youtube_signal(utm_blob):
            return SOURCE_YOUTUBE
        if _is_ig_signal(utm_source) or _is_ig_signal(utm_blob):
            return SOURCE_INSTAGRAM_ADS if is_paid_medium else SOURCE_INSTAGRAM
        if _is_fb_signal(utm_source) or _is_fb_signal(utm_blob):
            return SOURCE_FACEBOOK_ADS if is_paid_medium else SOURCE_FACEBOOK
        if _is_google_signal(utm_source) or _is_google_signal(utm_blob):
            return SOURCE_GOOGLE_ADS if is_paid_medium else SOURCE_GOOGLE
        # cpc bez jasnog source-a često Google Ads
        if utm_medium in ('cpc', 'ppc', 'paid') and not utm_source:
            return SOURCE_GOOGLE_ADS
        if is_paid_medium:
            return SOURCE_OTHER
        if utm_source:
            return SOURCE_OTHER

    # --- HTTP Referer (organski / vanjski link) ---
    referer = (request.META.get('HTTP_REFERER') or '').strip().lower()
    if referer:
        if (
            'facebook.com' in referer
            or 'fb.com' in referer
            or 'fb.me' in referer
            or 'l.facebook' in referer
            or 'lm.facebook' in referer
            or 'm.facebook' in referer
        ):
            return SOURCE_FACEBOOK
        if 'instagram.com' in referer or 'l.instagram' in referer:
            return SOURCE_INSTAGRAM
        if 'youtube.com' in referer or 'youtu.be' in referer:
            return SOURCE_YOUTUBE
        if (
            'google.' in referer
            or 'googleusercontent' in referer
            or 'googleapis' in referer
            or 'ggpht.com' in referer
        ):
            # Organska Google pretraga (bez gclid = nije Ads click)
            return SOURCE_GOOGLE
        try:
            from urllib.parse import urlparse
            host = (urlparse(referer).netloc or '').lower()
            site_host = (request.get_host() or '').lower().split(':')[0]
            if host and site_host and host != site_host and not host.endswith('.' + site_host):
                return SOURCE_OTHER
        except Exception:
            pass

    # Nema referera ni UTM → korisnik je ukucao sajt / bookmark / app
    return SOURCE_DIRECT


# AJAX / poll endpointi — ne smiju prepisati „Sada:” (trenutna stranica kupca)
BACKGROUND_PATH_PREFIXES = (
    '/online-nagrada/',
    '/ponuda/',
    '/preporuka/',
    '/uzivo/',
    '/korpa/podsjetnik',
    '/korpa/podsjetnik-exit',
    '/korpa/exit/',
    '/korpa/azuriraj',
    '/korpa/kupon',
    '/korpa/ukloni',
    '/api/',
)


def is_background_request_path(path):
    """Pozadinski endpoint (poll, heartbeat, dodaj u korpu…) — nije stvarna stranica."""
    path = (path or '').strip()
    if not path:
        return False
    return any(path.startswith(prefix) for prefix in BACKGROUND_PATH_PREFIXES)


OWNER_IP_CACHE_PREFIX = 'live_visitor_owner_ip:'
OWNER_IP_CACHE_TTL = 90 * 24 * 3600  # 90 dana


def _normalize_ip(ip: str) -> str:
    ip = (ip or '').strip()
    if not ip:
        return ''
    # IPv4-mapped IPv6 → IPv4
    if ip.startswith('::ffff:'):
        ip = ip[7:]
    # Hostname nije IP — mapiraj na loopback (ne šalji "localhost" u Postgres inet)
    if ip.lower() in ('localhost', 'localhost.', 'ip6-localhost'):
        return '127.0.0.1'
    return ip


def _is_valid_db_ip(ip: str) -> bool:
    """True samo za vrijednosti koje Postgres GenericIPAddressField (inet) prihvata."""
    ip = _normalize_ip(ip)
    if not ip:
        return False
    try:
        import ipaddress
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def get_configured_exclude_ips() -> set[str]:
    """
    IP-ovi iz settings (LIVE_VISITOR_EXCLUDE_IPS) + loopback.
    Vraća samo validne IP adrese (bez hostnamenova tipa „localhost”).
    """
    from django.conf import settings

    raw = getattr(settings, 'LIVE_VISITOR_EXCLUDE_IPS', ()) or ()
    ips = set()
    for item in raw:
        ip = _normalize_ip(item)
        if _is_valid_db_ip(ip):
            ips.add(ip)
    ips.update({'127.0.0.1', '::1'})
    return ips


def remember_owner_ip(request):
    """Live Visitors is retired; preserve this call signature without tracking side effects."""
    return ''


def visitors_analytics_qs():
    """
    LiveVisitor queryset za analitiku — bez staff naloga i isključenih IP-ova.
    (Cache owner IP-ovi se ne filtriraju ovdje; već se ne evidentiraju.)
    """
    qs = LiveVisitor.objects.all()
    qs = qs.exclude(user__is_staff=True).exclude(user__is_superuser=True)
    # Samo validni IP-ovi — Postgres inet odbija "localhost" i slične hostnamove
    exclude_ips = [ip for ip in get_configured_exclude_ips() if _is_valid_db_ip(ip)]
    if exclude_ips:
        qs = qs.exclude(ip_adresa__in=exclude_ips)
    return qs


def is_excluded_visitor_ip(ip: str) -> bool:
    """True ako se IP ne smije evidentirati kao posjet / izvor dolaska."""
    ip = _normalize_ip(ip)
    if not ip:
        return False
    if ip in get_configured_exclude_ips():
        return True
    try:
        from django.core.cache import cache
        if cache.get(f'{OWNER_IP_CACHE_PREFIX}{ip}'):
            return True
    except Exception:
        pass
    # Privatni / loopback (lokalni dev, LAN ured) — ne broji kao „kupac”
    try:
        import ipaddress
        parsed = ipaddress.ip_address(ip)
        if parsed.is_loopback or parsed.is_link_local:
            return True
    except ValueError:
        # Nevalidan IP / hostname — ne evidentiraj kao posjet
        return True
    return False


# User-Agent markeri botova / scrapera — ne bilježi se u LiveVisitor
_BOT_UA_MARKERS = (
    'bot', 'spider', 'crawler', 'slurp', 'scrapy', 'curl/', 'wget/',
    'python-requests', 'python-urllib', 'go-http-client', 'httpclient',
    'libwww', 'java/', 'apache-http', 'okhttp', 'headless', 'phantom',
    'selenium', 'puppeteer', 'playwright',
    'googlebot', 'bingbot', 'yandex', 'baiduspider', 'duckduckbot',
    'facebookexternalhit', 'facebot', 'meta-externalagent', 'twitterbot',
    'linkedinbot', 'pinterest', 'whatsapp', 'telegrambot', 'discordbot',
    'slackbot', 'applebot', 'semrush', 'ahrefs', 'mj12bot', 'dotbot',
    'petalbot', 'bytespider', 'gptbot', 'chatgpt', 'claudebot', 'anthropic',
    'amazonbot', 'ia_archiver', 'archive.org', 'screaming frog',
    'lighthouse', 'gtmetrix', 'pingdom', 'uptime', 'statuscake',
    'render', 'better uptime', 'hetzner', 'monitor',
    'python-httpx', 'aiohttp', 'scrape', 'http.rb', 'zgrab', 'masscan',
    'nuclei', 'sqlmap', 'nikto', 'censys', 'netcraft', 'got/', 'axios/',
)


def is_bot_user_agent(ua: str) -> bool:
    """True za poznate crawler/bot User-Agent stringove."""
    ua = (ua or '').strip().lower()
    if not ua or ua in ('-', 'null', 'undefined'):
        return True
    return any(m in ua for m in _BOT_UA_MARKERS)



def is_non_visitor_path(path):
    """CMS probes and assets must never appear as customer pages."""
    path = (path or '').split('?', 1)[0].lower()
    return (
        path.startswith(('/wp-', '/wordpress/', '/xmlrpc.php', '/.env', '/.git', '/phpmyadmin'))
        or path.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.ico',
                          '.css', '.js', '.map', '.woff', '.woff2', '.ttf', '.xml', '.txt'))
    )

def should_track_visitor(request):
    """Live Visitors is retired; preserve this call signature without tracking side effects."""
    return False


def _ensure_session_key(request):
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key or ''


def _presence_cache_key(session_key):
    return f'{PRESENCE_CACHE_PREFIX}{session_key}'


def _left_cache_key(session_key):
    return f'{LEFT_CACHE_PREFIX}{session_key}'


def touch_visitor_presence(session_key):
    """Live Visitors is retired; preserve this call signature without tracking side effects."""
    return None


def clear_visitor_presence(session_key):
    """Live Visitors is retired; preserve this call signature without tracking side effects."""
    return None


def is_visitor_marked_left(session_key):
    if not session_key:
        return False
    from django.core.cache import cache

    return bool(cache.get(_left_cache_key(session_key)))


def resolve_presence_session_key(request, body_session_key=''):
    """
    Session key za presence: cookie sesija ima prioritet.
    body_session_key se prihvata samo ako se poklapa sa cookie sesijom
    (ili ako cookie sesije nema — fallback za beacon edge case).
    """
    cookie_key = ''
    try:
        cookie_key = (getattr(request.session, 'session_key', None) or '').strip()
    except Exception:
        cookie_key = ''
    body_key = (body_session_key or '').strip()[:40]
    if cookie_key and body_key and cookie_key != body_key:
        # Ne dozvoli tuđu sesiju preko body-ja
        return cookie_key
    return cookie_key or body_key


def maybe_notify_visitor_online(session_key):
    """Live Visitors is retired; preserve this call signature without tracking side effects."""
    return False


def heartbeat_live_visitor(request, body_session_key=''):
    """Live Visitors is retired; preserve this call signature without tracking side effects."""
    return False


def _parse_leave_at_seconds(request):
    """Client leave_at u ms (Date.now()) → sekunde; None ako nema."""
    raw = None
    try:
        raw = request.POST.get('leave_at') or request.GET.get('leave_at')
    except Exception:
        raw = None
    if raw is None and getattr(request, 'body', None):
        try:
            from urllib.parse import parse_qs

            parsed = parse_qs(request.body.decode('utf-8', errors='ignore'))
            vals = parsed.get('leave_at') or []
            if vals:
                raw = vals[0]
        except Exception:
            raw = None
    if raw is None or raw == '':
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    # ms → s (Date.now() je ~1.7e12)
    if val > 1e12:
        val = val / 1000.0
    return val


def mark_live_visitor_left(request, body_session_key=''):
    """Live Visitors is retired; preserve this call signature without tracking side effects."""
    return False


VISITOR_COOKIE = 'ozb_vid'
VISITOR_COOKIE_MAX_AGE = 60 * 60 * 24 * 400  # ~13 mjeseci


def _touch_visitor_identity(request, session_key):
    """Live Visitors is retired; preserve this call signature without tracking side effects."""
    return None


def track_live_visitor(request):
    """Live Visitors is retired; preserve this call signature without tracking side effects."""
    return None

def cleanup_stale_live_visitors():
    # Retain historical records until explicitly deleted by the owner.
    return 0


def public_online_visitors_payload(limit=24):
    """Live Visitors is retired; preserve this call signature without tracking side effects."""
    return {'ok': True, 'disabled': True, 'count': 0, 'items': []}


def _ago_action_label(dt, now):
    if not dt:
        return ''
    seconds = max(0, int((now - dt).total_seconds()))
    if seconds < 60:
        return 'upravo sada'
    if seconds < 3600:
        return f'prije {seconds // 60} min'
    if seconds < 86400:
        return f'prije {seconds // 3600} h'
    return f'prije {seconds // 86400} d'


def _build_recent_offer_map(visitors, *, now):
    """
    Mapa posljednjih staff akcija po sesiji/useru:
    - offer_map[(session|user, id)] → najnovija non-registracija ponuda (za legacy chip)
    - offers_by_key[(session|user, id)] → {tip: offer} najnoviji po tipu
    - gift_push_by_session[session]
    - gift_claim_by_session[session] / gift_claim_by_user[user]
    """
    from django.db.models import Q

    from .models import LiveVisitorOffer

    session_keys = [visitor.session_key for visitor in visitors if visitor.session_key]
    user_ids = [visitor.user_id for visitor in visitors if visitor.user_id]
    empty = {
        'offer_map': {},
        'offers_by_key': {},
        'offers_list_by_session': {},
        'offers_list_by_user': {},
    }
    if not session_keys and not user_ids:
        return empty

    offer_cutoff = now - timedelta(minutes=WINDOW_MINUTES)
    clauses = Q()
    if session_keys:
        clauses |= Q(session_key__in=session_keys)
    if user_ids:
        clauses |= Q(user_id__in=user_ids)

    offers = (
        LiveVisitorOffer.objects.filter(clauses, azurirano__gte=offer_cutoff)
        .select_related('product')
        .order_by('-azurirano')
    )

    from collections import defaultdict

    offer_map = {}
    offers_by_key = {}
    offers_list_by_session = defaultdict(list)
    offers_list_by_user = defaultdict(list)
    for offer in offers:
        keys = []
        if offer.user_id:
            keys.append(('user', offer.user_id))
            offers_list_by_user[offer.user_id].append(offer)
        if offer.session_key:
            keys.append(('session', offer.session_key))
            offers_list_by_session[offer.session_key].append(offer)
        is_auto = False
        is_auto = (getattr(offer, 'aktivacioni_kod', '') or '').startswith(('AUTO-BROWSE', 'AI-PRODAJA'))
        for key in keys:
            bucket = offers_by_key.setdefault(key, {})
            # Auto preporuka posebno (zeleni/crveni krug) — ne gazi staff artikal-ponudu
            if is_auto:
                if 'auto_browse' not in bucket:
                    bucket['auto_browse'] = offer
            elif offer.tip not in bucket:
                bucket[offer.tip] = offer
            # Legacy chip: prefer staff, ali auto ako nema drugog
            if key not in offer_map and offer.tip != LiveVisitorOffer.Tip.REGISTRACIJA:
                offer_map[key] = offer
            elif key not in offer_map and offer.tip == LiveVisitorOffer.Tip.REGISTRACIJA:
                pass
        for key in keys:
            if key not in offer_map:
                offer_map[key] = offer

    return {
        'offer_map': offer_map,
        'offers_by_key': offers_by_key,
        'offers_list_by_session': dict(offers_list_by_session),
        'offers_list_by_user': dict(offers_list_by_user),
    }


def _lookup_recent_offer(actions_bundle, visitor):
    offer_map = actions_bundle.get('offer_map') or {}
    if visitor.user_id:
        offer = offer_map.get(('user', visitor.user_id))
        if offer:
            return offer
    return offer_map.get(('session', visitor.session_key))


def _lookup_offers_by_tip(actions_bundle, visitor):
    offers_by_key = actions_bundle.get('offers_by_key') or {}
    merged = {}
    if visitor.session_key:
        merged.update(offers_by_key.get(('session', visitor.session_key)) or {})
    if visitor.user_id:
        # user-level wins for same tip if newer
        for tip, offer in (offers_by_key.get(('user', visitor.user_id)) or {}).items():
            existing = merged.get(tip)
            if not existing or (offer.azurirano and existing.azurirano and offer.azurirano > existing.azurirano):
                merged[tip] = offer
    return merged


def _offer_status_fields(offer, *, visitor_online=False):
    if not offer:
        return {
            'offer_sent': False,
            'offer_active': False,
            'offer_product': '',
            'offer_product_id': None,
            'offer_status': '',
            'offer_status_label': '',
            'offer_discount_label': '',
            'offer_kind': '',
            'offer_kind_label': '',
        }

    from .models import LiveVisitorOffer

    product_id = None
    discount_label = ''
    if offer.tip == LiveVisitorOffer.Tip.REGISTRACIJA:
        accepted = bool(offer.kod_aktiviran)
        active = bool(offer.show_popup) and not accepted
        product_name = 'Poziv na registraciju'
        kind = 'register'
        kind_label = 'Registracija'
        if accepted:
            status = 'accepted'
            status_label = 'Registrovao se'
        elif active and visitor_online:
            status = 'active'
            status_label = 'Poziv poslan — čeka'
        elif active:
            status = 'active'
            status_label = 'Poziv poslan'
        else:
            status = 'left'
            status_label = 'Odbio / zatvorio poziv'
    elif offer.tip == LiveVisitorOffer.Tip.NARUDZBA:
        accepted = bool(offer.kod_aktiviran)
        active = offer.show_popup and not accepted
        pct = offer.discount_percent or 0
        pct_label = int(pct) if pct == int(pct) else pct
        product_name = f'Popust {pct_label}% na narudžbu'
        discount_label = f'{pct_label}%'
        kind = 'discount'
        kind_label = 'Popust na narudžbu'
        if offer.aktivacioni_kod:
            product_name = f'{product_name} ({offer.aktivacioni_kod})'
        if offer.besplatna_dostava:
            product_name = f'{product_name} + gratis dostava'
        if accepted:
            status = 'accepted'
            status_label = 'Prihvatio / aktivirao kod'
        elif active and visitor_online:
            status = 'active'
            status_label = 'Čeka odgovor'
        else:
            status = 'left'
            status_label = 'Odbio / napustio'
    else:
        accepted = bool(offer.added_to_cart)
        active = offer.show_popup and not accepted
        product_name = ''
        product_id = offer.product_id or None
        auto_browse = False
        auto_browse = (getattr(offer, 'aktivacioni_kod', '') or '').startswith(('AUTO-BROWSE', 'AI-PRODAJA'))
        kind = 'auto_browse' if auto_browse else 'product_offer'
        kind_label = 'Auto preporuka' if auto_browse else 'Ponuda artikla'
        if offer.product_id and offer.product:
            product_name = offer.product.naziv
        pct = offer.discount_percent or 0
        if pct:
            pct_label = int(pct) if pct == int(pct) else pct
            discount_label = f'{pct_label}%'
            if product_name:
                product_name = f'{product_name} (−{pct_label}%)'
        if offer.besplatna_dostava:
            product_name = (product_name or 'Ponuda') + ' + gratis dostava'
        if auto_browse and not product_name:
            product_name = 'Auto preporuka (gledanje)'
        if accepted:
            status = 'accepted'
            status_label = 'Prihvatio — u korpi'
        elif active and visitor_online:
            status = 'active'
            status_label = 'Čeka odgovor'
        elif active:
            status = 'active'
            status_label = 'Ponuda poslana'
        else:
            status = 'left'
            status_label = 'Odbio / zatvorio'

    return {
        'offer_sent': True,
        'offer_active': status == 'active',
        'offer_product': product_name or '',
        'offer_product_id': product_id,
        'offer_status': status,
        'offer_status_label': status_label,
        'offer_discount_label': discount_label,
        'offer_kind': kind,
        'offer_kind_label': kind_label,
    }


def _serialize_staff_action_from_offer(offer, *, visitor_online=False, now=None):
    now = now or timezone.now()
    fields = _offer_status_fields(offer, visitor_online=visitor_online)
    sent_at = offer.kreirano or offer.azurirano
    return {
        'kind': fields.get('offer_kind') or 'offer',
        'kind_label': fields.get('offer_kind_label') or 'Ponuda',
        'title': fields.get('offer_product') or fields.get('offer_kind_label') or 'Ponuda',
        'status': fields.get('offer_status') or '',
        'status_label': fields.get('offer_status_label') or '',
        'discount_label': fields.get('offer_discount_label') or '',
        'product_id': fields.get('offer_product_id'),
        'sent_at_label': _ago_action_label(sent_at, now),
        'sent_at_clock': (
            timezone.localtime(sent_at).strftime('%H:%M') if sent_at else ''
        ),
        'already_sent': True,
    }








def _staff_actions_for_visitor(visitor, actions_bundle, *, visitor_online=False, now=None):
    """Sve poslane staff akcije + ishod — potpuna istorija (odbio + prihvatio ostaju)."""
    now = now or timezone.now()
    from .models import LiveVisitorOffer

    actions = []
    seen_offer_ids = set()

    # SVE ponude (ne samo zadnja po tipu) — da stoje i odbijene i prihvaćene
    offer_lists = []
    if visitor.session_key:
        offer_lists.append(
            (actions_bundle.get('offers_list_by_session') or {}).get(visitor.session_key) or []
        )
    if visitor.user_id:
        offer_lists.append(
            (actions_bundle.get('offers_list_by_user') or {}).get(visitor.user_id) or []
        )
    for offer_list in offer_lists:
        for offer in offer_list:
            if offer.pk in seen_offer_ids:
                continue
            seen_offer_ids.add(offer.pk)
            action = _serialize_staff_action_from_offer(
                offer, visitor_online=visitor_online, now=now,
            )
            if (
                offer.tip == LiveVisitorOffer.Tip.REGISTRACIJA
                and visitor.user_id
                and action.get('status') != 'accepted'
            ):
                action['status'] = 'accepted'
                action['status_label'] = 'Registrovao se'
            try:
                action['sent_at_ts'] = (
                    offer.azurirano or offer.kreirano
                ).timestamp()
            except Exception:
                action['sent_at_ts'] = 0
            action['event_id'] = f'offer-{offer.pk}'
            actions.append(action)

    # Fallback: stari bucket po tipu (ako lista prazna)
    if not actions:
        by_tip = _lookup_offers_by_tip(actions_bundle, visitor)
        for tip in (
            'auto_browse',
            LiveVisitorOffer.Tip.ARTIKAL,
            LiveVisitorOffer.Tip.NARUDZBA,
            LiveVisitorOffer.Tip.REGISTRACIJA,
        ):
            offer = by_tip.get(tip)
            if not offer or offer.pk in seen_offer_ids:
                continue
            seen_offer_ids.add(offer.pk)
            action = _serialize_staff_action_from_offer(
                offer, visitor_online=visitor_online, now=now,
            )
            try:
                action['sent_at_ts'] = (offer.azurirano or offer.kreirano).timestamp()
            except Exception:
                action['sent_at_ts'] = 0
            action['event_id'] = f'offer-{offer.pk}'
            actions.append(action)

    # Sve nagrade (osvojio / izgubio / odbio / čeka)

    # Najnovije prvo
    actions.sort(key=lambda a: float(a.get('sent_at_ts') or 0), reverse=True)
    return actions


def _build_cart_presence_map(visitors):
    """
    Vraća:
    sessions_with_cart, users_with_cart,
    cart_value_by_session, cart_value_by_user,
    cart_items_by_session, cart_items_by_user
    """
    from collections import defaultdict
    from decimal import Decimal

    from django.db.models import Sum

    session_keys = [visitor.session_key for visitor in visitors if visitor.session_key]
    user_ids = [visitor.user_id for visitor in visitors if visitor.user_id]
    sessions_with_cart = set()
    users_with_cart = set()
    cart_value_by_session = {}
    cart_value_by_user = {}
    cart_items_by_session = defaultdict(list)
    cart_items_by_user = defaultdict(list)

    if session_keys:
        session_rows = (
            ActiveCartItem.objects.filter(session_key__in=session_keys)
            .values('session_key')
            .annotate(total=Sum('ukupno'))
        )
        for row in session_rows:
            key = row['session_key']
            total = row['total'] or Decimal('0')
            sessions_with_cart.add(key)
            cart_value_by_session[key] = total

        for item in (
            ActiveCartItem.objects.filter(session_key__in=session_keys)
            .select_related('product', 'variation')
            .order_by('-azurirano', '-id')
        ):
            cart_items_by_session[item.session_key].append(_serialize_cart_line(item))

    if user_ids:
        user_rows = (
            ActiveCartItem.objects.filter(user_id__in=user_ids)
            .values('user_id')
            .annotate(total=Sum('ukupno'))
        )
        for row in user_rows:
            uid = row['user_id']
            total = row['total'] or Decimal('0')
            users_with_cart.add(uid)
            cart_value_by_user[uid] = total

        for item in (
            ActiveCartItem.objects.filter(user_id__in=user_ids)
            .select_related('product', 'variation')
            .order_by('-azurirano', '-id')
        ):
            cart_items_by_user[item.user_id].append(_serialize_cart_line(item))

    return (
        sessions_with_cart,
        users_with_cart,
        cart_value_by_session,
        cart_value_by_user,
        dict(cart_items_by_session),
        dict(cart_items_by_user),
    )


def _serialize_cart_line(item):
    """JSON-friendly stavka aktivne korpe za staff live UI."""
    image_url = ''
    product_url = ''
    try:
        if item.variation_id and item.variation and item.variation.slika:
            image_url = item.variation.slika.url
        elif item.product_id and item.product and item.product.prikazna_slika:
            image_url = item.product.prikazna_slika.url
    except Exception:
        image_url = ''
    try:
        if item.product_id and item.product:
            product_url = item.product.get_absolute_url()
    except Exception:
        product_url = ''
    name = (item.naziv or '').strip() or 'Artikal'
    var_name = (item.varijacija_naziv or '').strip()
    try:
        cijena = item.cijena
        ukupno = item.ukupno
    except Exception:
        cijena = 0
        ukupno = 0
    return {
        'product_id': item.product_id,
        'naziv': name,
        'varijacija': var_name,
        'kolicina': int(item.kolicina or 1),
        'cijena': str(cijena),
        'cijena_label': f'{cijena} KM',
        'ukupno': str(ukupno),
        'ukupno_label': f'{ukupno} KM',
        'image_url': image_url,
        'product_url': product_url,
    }


def _visitor_has_cart(visitor, sessions_with_cart, users_with_cart):
    if visitor.user_id and visitor.user_id in users_with_cart:
        return True
    return visitor.session_key in sessions_with_cart


def _visitor_cart_value(visitor, cart_value_by_session, cart_value_by_user):
    from decimal import Decimal

    if visitor.user_id and visitor.user_id in cart_value_by_user:
        return cart_value_by_user[visitor.user_id]
    return cart_value_by_session.get(visitor.session_key) or Decimal('0')


def _visitor_cart_items(visitor, cart_items_by_session, cart_items_by_user):
    """Preferiraj korpu po sesiji; ako prazna, po useru."""
    items = []
    if visitor.session_key:
        items = list(cart_items_by_session.get(visitor.session_key) or [])
    if not items and visitor.user_id:
        items = list(cart_items_by_user.get(visitor.user_id) or [])
    return items


















def _format_time_on_site(seconds):
    """Čitljivo vrijeme provedeno na sajtu (npr. 45 s, 12 min, 1 h 5 min)."""
    seconds = max(0, int(seconds or 0))
    if seconds < 60:
        return f'{seconds} s'
    minutes = seconds // 60
    if minutes < 60:
        return f'{minutes} min'
    hours = minutes // 60
    rem_min = minutes % 60
    if hours < 24:
        if rem_min:
            return f'{hours} h {rem_min} min'
        return f'{hours} h'
    days = hours // 24
    rem_h = hours % 24
    if rem_h:
        return f'{days} d {rem_h} h'
    return f'{days} d'


def _build_site_buyer_stats(visitors):
    """
    Korisnici / emailovi koji su već poručili preko sajta.
    Vraća (buyer_user_ids, buyer_emails, purchase_count_by_user, purchase_count_by_email).
    """
    from django.db.models import Count
    from django.db.models.functions import Lower

    from .models import Order

    user_ids = [v.user_id for v in visitors if v.user_id]
    emails = []
    for visitor in visitors:
        email = (visitor.email or '').strip().lower()
        if email:
            emails.append(email)
        user = getattr(visitor, 'user', None)
        if user is not None:
            user_email = (getattr(user, 'email', None) or '').strip().lower()
            if user_email:
                emails.append(user_email)
    emails = list(set(emails))

    buyer_user_ids = set()
    buyer_emails = set()
    purchase_count_by_user = {}
    purchase_count_by_email = {}
    orders = Order.objects.exclude(status=Order.Status.OTKAZANA)
    if user_ids:
        for row in (
            orders.filter(korisnik_id__in=user_ids)
            .values('korisnik_id')
            .annotate(cnt=Count('pk'))
        ):
            uid = row['korisnik_id']
            cnt = row['cnt'] or 0
            if cnt:
                buyer_user_ids.add(uid)
                purchase_count_by_user[uid] = cnt
    if emails:
        for row in (
            orders.annotate(email_l=Lower('email'))
            .filter(email_l__in=emails)
            .values('email_l')
            .annotate(cnt=Count('pk'))
        ):
            em = row['email_l']
            cnt = row['cnt'] or 0
            if cnt and em:
                buyer_emails.add(em)
                purchase_count_by_email[em] = cnt
    return buyer_user_ids, buyer_emails, purchase_count_by_user, purchase_count_by_email


def _visitor_purchase_info(visitor, buyer_user_ids, buyer_emails, purchase_count_by_user, purchase_count_by_email):
    count = 0
    if visitor.user_id and visitor.user_id in purchase_count_by_user:
        count = purchase_count_by_user[visitor.user_id]
    email = (visitor.email or '').strip().lower()
    if not count and email and email in purchase_count_by_email:
        count = purchase_count_by_email[email]
    user = getattr(visitor, 'user', None)
    if not count and user is not None:
        user_email = (getattr(user, 'email', None) or '').strip().lower()
        if user_email and user_email in purchase_count_by_email:
            count = purchase_count_by_email[user_email]
    has_purchased = bool(count) or (
        (visitor.user_id and visitor.user_id in buyer_user_ids)
        or (email and email in buyer_emails)
    )
    return has_purchased, count


def _build_site_buyer_sets(visitors):
    """Kompatibilnost sa staff_alerts — (buyer_user_ids, buyer_emails)."""
    buyer_user_ids, buyer_emails, _, _ = _build_site_buyer_stats(visitors)
    return buyer_user_ids, buyer_emails


def _visitor_has_purchased(visitor, buyer_user_ids, buyer_emails):
    """Kompatibilnost sa staff_alerts."""
    has_purchased, _count = _visitor_purchase_info(
        visitor, buyer_user_ids, buyer_emails, {}, {},
    )
    # Ako nema count mapa, fallback na setove
    if has_purchased:
        return True
    if visitor.user_id and visitor.user_id in buyer_user_ids:
        return True
    email = (visitor.email or '').strip().lower()
    if email and email in buyer_emails:
        return True
    user = getattr(visitor, 'user', None)
    if user is not None:
        user_email = (getattr(user, 'email', None) or '').strip().lower()
        if user_email and user_email in buyer_emails:
            return True
    return False


def _normalize_product_views(raw_products):
    products = []
    for item in (raw_products or []):
        if not isinstance(item, dict):
            continue
        try:
            pid = int(item.get('id') or 0)
        except (TypeError, ValueError):
            continue
        if not pid:
            continue
        try:
            views = max(1, int(item.get('views') or 1))
        except (TypeError, ValueError):
            views = 1
        naziv = str(item.get('naziv') or '').strip()[:120]
        products.append({'id': pid, 'naziv': naziv, 'views': views})
    return products


def _visitor_payload(
    visitor,
    *,
    now,
    offer=None,
    staff_actions=None,
    has_cart=False,
    cart_value=None,
    cart_items=None,
    has_purchased=False,
    purchase_count=0,
):
    from decimal import Decimal

    seconds_ago = max(0, int((now - visitor.last_seen).total_seconds()))
    if seconds_ago < 60:
        ago_label = 'upravo sada'
    elif seconds_ago < 3600:
        minutes = seconds_ago // 60
        ago_label = f'prije {minutes} min'
    else:
        hours = seconds_ago // 3600
        ago_label = f'prije {hours} h'

    # Online: svjež last_seen I nije označen kao left (zatvorio tab)
    marked_left = is_visitor_marked_left(getattr(visitor, 'session_key', '') or '')
    is_online = (seconds_ago < ONLINE_MINUTES * 60) and not marked_left
    # Online: od ulaska do sada; offline: od ulaska do zadnje aktivnosti
    end_time = now if is_online else visitor.last_seen
    first_seen = visitor.first_seen or visitor.last_seen or now
    time_on_site_seconds = max(0, int((end_time - first_seen).total_seconds()))
    time_on_site_label = _format_time_on_site(time_on_site_seconds)

    # Grad: uvijek prikaži ako znamo (IP geo, profil, raniji unos)
    grad = (visitor.grad or '').strip()
    if not grad and visitor.user_id and getattr(visitor, 'user', None):
        profil = getattr(visitor.user, 'profil', None)
        if profil and profil.grad:
            grad = profil.grad.strip()

    category_views = _normalize_category_views(visitor.pregledane_kategorije)
    categories = [c['naziv'] for c in category_views if c.get('naziv')]
    # Sa brojem ulazaka (views) za staff UI
    categories_with_views = [
        {
            'naziv': c['naziv'],
            'views': int(c.get('views') or 1),
        }
        for c in category_views if c.get('naziv')
    ]
    products = _normalize_product_views(getattr(visitor, 'pregledani_proizvodi', None))
    products_viewed_count = len(products)
    total_product_entries = sum(int(p.get('views') or 1) for p in products)
    almost_cart = []
    try:
        from .almost_cart import get_almost_cart_products

        almost_cart = get_almost_cart_products(visitor)
    except Exception:
        almost_cart = []
    revisited = [p for p in products if p.get('views', 1) > 1]
    returned_to_product = bool(revisited)
    returned_products = [
        {
            'id': p['id'],
            'naziv': p.get('naziv') or '',
            'views': p.get('views') or 1,
        }
        for p in revisited
    ]
    returned_products_label = ', '.join(
        f"{p['naziv']} ({p['views']}×)" for p in returned_products[:3] if p.get('naziv')
    )
    if len(returned_products) > 3:
        returned_products_label = f'{returned_products_label}…'

    source_key = normalize_traffic_source(getattr(visitor, 'izvor_dolaska', None))
    source_label = traffic_source_label(source_key, short=True)

    if cart_value is None:
        cart_value = Decimal('0')
    try:
        cart_value_dec = Decimal(str(cart_value or 0)).quantize(Decimal('0.01'))
    except Exception:
        cart_value_dec = Decimal('0.00')
    cart_value_label = f'{cart_value_dec:.2f} KM' if cart_value_dec > 0 else '—'

    is_registered = bool(visitor.user_id)
    # Label s brojem ulazaka: „Artikal (3×), Drugi (1×)”
    def _prod_entry_label(p):
        name = (p.get('naziv') or '').strip()
        if not name:
            return ''
        views = int(p.get('views') or 1)
        return f'{name} ({views}×)' if views > 1 else f'{name} (1×)'

    products_label = ', '.join(
        _prod_entry_label(p) for p in products[:5] if p.get('naziv')
    )
    if products_viewed_count > 5:
        products_label = f'{products_label}…'

    purchase_label = ''
    if has_purchased:
        if purchase_count > 1:
            purchase_label = f'{purchase_count} kupovine'
        elif purchase_count == 1:
            purchase_label = '1 kupovina'
        else:
            purchase_label = 'Kupovao ranije'

    # Pametne preporuke za staff (šta ručno poslati) + psihologija gledanja
    sell_recs = []
    visitor_insight = {}
    offer_outcomes = {'accepted': [], 'rejected': [], 'pending': []}

    top_sell = sell_recs[0] if sell_recs else None

    # Trenutni artikal (ako je na /artikal/…) — za brzi % na kartici „Sada”
    current_path = (getattr(visitor, 'trenutna_putanja', None) or '')[:300]
    current_page = (getattr(visitor, 'trenutno_gleda', None) or '')[:200]
    current_product = _product_from_path(current_path)
    current_product_id = current_product.pk if current_product else None
    current_product_name = (
        (current_product.naziv or '')[:120] if current_product else ''
    )
    if current_product_id and not current_page:
        current_page = f'Artikal: {current_product_name}'[:200]

    payload = {
        'session_key': visitor.session_key,
        'user_id': visitor.user_id or None,
        'ime': visitor.ime or 'Gost',
        'email': visitor.email or '',
        'grad': grad or '',
        'grad_label': grad or 'Grad nepoznat',
        'categories': categories,
        'categories_with_views': categories_with_views,
        'categories_label': ', '.join(
            f"{c['naziv']} ({c['views']}×)"
            for c in category_views[:6]
            if c.get('naziv')
        ),
        'products': products,
        'products_viewed_count': products_viewed_count,
        'total_product_entries': total_product_entries,
        'products_label': products_label,
        'visited_product_ids': [int(p['id']) for p in products if p.get('id')],
        'returned_to_product': returned_to_product,
        'returned_products': returned_products,
        'returned_products_label': returned_products_label,
        'traffic_source': source_key,
        'traffic_source_label': source_label,
        'cart_value': str(cart_value_dec),
        'cart_value_label': cart_value_label,
        'has_cart': has_cart or cart_value_dec > 0,
        'cart_items': list(cart_items or []),
        'cart_items_count': len(list(cart_items or [])),
        'has_purchased': bool(has_purchased),
        'purchase_count': int(purchase_count or 0),
        'purchase_label': purchase_label,
        'is_registered': is_registered,
        'is_guest': not is_registered and not visitor.email,
        'can_invite_register': not is_registered,
        'can_email_offer': bool((visitor.email or '').strip()),
        'last_seen': visitor.last_seen,
        'last_seen_label': ago_label,
        'seconds_ago': seconds_ago,
        'time_on_site_seconds': time_on_site_seconds,
        'time_on_site_label': time_on_site_label,
        'is_online': is_online,
        'site_visit_count': int(getattr(visitor, 'site_visit_count', 1) or 1),
        'is_returning_visitor': int(getattr(visitor, 'site_visit_count', 1) or 1) > 1,
        'returning_label': (
            f'Vraćeni posjetilac ({int(getattr(visitor, "site_visit_count", 1) or 1)}× na sajtu)'
            if int(getattr(visitor, 'site_visit_count', 1) or 1) > 1
            else 'Prvi put na sajtu'
        ),
        'current_path': current_path,
        'current_page': current_page,
        'current_product_id': current_product_id,
        'current_product_name': current_product_name,
        'current_product_accepted': bool(
            current_product_id
            and any(
                int(r.get('product_id') or 0) == int(current_product_id)
                for r in (offer_outcomes.get('accepted') or [])
                if r.get('product_id')
            )
        ),
        'visitor_insight': visitor_insight,
        'sell_recommendations': sell_recs,
        'top_sell_recommendation': top_sell,
        'top_sell_label': (
            f"{top_sell.get('naziv')} — {top_sell.get('reason')}"
            if top_sell else ''
        ),
        'almost_cart': almost_cart,
        'almost_cart_count': len(almost_cart),
        'almost_cart_label': (
            f"{almost_cart[0].get('naziv')} ({almost_cart[0].get('hovers')}× hover)"
            if almost_cart else ''
        ),
        'offer_outcomes': offer_outcomes,
        'accepted_offer_ids': [
            r.get('product_id') for r in (offer_outcomes.get('accepted') or [])
            if r.get('product_id')
        ],
        'rejected_offer_ids': [
            r.get('product_id') for r in (offer_outcomes.get('rejected') or [])
            if r.get('product_id')
        ],
    }
    # AI conversion — intent skor + preporučena akcija za staff
    payload.update(_offer_status_fields(offer, visitor_online=payload['is_online']))
    actions = list(staff_actions or [])
    payload['staff_actions'] = actions
    payload['has_staff_actions'] = bool(actions)
    # Flagovi da se ne šalje isto 2× (UI disabled / badge)
    payload['sent_product_offer'] = any(a.get('kind') == 'product_offer' for a in actions)
    payload['sent_discount_offer'] = any(a.get('kind') == 'discount' for a in actions)
    payload['sent_register_invite'] = any(a.get('kind') == 'register' for a in actions)
    payload['sent_gift'] = any(a.get('kind') == 'gift' for a in actions)
    # Šta je kupac uradio (sažetak za karticu)
    if actions:
        latest = actions[0]
        # prefer active then accepted then left for card chip
        for preferred in ('active', 'accepted', 'left'):
            match = next((a for a in actions if a.get('status') == preferred), None)
            if match:
                latest = match
                break
        payload['staff_action_summary'] = (
            f"{latest.get('kind_label')}: {latest.get('status_label')}"
        )
        payload['staff_action_summary_status'] = latest.get('status') or ''
    else:
        payload['staff_action_summary'] = ''
        payload['staff_action_summary_status'] = ''
    return payload




def get_live_visitor_snapshot_lite(*, limit: int = 60):
    """
    Lagani live snapshot za staff (0.5 CPU).

    Samo tko je online sada: ime, šta gleda, odakle je došao.
    Bez korpi, AI, gift, registered list, traffic stats, offline prozora.
    """
    now = timezone.now()
    online_cutoff = now - timedelta(minutes=ONLINE_MINUTES)

    exclude_ips = [ip for ip in get_configured_exclude_ips() if _is_valid_db_ip(ip)]
    qs = (
        LiveVisitor.objects.filter(last_seen__gte=online_cutoff)
        .exclude(user__is_staff=True)
        .exclude(user__is_superuser=True)
        .exclude(session_key='')
    )
    if exclude_ips:
        qs = qs.exclude(ip_adresa__in=exclude_ips)
    qs = qs.only(
        'session_key',
        'ime',
        'email',
        'grad',
        'izvor_dolaska',
        'trenutna_putanja',
        'trenutno_gleda',
        'last_seen',
        'first_seen',
        'visitor_token',
        'ip_adresa',
        'user_id',
    ).order_by('-last_seen')[: max(20, min(int(limit or 60), 80))]

    # person_key → najnoviji red (dedupe bot/više tabova)
    by_person: dict[str, dict] = {}
    for row in qs:
        if is_non_visitor_path(row.trenutna_putanja):
            continue
        sk = (row.session_key or '').strip()
        if not sk or is_visitor_marked_left(sk):
            continue
        seconds_ago = (
            max(0, int((now - row.last_seen).total_seconds()))
            if row.last_seen
            else 9999
        )
        if seconds_ago >= ONLINE_MINUTES * 60:
            continue

        token = (row.visitor_token or '').strip()
        ip = row.ip_adresa
        if token:
            person = f't:{token}'
        elif ip:
            person = f'ip:{ip}'
        else:
            person = f's:{sk}'

        source_key = normalize_traffic_source(getattr(row, 'izvor_dolaska', None))
        doing = (row.trenutno_gleda or '').strip() or (row.trenutna_putanja or '').strip() or 'Na sajtu'
        # Preskoči čisti poll/API šum
        path = (row.trenutna_putanja or '').strip()
        if path.startswith(('/api/', '/uzivo/', '/healthz', '/static/', '/admin/')):
            continue

        ime = (row.ime or '').strip()
        if not ime:
            email = (row.email or '').strip()
            ime = email.split('@', 1)[0] if email else 'Gost'
        if seconds_ago < 15:
            ago_label = 'sada'
        elif seconds_ago < 60:
            ago_label = f'prije {seconds_ago} s'
        else:
            ago_label = f'prije {seconds_ago // 60} min'

        payload = {
            'session_key': sk,
            'ime': ime[:80],
            'grad': (row.grad or '').strip()[:80],
            'current_page': doing[:200],
            'path': path[:200],
            'source_key': source_key,
            'source_label': traffic_source_label(source_key, short=True),
            'source_label_full': traffic_source_label(source_key),
            'seconds_ago': seconds_ago,
            'ago_label': ago_label,
            'is_online': True,
        }
        prev = by_person.get(person)
        if prev is None or payload['seconds_ago'] < prev['seconds_ago']:
            by_person[person] = payload

    online_visitors = sorted(by_person.values(), key=lambda r: r['seconds_ago'])

    # Sažetak izvora među online
    source_counts: dict[str, int] = {}
    for v in online_visitors:
        k = v['source_key']
        source_counts[k] = source_counts.get(k, 0) + 1
    sources = [
        {
            'key': k,
            'label': traffic_source_label(k, short=True),
            'count': n,
        }
        for k, n in sorted(source_counts.items(), key=lambda x: -x[1])
    ]

    return {
        'online_count': len(online_visitors),
        'online_visitors': online_visitors,
        'sources': sources,
        'online_minutes': ONLINE_MINUTES,
        'generated_at': now,
    }


def get_live_visitor_snapshot():
    """
    Kompatibilni alias — staff UI sada koristi lite snapshot.
    (Stari teški path s korpama/AI/gift je uklonjen s hot-patha.)
    """
    lite = get_live_visitor_snapshot_lite()
    return {
        'online_count': lite['online_count'],
        'window_count': lite['online_count'],
        'registered_online_count': 0,
        'registered_window_count': 0,
        'online_visitors': lite['online_visitors'],
        'window_visitors': lite['online_visitors'],
        'registered_online_visitors': [],
        'registered_window_visitors': [],
        'sources': lite.get('sources') or [],
        'online_minutes': lite['online_minutes'],
        'window_minutes': lite['online_minutes'],
        'generated_at': lite['generated_at'],
    }


def get_registered_customers(*, online_user_ids=None):
    """
    Svi registrovani kupci (ne-staff) s emailom, uključujući neaktivirane naloge.
    online_user_ids — set user_id koji su trenutno online (za badge).
    """
    from django.contrib.auth.models import User

    online_user_ids = set(online_user_ids or [])
    users = (
        User.objects.filter(is_superuser=False, is_staff=False)
        .exclude(email='')
        .select_related('profil')
        # Neaktivirani prvo, zatim po emailu
        .order_by('is_active', 'email')
    )
    rows = []
    for user in users:
        email = (user.email or '').strip()
        if not email or '@' not in email:
            continue
        full = (user.get_full_name() or '').strip()
        if not full:
            full = (user.first_name or '').strip() or email.split('@', 1)[0]
        grad = ''
        profil = getattr(user, 'profil', None)
        if profil and profil.grad:
            grad = profil.grad.strip()
        rows.append({
            'user_id': user.pk,
            'ime': full[:120],
            'email': email,
            'grad': grad,
            'is_online': user.pk in online_user_ids,
            'is_active': bool(user.is_active),
            'account_status_label': 'Aktivan' if user.is_active else 'Nije aktiviran',
        })
    return rows
