"""
Pregled sajta — agregati posjeta, kupovina, prometa, izvora prometa i interakcija.

Izvori:
  - LiveVisitor.first_seen + izvor_dolaska → posjetioci / kanali
  - Order (bez otkazanih) → narudžbe, promet
  - StaffSiteEvent → korpa, kupovine, savjetnik, ponude
  - ChatConversation / ChatMessage → chat interakcije
  - ActiveCartItem → aktivne korpe
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth, TruncYear
from django.utils import timezone

from .live_visitors import (
    SOURCE_DIRECT,
    SOURCE_DISPLAY_ORDER,
    SOURCE_LABELS,
    SOURCE_OTHER,
    normalize_traffic_source,
    traffic_source_label,
    visitors_analytics_qs,
)
from .models import (
    ActiveCartItem,
    ChatConversation,
    ChatMessage,
    Order,
    StaffSiteEvent,
)


def _as_local(dt):
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return timezone.localtime(dt)


def _day_bounds(d: date):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(d, time.min), tz)
    end = timezone.make_aware(datetime.combine(d, time.max), tz)
    return start, end


def _month_bounds(year: int, month: int):
    last = monthrange(year, month)[1]
    return _day_bounds(date(year, month, 1))[0], _day_bounds(date(year, month, last))[1]


def _year_bounds(year: int):
    return _day_bounds(date(year, 1, 1))[0], _day_bounds(date(year, 12, 31))[1]


def _period_window(period: str):
    """(start, end, label) za filter analitike po izvoru / engagementu."""
    today = timezone.localdate()
    if period == 'month':
        start_date = today.replace(day=1)
        for _ in range(23):
            y, m = start_date.year, start_date.month - 1
            if m < 1:
                m, y = 12, y - 1
            start_date = date(y, m, 1)
        start, _ = _month_bounds(start_date.year, start_date.month)
        _, end = _month_bounds(today.year, today.month)
        return start, end, 'Zadnja 24 mjeseca'
    if period == 'year':
        start, end = _year_bounds(today.year - 7)[0], _year_bounds(today.year)[1]
        return start, end, 'Zadnjih 8 godina'
    # day default
    start_date = today - timedelta(days=30)
    start, end = _day_bounds(start_date)[0], _day_bounds(today)[1]
    return start, end, 'Zadnjih 31 dan'


def _orders_qs():
    """Online narudžbe koje brojimo u promet (sve osim otkazanih)."""
    return Order.objects.exclude(status=Order.Status.OTKAZANA)


def _money(value) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(Decimal('0.01'))
    except Exception:
        return Decimal('0.00')


def _pct(part, whole) -> float:
    try:
        w = float(whole or 0)
        if w <= 0:
            return 0.0
        return round(100.0 * float(part or 0) / w, 1)
    except Exception:
        return 0.0


def summary_totals(*, start=None, end=None) -> dict:
    """Ukupni brojevi za period (ili all-time ako start/end None)."""
    visitors = visitors_analytics_qs()
    orders = _orders_qs()
    if start is not None:
        visitors = visitors.filter(first_seen__gte=start)
        orders = orders.filter(kreirana__gte=start)
    if end is not None:
        visitors = visitors.filter(first_seen__lte=end)
        orders = orders.filter(kreirana__lte=end)

    visitors_count = visitors.count()
    buyers_count = (
        orders.exclude(email='')
        .values('email')
        .distinct()
        .count()
    )
    orders_count = orders.count()
    revenue = _money(orders.aggregate(t=Sum('ukupno'))['t'])
    avg_order = _money(revenue / orders_count) if orders_count else Decimal('0.00')
    returning = visitors.filter(site_visit_count__gt=1).count()

    return {
        'visitors': visitors_count,
        'buyers': buyers_count,
        'orders': orders_count,
        'revenue': revenue,
        'avg_order': avg_order,
        'conversion_visitors': _pct(orders_count, visitors_count),
        'conversion_buyers': _pct(buyers_count, visitors_count),
        'returning': returning,
        'returning_pct': _pct(returning, visitors_count),
        'new_visitors': max(0, visitors_count - returning),
    }


def traffic_source_breakdown(*, start=None, end=None) -> list[dict]:
    """
    Posjetioci po izvoru dolaska + udio.
    Uključuje sve poznate kanale (0 ako nema podataka).
    """
    qs = visitors_analytics_qs()
    if start is not None:
        qs = qs.filter(first_seen__gte=start)
    if end is not None:
        qs = qs.filter(first_seen__lte=end)

    raw = qs.values('izvor_dolaska').annotate(visitors=Count('id'))
    counts: dict[str, int] = {k: 0 for k in SOURCE_DISPLAY_ORDER}
    for row in raw:
        key = normalize_traffic_source(row['izvor_dolaska'])
        counts[key] = counts.get(key, 0) + int(row['visitors'] or 0)

    total = sum(counts.values())
    rows = []
    for key in SOURCE_DISPLAY_ORDER:
        n = counts.get(key, 0)
        rows.append({
            'key': key,
            'label': traffic_source_label(key),
            'short_label': traffic_source_label(key, short=True),
            'visitors': n,
            'share': _pct(n, total),
        })
    # Sort: najviše posjeta prvo, ali zadrži nule na dnu
    rows.sort(key=lambda r: (-r['visitors'], list(SOURCE_DISPLAY_ORDER).index(r['key'])))
    return rows


def _visitor_source_by_email(*, start=None, end=None) -> dict[str, str]:
    """email (lower) → izvor (first-touch u periodu, najraniji first_seen)."""
    qs = (
        visitors_analytics_qs()
        .exclude(email='')
        .exclude(email__isnull=True)
        .order_by('first_seen')
        .values_list('email', 'izvor_dolaska')
    )
    if start is not None:
        qs = qs.filter(first_seen__gte=start)
    if end is not None:
        qs = qs.filter(first_seen__lte=end)

    mapping: dict[str, str] = {}
    for email, izvor in qs.iterator(chunk_size=500):
        key = (email or '').strip().lower()
        if not key or key in mapping:
            continue
        mapping[key] = normalize_traffic_source(izvor)
    return mapping


def _visitor_source_by_session(*, start=None, end=None) -> dict[str, str]:
    qs = visitors_analytics_qs().order_by('first_seen').values_list(
        'session_key', 'izvor_dolaska',
    )
    if start is not None:
        qs = qs.filter(first_seen__gte=start)
    if end is not None:
        qs = qs.filter(first_seen__lte=end)
    mapping: dict[str, str] = {}
    for session_key, izvor in qs.iterator(chunk_size=500):
        if not session_key or session_key in mapping:
            continue
        mapping[session_key] = normalize_traffic_source(izvor)
    return mapping


def orders_by_traffic_source(*, start=None, end=None) -> list[dict]:
    """
    Narudžbe / promet po izvoru — veza preko emaila posjetioca
    (first-touch u periodu), fallback Ostalo ako nema veze.
    """
    email_src = _visitor_source_by_email(start=start, end=end)
    orders = _orders_qs()
    if start is not None:
        orders = orders.filter(kreirana__gte=start)
    if end is not None:
        orders = orders.filter(kreirana__lte=end)

    agg: dict[str, dict] = {
        k: {'orders': 0, 'revenue': Decimal('0.00'), 'buyers': set()}
        for k in SOURCE_DISPLAY_ORDER
    }
    for order in orders.only('email', 'ukupno').iterator(chunk_size=300):
        email = (order.email or '').strip().lower()
        src = email_src.get(email) if email else None
        if not src:
            # Nema veze s posjetiocem (email nije viđen u analyticsu)
            src = SOURCE_OTHER
        bucket = agg.setdefault(
            src,
            {'orders': 0, 'revenue': Decimal('0.00'), 'buyers': set()},
        )
        bucket['orders'] += 1
        bucket['revenue'] += _money(order.ukupno)
        if email:
            bucket['buyers'].add(email)

    # visitors per source for conversion
    visitor_rows = {
        r['key']: r['visitors']
        for r in traffic_source_breakdown(start=start, end=end)
    }

    rows = []
    for key in SOURCE_DISPLAY_ORDER:
        b = agg.get(key) or {'orders': 0, 'revenue': Decimal('0.00'), 'buyers': set()}
        orders_n = b['orders']
        rev = _money(b['revenue'])
        visitors_n = visitor_rows.get(key, 0)
        rows.append({
            'key': key,
            'label': traffic_source_label(key),
            'short_label': traffic_source_label(key, short=True),
            'visitors': visitors_n,
            'orders': orders_n,
            'buyers': len(b['buyers']),
            'revenue': rev,
            'avg_order': _money(rev / orders_n) if orders_n else Decimal('0.00'),
            'conversion': _pct(orders_n, visitors_n),
            'share_orders': 0.0,  # popuni ispod
        })

    total_orders = sum(r['orders'] for r in rows)
    for r in rows:
        r['share_orders'] = _pct(r['orders'], total_orders)
    rows.sort(key=lambda r: (-r['orders'], -r['visitors']))
    return rows


def engagement_stats(*, start=None, end=None) -> dict:
    """Interakcije: korpa, pregledi, skoro-korpa, savjetnik, ponude, online."""
    visitors = visitors_analytics_qs()
    events = StaffSiteEvent.objects.all()
    if start is not None:
        visitors = visitors.filter(first_seen__gte=start)
        events = events.filter(kreirano__gte=start)
    if end is not None:
        visitors = visitors.filter(first_seen__lte=end)
        events = events.filter(kreirano__lte=end)

    with_products = visitors.exclude(pregledani_proizvodi=[]).exclude(
        pregledani_proizvodi__isnull=True,
    ).count()
    with_categories = visitors.exclude(pregledane_kategorije=[]).exclude(
        pregledane_kategorije__isnull=True,
    ).count()
    almost_cart = visitors.exclude(skoro_korpa=[]).exclude(skoro_korpa__isnull=True).count()
    with_advisor = visitors.exclude(savjetnik={}).exclude(savjetnik__isnull=True).count()

    event_counts = {
        row['tip']: int(row['n'] or 0)
        for row in events.values('tip').annotate(n=Count('id'))
    }

    cart_sessions = (
        ActiveCartItem.objects.values('session_key').distinct().count()
    )
    if start is not None or end is not None:
        # ActiveCartItem nema first_seen — brojimo događaje cart u periodu
        cart_sessions = event_counts.get(StaffSiteEvent.Tip.CART, 0)

    return {
        'viewed_products': with_products,
        'viewed_categories': with_categories,
        'almost_cart': almost_cart,
        'advisor_sessions': with_advisor,
        'events_cart': event_counts.get(StaffSiteEvent.Tip.CART, 0),
        'events_purchase': event_counts.get(StaffSiteEvent.Tip.PURCHASE, 0),
        'events_register': event_counts.get(StaffSiteEvent.Tip.REGISTER, 0),
        'events_offer': event_counts.get(StaffSiteEvent.Tip.OFFER, 0),
        'events_advisor': event_counts.get(StaffSiteEvent.Tip.ADVISOR, 0),
        'events_online': event_counts.get(StaffSiteEvent.Tip.ONLINE, 0),
        'active_cart_sessions': cart_sessions,
        'visitors_total': visitors.count(),
    }


def chat_stats(*, start=None, end=None) -> dict:
    convs = ChatConversation.objects.all()
    msgs = ChatMessage.objects.all()
    if start is not None:
        convs = convs.filter(created_at__gte=start)
        msgs = msgs.filter(created_at__gte=start)
    if end is not None:
        convs = convs.filter(created_at__lte=end)
        msgs = msgs.filter(created_at__lte=end)

    total_convs = convs.count()
    open_convs = convs.filter(status=ChatConversation.Status.OPEN).count()
    closed_convs = convs.filter(status=ChatConversation.Status.CLOSED).count()
    customer_msgs = msgs.filter(sender_type=ChatMessage.Sender.CUSTOMER).count()
    staff_msgs = msgs.filter(sender_type=ChatMessage.Sender.STAFF).count()
    product_offers = msgs.exclude(product_id=None).count()

    # Chat ↔ izvor (preko session_key)
    session_src = _visitor_source_by_session(start=start, end=end)
    by_source: dict[str, int] = {k: 0 for k in SOURCE_DISPLAY_ORDER}
    for sk in convs.exclude(session_key='').values_list('session_key', flat=True):
        key = session_src.get(sk) or SOURCE_OTHER
        by_source[key] = by_source.get(key, 0) + 1

    source_rows = [
        {
            'key': k,
            'label': traffic_source_label(k, short=True),
            'conversations': by_source.get(k, 0),
            'share': _pct(by_source.get(k, 0), total_convs),
        }
        for k in SOURCE_DISPLAY_ORDER
        if by_source.get(k, 0)
    ]
    source_rows.sort(key=lambda r: -r['conversations'])

    return {
        'conversations': total_convs,
        'open': open_convs,
        'closed': closed_convs,
        'messages_total': customer_msgs + staff_msgs,
        'messages_customer': customer_msgs,
        'messages_staff': staff_msgs,
        'product_offers': product_offers,
        'avg_messages': (
            round((customer_msgs + staff_msgs) / total_convs, 1) if total_convs else 0.0
        ),
        'by_source': source_rows,
    }


def _bucket_key(value):
    """
    Normalizuj Trunc* vrijednost u date radi stabilnog spajanja i sortiranja.

    Postgres/SQLite mogu vratiti date ili datetime (aware/naive) za isti bucket —
    miješanje tipova u dict ključu i sorted() baca TypeError (500 na pregledu).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        local = _as_local(value) or value
        try:
            return local.date()
        except Exception:
            return date(local.year, local.month, local.day)
    if isinstance(value, date):
        return value
    # string / ostalo — pokušaj parsirati
    try:
        text = str(value)[:10]
        return date.fromisoformat(text)
    except Exception:
        return None


