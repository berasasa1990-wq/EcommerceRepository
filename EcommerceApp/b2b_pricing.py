from decimal import Decimal, ROUND_HALF_UP
from .models import B2BOfferItem, B2BBrandPricing


def account_brand_rabats(account):
    if not account:
        return {}
    cached = getattr(account, '_brand_rabat_map', None)
    if cached is not None:
        return cached
    mapping = {}
    for row in account.brand_rabats.all():
        if row.postotak and row.postotak > 0:
            mapping[row.brand_id] = Decimal(str(row.postotak))
    account._brand_rabat_map = mapping
    return mapping


def rabat_percent_for_product(product, rabats):
    brand_id = getattr(product, 'brend_id', None)
    if not brand_id:
        return Decimal('0')
    return Decimal(str(rabats.get(brand_id) or 0))


def volume_discount_for_netto(netto_total, percent=None):
    """Immediate rabat off VPC netto, no minimum."""
    percent = Decimal(str(percent or 0))
    netto_total = Decimal(str(netto_total or 0))
    if percent <= 0:
        return Decimal('0.00')
    return (netto_total * percent / Decimal('100')).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP)


def rabat_totals(lines, line_gross=None):
    """lines: iterable of (netto_unit, qty, percent). Return netto_after, discount, gross, tax."""
    total = Decimal('0.00')
    discount = Decimal('0.00')
    for unit, qty, percent in lines:
        line = Decimal(str(unit or 0)) * int(qty or 0)
        total += line
        discount += volume_discount_for_netto(line, percent)
    total = total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    discount = discount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    netto_after = (total - discount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    if discount:
        gross_after = (netto_after * Decimal('1.17')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    elif line_gross is not None:
        gross_after = Decimal(str(line_gross)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    else:
        gross_after = (netto_after * Decimal('1.17')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    tax = (gross_after - netto_after).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return netto_after, discount, gross_after, tax


def apply_volume_discount(netto_total, line_gross=None, percent=None):
    """Return (netto_after, discount, gross_after, tax)."""
    netto_total = Decimal(str(netto_total or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    discount = volume_discount_for_netto(netto_total, percent)
    netto_after = (netto_total - discount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    if discount:
        gross_after = (netto_after * Decimal('1.17')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    elif line_gross is not None:
        gross_after = Decimal(str(line_gross)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    else:
        gross_after = (netto_after * Decimal('1.17')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    tax = (gross_after - netto_after).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return netto_after, discount, gross_after, tax


def discounts_for(product_ids):
    return dict(B2BOfferItem.objects.filter(settings_id=1, product_id__in=product_ids).values_list('product_id', 'discount_percent'))


def brand_divisors():
    return dict(B2BBrandPricing.objects.filter(settings_id=1).values_list('brand_id', 'divisor'))


def base_net(mpc, divisor=Decimal('1.38')):
    return (mpc / divisor / Decimal('1.17')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def net_price(product, variation, discounts, divisors):
    base = base_net(variation.bazna_cijena if variation else product.cijena, divisors.get(product.brend_id, Decimal('1.38')))
    percent = discounts.get(product.pk, Decimal('0'))
    return (base * (Decimal('1') - percent / Decimal('100'))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def invoice_price_notes(item, quantity):
    """Print historical order terms, never today's brand rules or offers."""
    snapshot = item.b2b_pricing_snapshot
    notes = []
    if snapshot:
        original = Decimal(snapshot['original_netto'])
        price = Decimal(snapshot['netto'])
        percent = Decimal(snapshot['discount_percent'])
        if snapshot.get('brand_override'):
            divisor = format(Decimal(snapshot['divisor']).normalize(), 'f')
            notes.append(f"Posebna VPC cijena — {snapshot['brand']}: MPC / {divisor} / 1,17 = {original:.2f} KM netto.")
        if percent > 0:
            saving = original - price
            notes.append(f"Akcijski popust −{percent.normalize():f}%: {original:.2f} → {price:.2f} KM netto; "
                         f"sniženo {saving:.2f} KM/kom, ukupno {saving * quantity:.2f} KM.")
        rabat_percent = Decimal(str(snapshot.get('rabat_percent') or 0))
        if rabat_percent > 0:
            brand = snapshot.get('rabat_brand') or snapshot.get('brand') or ''
            line = price * quantity
            rabat_saving = volume_discount_for_netto(line, rabat_percent)
            label = f' {brand}' if brand else ''
            notes.append(
                f"Rabat{label} −{rabat_percent.normalize():f}%: "
                f"−{rabat_saving:.2f} KM netto na {quantity} kom."
            )
        if notes:
            notes.append(f'VPC netto za fakturu: {price:.2f} KM/kom.')
    elif item.bazna_cijena is not None and item.bazna_cijena > item.cijena:
        saving = item.bazna_cijena - item.cijena
        percent = item.popust_postotak
        if percent is None:
            percent = (saving / item.bazna_cijena * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        notes.append(f'Sniženje −{percent.normalize():f}%: {item.bazna_cijena:.2f} → {item.cijena:.2f} KM; '
                     f'sniženo {saving:.2f} KM/kom, ukupno {saving * quantity:.2f} KM.')
    elif item.popust_postotak and item.popust_postotak > 0:
        notes.append(f'Sniženje −{item.popust_postotak.normalize():f}%.')
    return notes


def order_rabat_map(order):
    for row in (getattr(order, 'popust_detalji', None) or []):
        brands = row.get('rabat_brands')
        if row.get('rabat') and brands:
            return {
                int(item['brand_id']): Decimal(str(item['percent']))
                for item in brands if item.get('brand_id') and Decimal(str(item.get('percent') or 0)) > 0
            }
    from .models import B2BSubmission
    submission = B2BSubmission.objects.filter(order_id=order.pk).select_related('account').prefetch_related(
        'account__brand_rabats').first()
    return account_brand_rabats(submission.account) if submission else {}


def b2b_volume_discount_notes(order, items=None):
    """Print notes when B2B brand rabat was applied, using billed qty."""
    items = list(items if items is not None else order.stavke.all())
    grouped = {}
    for item in items:
        snap = item.b2b_pricing_snapshot or {}
        qty = int(item.kolicina_faktura or 0)
        if qty <= 0:
            continue
        percent = Decimal(str(snap.get('rabat_percent') or 0))
        if percent <= 0:
            continue
        if snap.get('netto'):
            unit = Decimal(str(snap['netto']))
        else:
            unit = (Decimal(str(item.cijena or 0)) / Decimal('1.17')).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP)
        line = unit * qty
        disc = volume_discount_for_netto(line, percent)
        if disc <= 0:
            continue
        brand = snap.get('rabat_brand') or snap.get('brand') or 'brend'
        key = (brand, percent)
        net, saving = grouped.get(key, (Decimal('0.00'), Decimal('0.00')))
        grouped[key] = (net + line, saving + disc)
    notes = []
    for (brand, percent), (net, saving) in grouped.items():
        after = (net - saving).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        notes.append(
            f'Ostvaren rabat {brand} −{format(percent.normalize(), "f")}%: −{saving:.2f} KM netto '
            f'(VPC netto {net:.2f} → {after:.2f} KM).'
        )
    return notes


def price_snapshot(product, variation, discounts, divisors, rabat_percent=None):
    mpc = variation.bazna_cijena if variation else product.cijena
    divisor = divisors.get(product.brend_id, Decimal('1.38'))
    percent = Decimal(str(rabat_percent or 0))
    return {
        'mpc': str(mpc), 'divisor': str(divisor),
        'brand_override': product.brend_id in divisors,
        'brand': product.brend.naziv if product.brend_id else '',
        'original_netto': str(base_net(mpc, divisor)),
        'netto': str(net_price(product, variation, discounts, divisors)),
        'discount_percent': str(discounts.get(product.pk, 0)),
        'rabat_percent': str(percent),
        'rabat_brand': product.brend.naziv if percent > 0 and product.brend_id else '',
    }
