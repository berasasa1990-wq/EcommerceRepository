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
)
from .models import (
    ActiveCartItem,
    ChatConversation,
    ChatMessage,
    LiveVisitor,
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
    visitors = LiveVisitor.objects.all()
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
    qs = LiveVisitor.objects.all()
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
        LiveVisitor.objects.exclude(email='')
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
    qs = LiveVisitor.objects.order_by('first_seen').values_list('session_key', 'izvor_dolaska')
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
    visitors = LiveVisitor.objects.all()
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


def _bucket_rows(trunc_fn, *, start, end, label_fmt, period: str) -> list[dict]:
    """
    Spoji posjetioce (first_seen) i narudžbe (kreirana) po bucketu.
    """
    visitors = (
        LiveVisitor.objects.filter(first_seen__gte=start, first_seen__lte=end)
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
        b = row['bucket']
        if b is None:
            continue
        key = _as_local(b) if hasattr(b, 'tzinfo') else b
        by_bucket[key] = {
            'bucket': key,
            'visitors': int(row['visitors'] or 0),
            'orders': 0,
            'buyers': 0,
            'revenue': Decimal('0.00'),
        }

    for row in orders:
        b = row['bucket']
        if b is None:
            continue
        key = _as_local(b) if hasattr(b, 'tzinfo') else b
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
        if isinstance(b, datetime):
            label = b.strftime(label_fmt)
            sort_key = b
        elif isinstance(b, date):
            label = b.strftime(label_fmt)
            sort_key = b
        else:
            label = str(b)
            sort_key = b
        orders_n = e['orders']
        rev = e['revenue']
        rows.append({
            'period': period,
            'label': label,
            'sort_key': sort_key,
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


def build_site_overview(*, period: str = 'day') -> dict:
    """
    Kompletan payload za staff dashboard.
    period: day | month | year
    """
    period = (period or 'day').strip().lower()
    if period not in ('day', 'month', 'year'):
        period = 'day'

    if period == 'month':
        rows = stats_by_month(months=24)
        period_label = 'Po mjesecima (zadnja 24)'
    elif period == 'year':
        rows = stats_by_year(years=8)
        period_label = 'Po godinama'
    else:
        rows = stats_by_day(days=31)
        period_label = 'Po danima (zadnjih 31)'

    window_start, window_end, window_label = _period_window(period)

    totals_all = summary_totals()
    totals_window = summary_totals(start=window_start, end=window_end)

    period_visitors = sum(r['visitors'] for r in rows)
    period_orders = sum(r['orders'] for r in rows)
    period_buyers = sum(r['buyers'] for r in rows)
    period_revenue = sum((r['revenue'] for r in rows), Decimal('0.00'))

    sources = traffic_source_breakdown(start=window_start, end=window_end)
    sources_all = traffic_source_breakdown()
    sources_orders = orders_by_traffic_source(start=window_start, end=window_end)
    engagement = engagement_stats(start=window_start, end=window_end)
    chat = chat_stats(start=window_start, end=window_end)

    # Brzi danas / jučer
    today = timezone.localdate()
    yesterday = today - timedelta(days=1)
    t0, t1 = _day_bounds(today)
    y0, y1 = _day_bounds(yesterday)
    today_stats = summary_totals(start=t0, end=t1)
    yesterday_stats = summary_totals(start=y0, end=y1)

    # Highlight kartice izvora (top kanali)
    top_sources = [s for s in sources if s['visitors'] > 0][:6]

    return {
        'period': period,
        'period_label': period_label,
        'window_label': window_label,
        'rows': rows,
        'totals_all': totals_all,
        'totals_window': totals_window,
        'totals_period': {
            'visitors': period_visitors,
            'orders': period_orders,
            'buyers': period_buyers,
            'revenue': _money(period_revenue),
            'avg_order': (
                _money(period_revenue / period_orders) if period_orders else Decimal('0.00')
            ),
            'conversion': _pct(period_orders, period_visitors),
        },
        'today': today_stats,
        'yesterday': yesterday_stats,
        'traffic_sources': sources,
        'traffic_sources_all': sources_all,
        'traffic_sources_orders': sources_orders,
        'top_sources': top_sources,
        'engagement': engagement,
        'chat': chat,
        'source_labels': SOURCE_LABELS,
        'generated_at': timezone.localtime(),
    }
