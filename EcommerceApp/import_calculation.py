"""Decimal calculations for warehouse invoices (recomputed on the server)."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json

NUMBERS = ('kurs', 'iznos_fakture', 'prevoz', 'carina', 'pdv', 'ostali_troskovi')


def number(value):
    text = str(value or '0').strip().replace(' ', '').replace('\u00a0', '')
    if ',' in text:
        text = text.replace('.', '').replace(',', '.')
    try:
        result = Decimal(text)
        if not result.is_finite() or result < 0 or result > Decimal('999999999'):
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('Unesi ispravne nenegativne brojeve za kalkulaciju.') from exc


def calculation(raw):
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        raise ValueError('Neispravni podaci kalkulacije.') from exc
    if not isinstance(data, dict):
        raise ValueError('Neispravni podaci kalkulacije.')
    values = {key: number(data.get(key)) for key in NUMBERS}
    if values['kurs'] <= 0 or values['iznos_fakture'] <= 0:
        raise ValueError('Kurs i iznos fakture moraju biti veći od nule.')
    invoice_km = values['iznos_fakture'] * values['kurs']
    percent = (values['carina'] + values['prevoz'] + values['ostali_troskovi']) / invoice_km * 100
    return {**{key: str(value) for key, value in values.items()},
            'jci': str(data.get('jci', ''))[:200], 'faktura': str(data.get('faktura', ''))[:200],
            'faktura_km': str(invoice_km.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)),
            'trosak_bez_pdv_pct': str(percent.quantize(Decimal('.000001'), rounding=ROUND_HALF_UP))}


def calculate_rows(rows, config):
    from .models import Product
    products = {}
    for product in Product.objects.filter(naziv__in=[row['artikal'] for row in rows], obrisan_at__isnull=True):
        products.setdefault(product.naziv, []).append(product)
    result = []
    for original in rows:
        row = dict(original)
        mpc = row.get('mpc_brutto')
        matches = products.get(row['artikal'], [])
        if mpc is None and len(matches) == 1:
            mpc = matches[0].cijena
        row['mpc_brutto'] = mpc
        invoice = row.get('fakturna')
        qty = row.get('kolicina')
        row['ukupno_fakturna'] = (invoice * qty).quantize(Decimal('.01'), rounding=ROUND_HALF_UP) if invoice is not None and qty is not None else None
        row['vpc_netto'] = (mpc / Decimal('1.17') / Decimal('1.38')).quantize(Decimal('.0001'), rounding=ROUND_HALF_UP) if mpc is not None else None
        row['nabavna'] = (invoice * Decimal(config['kurs']) * (1 + (Decimal(config['carina']) + Decimal(config['prevoz']) + Decimal(config['ostali_troskovi'])) / (Decimal(config['iznos_fakture']) * Decimal(config['kurs'])))).quantize(Decimal('.0001'), rounding=ROUND_HALF_UP) if invoice is not None else None
        row['vpc_marza'] = (row['vpc_netto'] / row['nabavna'] * 100).quantize(Decimal('.000001'), rounding=ROUND_HALF_UP) if row['nabavna'] and row['vpc_netto'] is not None else None
        for field in ('nabavna', 'vpc_netto'):
            if row[field] is not None and row[field] >= Decimal('100000000'):
                raise ValueError('Izračunata cijena prelazi dozvoljeni iznos.')
        if row['vpc_marza'] is not None and row['vpc_marza'] >= Decimal('1000000'):
            raise ValueError('Izračunata VPC marža prelazi dozvoljeni iznos.')
        if row['ukupno_fakturna'] is not None and row['ukupno_fakturna'] >= Decimal('1000000000000'):
            raise ValueError('Ukupna fakturna prelazi dozvoljeni iznos.')
        result.append(row)
    return result