def _bucket_rows(trunc_fn, *, start, end, label_fmt, period: str) -> list[dict]:
    """
    Spoji posjetioce (first_seen) i narudžbe (kreirana) po bucketu.
    """
    visitors = (
        visitors_analytics_qs()
        .filter(first_seen__gte=start, first_seen__lte=end)
        .annotate(bucket=trunc_fn('first_seen'))
        .values('bucket')
        .annotate(visitors=Count('id'))
    )
    orders = (
        _orders_qs()
        .filter(kreirana__gte=start, kreirana__lte=end)
        .annotate(bucket=trunc_fn('kreirana'))
        .values('bucket')
        .annotate(
            orders=Count('id'),
            buyers=Count('email', distinct=True),
            revenue=Sum('ukupno'),
        )
    )

    by_bucket: dict = {}
    for row in visitors:
        key = _bucket_key(row['bucket'])
        if key is None:
            continue
        by_bucket[key] = {
            'bucket': key,
            'visitors': int(row['visitors'] or 0),
            'orders': 0,
            'buyers': 0,
            'revenue': Decimal('0.00'),
        }

    for row in orders:
        key = _bucket_key(row['bucket'])
        if key is None:
            continue
        entry = by_bucket.setdefault(
            key,
            {
                'bucket': key,
                'visitors': 0,
                'orders': 0,
                'buyers': 0,
                'revenue': Decimal('0.00'),
            },
        )
        entry['orders'] = int(row['orders'] or 0)
        entry['buyers'] = int(row['buyers'] or 0)
        entry['revenue'] = _money(row['revenue'])

    rows = []
    for key in sorted(by_bucket.keys(), reverse=True):
        e = by_bucket[key]
        b = e['bucket']
        try:
            label = b.strftime(label_fmt)
        except Exception:
            label = str(b)
        orders_n = e['orders']
        rev = e['revenue']
        rows.append({
            'period': period,
            'label': label,
            'sort_key': b,
            'visitors': e['visitors'],
            'buyers': e['buyers'],
            'orders': orders_n,
            'revenue': rev,
            'avg_order': _money(rev / orders_n) if orders_n else Decimal('0.00'),
            'conversion': _pct(orders_n, e['visitors']),
        })
    return rows


