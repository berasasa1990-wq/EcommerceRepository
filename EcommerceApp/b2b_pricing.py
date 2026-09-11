from decimal import Decimal, ROUND_HALF_UP
from .models import B2BOfferItem, B2BBrandPricing

def account_rabat_percent(account):
    if not account or not getattr(account, 'rabat', False):
        return Decimal('0')
    percent = getattr(account, 'rabat_postotak', None)
    if percent is None or percent <= 0:
        return Decimal('0')
    return Decimal(str(percent))


def volume_discount_for_netto(netto_total, percent=None):
    """Immediate rabat off VPC netto, no minimum."""
    percent = Decimal(str(percent or 0))
    netto_total = Decimal(str(netto_total or 0))
    if percent <= 0:
        return Decimal('0.00')
    return (netto_total * percent / Decimal('100')).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP)


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


def order_rabat_percent(order):
    for row in (getattr(order, 'popust_detalji', None) or []):
        if row.get('rabat'):
            return Decimal(str(row.get('rabat_percent') or 0))
    from .models import B2BSubmission
    submission = B2BSubmission.objects.filter(order_id=order.pk).select_related('account').first()
    return account_rabat_percent(submission.account) if submission else Decimal('0')


def order_has_rabat(order):
    return order_rabat_percent(order) > 0


def b2b_volume_discount_note(order, items=None):
    """Print note when B2B rabat was applied, using billed qty."""
    percent = order_rabat_percent(order)
    if percent <= 0:
        return ''
    items = list(items if items is not None else order.stavke.all())
    net_total = Decimal('0.00')
    has_b2b = False
    for item in items:
        snap = item.b2b_pricing_snapshot or {}
        if snap:
            has_b2b = True
        qty = int(item.kolicina_faktura or 0)
        if qty <= 0:
            continue
        if snap.get('netto'):
            net_total += Decimal(str(snap['netto'])) * qty
        elif snap:
            net_total += (Decimal(str(item.cijena or 0)) / Decimal('1.17')).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP) * qty
    if not has_b2b:
        return ''
    netto_after, discount, _gross, _tax = apply_volume_discount(net_total, percent=percent)
    if discount <= 0:
        return ''
    pct = format(percent.normalize(), 'f')
    return (
        f'Ostvaren rabat −{pct}%: −{discount:.2f} KM netto '
        f'(VPC netto {net_total:.2f} → {netto_after:.2f} KM).'
    )


def price_snapshot(product, variation, discounts, divisors):
    mpc = variation.bazna_cijena if variation else product.cijena
    divisor = divisors.get(product.brend_id, Decimal('1.38'))
    return {
        'mpc': str(mpc), 'divisor': str(divisor),
        'brand_override': product.brend_id in divisors,
        'brand': product.brend.naziv if product.brend_id else '',
        'original_netto': str(base_net(mpc, divisor)),
        'netto': str(net_price(product, variation, discounts, divisors)),
        'discount_percent': str(discounts.get(product.pk, 0)),
    }
