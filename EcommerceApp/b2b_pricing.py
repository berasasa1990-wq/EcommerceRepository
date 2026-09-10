from decimal import Decimal, ROUND_HALF_UP
from .models import B2BOfferItem, B2BBrandPricing


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