def stats_by_day(*, days: int = 30, end_date: date | None = None) -> list[dict]:
    end_date = end_date or timezone.localdate()
    start_date = end_date - timedelta(days=max(1, days) - 1)
    start, end = _day_bounds(start_date)[0], _day_bounds(end_date)[1]
    return _bucket_rows(TruncDate, start=start, end=end, label_fmt='%d.%m.%Y.', period='day')


def stats_by_month(*, months: int = 12, end_date: date | None = None) -> list[dict]:
    end_date = end_date or timezone.localdate()
    y, m = end_date.year, end_date.month
    for _ in range(max(1, months) - 1):
        m -= 1
        if m < 1:
            m = 12
            y -= 1
    start, _ = _month_bounds(y, m)
    _, end = _month_bounds(end_date.year, end_date.month)
    return _bucket_rows(TruncMonth, start=start, end=end, label_fmt='%m.%Y.', period='month')


def stats_by_year(*, years: int = 5, end_date: date | None = None) -> list[dict]:
    end_date = end_date or timezone.localdate()
    start_year = end_date.year - max(1, years) + 1
    start, end = _year_bounds(start_year)[0], _year_bounds(end_date.year)[1]
    return _bucket_rows(TruncYear, start=start, end=end, label_fmt='%Y.', period='year')


