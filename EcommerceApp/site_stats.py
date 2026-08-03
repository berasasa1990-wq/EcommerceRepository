"""
Pregled sajta — agregati posjeta, kupovina i prometa (online).

Izvori:
  - LiveVisitor.first_seen → posjetioci (sesije)
  - Order (bez otkazanih) → narudžbe, promet (ukupno KM)
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth, TruncYear
from django.utils import timezone

from .models import LiveVisitor, Order


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
    # Distinct email kupaca (gost + nalog)
    buyers_count = (
        orders.exclude(email='')
        .values('email')
        .distinct()
        .count()
    )
    orders_count = orders.count()
    revenue = _money(orders.aggregate(t=Sum('ukupno'))['t'])
    avg_order = _money(revenue / orders_count) if orders_count else Decimal('0.00')

    return {
        'visitors': visitors_count,
        'buyers': buyers_count,
        'orders': orders_count,
        'revenue': revenue,
        'avg_order': avg_order,
        'conversion_visitors': _pct(orders_count, visitors_count),
        'conversion_buyers': _pct(buyers_count, visitors_count),
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
    # Početak: months-1 mjeseci unazad
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

    totals_all = summary_totals()
    # Period totals = sum of visible rows (consistent with table)
    period_visitors = sum(r['visitors'] for r in rows)
    period_orders = sum(r['orders'] for r in rows)
    period_buyers = sum(r['buyers'] for r in rows)  # not unique across days — OK for display note
    period_revenue = sum((r['revenue'] for r in rows), Decimal('0.00'))

    return {
        'period': period,
        'period_label': period_label,
        'rows': rows,
        'totals_all': totals_all,
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
        'generated_at': timezone.localtime(),
    }