def _person_key(visitor_token: str, ip_adresa, session_key: str = '') -> str:
    """
    Identitet jedne osobe.

    1) Trajni cookie token (ozb_vid)
    2) IP adresa (spaja više sesija / botova s istog IP-a)
    Session-only (bez token/IP) = vjerojatno bot → ne brojimo.
    """
    token = (visitor_token or '').strip()
    if token:
        return f't:{token}'
    if ip_adresa:
        return f'ip:{ip_adresa}'
    return ''


def _period_bounds(
    *,
    period: str = 'day',
    date_from: str = '',
    date_to: str = '',
) -> tuple:
    """
    Vrati (start_dt, end_dt, label, period_key, date_from_iso, date_to_iso).

    period: day | month | year | range
    """
    today = timezone.localdate()
    period = (period or 'day').strip().lower()
    if period not in ('day', 'month', 'year', 'range'):
        period = 'day'

    def _parse(s: str):
        s = (s or '').strip()[:10]
        if not s:
            return None
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None

    if period == 'month':
        start_d = today.replace(day=1)
        end_d = today
        label = f'{start_d.strftime("%m.%Y.")} (do danas)'
    elif period == 'year':
        start_d = date(today.year, 1, 1)
        end_d = today
        label = f'{today.year}. (do danas)'
    elif period == 'range':
        start_d = _parse(date_from) or today
        end_d = _parse(date_to) or today
        if end_d < start_d:
            start_d, end_d = end_d, start_d
        # Cap 92 dana — štiti CPU na 0.5 planu
        if (end_d - start_d).days > 92:
            start_d = end_d - timedelta(days=92)
        label = f'{start_d.strftime("%d.%m.%Y.")} – {end_d.strftime("%d.%m.%Y.")}'
        period = 'range'
    else:
        start_d = today
        end_d = today
        label = today.strftime('%d.%m.%Y.')
        period = 'day'

    start_dt, _ = _day_bounds(start_d)
    _, end_dt = _day_bounds(end_d)
    return (
        start_dt,
        end_dt,
        label,
        period,
        start_d.isoformat(),
        end_d.isoformat(),
    )


def _visitors_in_range_qs(start, end):
    """
    Sesije s first_seen u periodu.

    Filtrira bot/šum:
    - staff / excluded IP
    - prazan session
    - health/api/admin/static putanje
    - mora imati IP ili visitor_token (session-only ≈ bot)
    """
    qs = visitors_analytics_qs().filter(
        first_seen__gte=start,
        first_seen__lte=end,
    ).exclude(session_key='')

    qs = qs.exclude(
        Q(trenutna_putanja__startswith='/healthz')
        | Q(trenutna_putanja__startswith='/api/')
        | Q(trenutna_putanja__startswith='/uzivo/')
        | Q(trenutna_putanja__startswith='/admin/')
        | Q(trenutna_putanja__startswith='/static/')
        | Q(trenutna_putanja='/favicon.ico')
        | Q(trenutna_putanja='/robots.txt')
        | Q(trenutna_putanja='/sitemap.xml')
        | Q(trenutna_putanja='/facebook-feed.xml')
    )
    # Session-only bez IP/tokena — botovi / headless bez cookie-a
    qs = qs.exclude(
        Q(visitor_token='') | Q(visitor_token__isnull=True),
        ip_adresa__isnull=True,
    )
    return qs


def _unique_people_and_sources(rows) -> tuple[int, list[dict], int]:
    """
    Iz liste {token, ip, session, izvor}:
    - broj jedinstvenih ljudi (token/IP)
    - izvori po ljudima
    - broj odbačenih session-only redova (bot signal)
    """
    person_source: dict[str, str] = {}
    skipped_bot = 0
    for row in rows:
        key = _person_key(
            row.get('visitor_token') or '',
            row.get('ip_adresa'),
            row.get('session_key') or '',
        )
        if not key:
            skipped_bot += 1
            continue
        if key in person_source:
            continue
        person_source[key] = normalize_traffic_source(row.get('izvor_dolaska'))

    visitors_count = len(person_source)
    counts: dict[str, int] = {k: 0 for k in SOURCE_DISPLAY_ORDER}
    for src in person_source.values():
        counts[src] = counts.get(src, 0) + 1

    traffic_sources = []
    for key in SOURCE_DISPLAY_ORDER:
        n = counts.get(key, 0)
        if n <= 0:
            continue
        traffic_sources.append({
            'key': key,
            'label': traffic_source_label(key),
            'short_label': traffic_source_label(key, short=True),
            'visitors': n,
            'share': _pct(n, visitors_count),
        })
    traffic_sources.sort(key=lambda r: -r['visitors'])
    return visitors_count, traffic_sources, skipped_bot


def build_site_overview(
    *,
    period: str = 'day',
    date_from: str = '',
    date_to: str = '',
) -> dict:
    """
    Lagani pregled (0.5 CPU): jedinstveni posjetioci + kupovine + izvori.

    period: day | month | year | range (+ date_from/date_to YYYY-MM-DD)
    """
    from django.core.cache import cache

    start, end, label, period_key, from_iso, to_iso = _period_bounds(
        period=period,
        date_from=date_from,
        date_to=date_to,
    )
    cache_key = f'staff_overview_v5:{period_key}:{from_iso}:{to_iso}'
    cached = cache.get(cache_key)
    if cached is not None:
        cached = dict(cached)
        cached['generated_at'] = timezone.localtime()
        cached['from_cache'] = True
        return cached

    # Jedan SELECT potrebnih polja (cap radi CPU-a)
    visitors_qs = _visitors_in_range_qs(start, end)
    rows = list(
        visitors_qs.order_by('first_seen').values(
            'visitor_token', 'ip_adresa', 'session_key', 'izvor_dolaska',
        )[:25000]
    )
    visitors_count, traffic_sources, bots_skipped = _unique_people_and_sources(rows)
    sessions_raw = len(rows)

    orders_qs = _orders_qs().filter(kreirana__gte=start, kreirana__lte=end)
    agg = orders_qs.aggregate(
        orders=Count('id'),
        revenue=Sum('ukupno'),
        buyers=Count('email', distinct=True, filter=~Q(email='')),
    )
    orders_n = int(agg['orders'] or 0)
    revenue = _money(agg['revenue'])
    buyers_n = int(agg['buyers'] or 0)
    avg_order = _money(revenue / orders_n) if orders_n else Decimal('0.00')
    conversion = _pct(orders_n, visitors_count)

    payload = {
        'period': period_key,
        'period_label': label,
        'date_from': from_iso,
        'date_to': to_iso,
        'today_label': label,
        'visitors': visitors_count,
        'sessions_raw': sessions_raw,
        'bots_filtered': bots_skipped,
        'orders': orders_n,
        'buyers': buyers_n,
        'revenue': revenue,
        'avg_order': avg_order,
        'conversion': conversion,
        'traffic_sources': traffic_sources,
        'source_labels': SOURCE_LABELS,
        'generated_at': timezone.localtime(),
        'from_cache': False,
        'cache_seconds': 45,
        'period_choices': (
            ('day', 'Danas'),
            ('month', 'Ovaj mjesec'),
            ('year', 'Ova godina'),
            ('range', 'Datumi'),
        ),
    }
    cache.set(cache_key, {**payload, 'from_cache': True}, 45)
    return payload
