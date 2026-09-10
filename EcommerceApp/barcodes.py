"""Barcode validation and a durable record of rejected assignments."""
from contextlib import contextmanager
from hashlib import sha256

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.db.models.functions import Lower, Trim
from django.utils import timezone


def normalized_barcode(value):
    return (value or '').strip().lower()


class DuplicateBarcode(ValidationError):
    pass


def conflicting_products(product, barcode):
    from .models import Product
    code = normalized_barcode(barcode)
    if not code:
        return Product.objects.none()
    return Product.objects.annotate(barcode_key=Lower(Trim('barkod'))).filter(barcode_key=code).exclude(pk=product.pk)


def record_conflicts(product, barcode, matches, user=None, existing=False):
    from .models import BarcodeConflict
    code = normalized_barcode(barcode)
    for other in matches:
        identity = str(product.pk) if product.pk else f'new:{product.naziv}:{product.sifra or ""}'
        key = sha256(f'{code}:{identity}:{other.pk}'.encode()).hexdigest()
        record, created = BarcodeConflict.objects.get_or_create(key=key, defaults={
            'barcode': (barcode or '').strip(), 'product': product if product.pk else None,
            'other_product': other, 'product_name': product.naziv or 'Novi artikal',
            'product_sku': product.sifra or '', 'other_name': other.naziv,
            'other_sku': other.sifra or '', 'existing_duplicate': existing,
            'reported_by': user if user and user.is_authenticated else None,
        })
        if not created:
            BarcodeConflict.objects.filter(pk=record.pk).update(last_seen=timezone.now(), attempts=F('attempts') + 1)


def validate_barcode(product, barcode, user=None, *, record=True):
    from .models import Product
    code = normalized_barcode(barcode)
    if not code:
        return
    if product.pk:
        old = Product.objects.filter(pk=product.pk).values_list('barkod', flat=True).first()
        if normalized_barcode(old) == code:
            return  # Existing collisions may be corrected without blocking other edits.
    matches = list(conflicting_products(product, barcode))
    if matches:
        if record:
            record_conflicts(product, barcode, matches, user)
        names = ', '.join(f'{p.naziv} (#{p.pk})' for p in matches[:3])
        raise DuplicateBarcode({'barkod': f'Dupli barkod {barcode.strip()}: već pripada artiklu {names}. '
                                'Artikal nije sačuvan. Evidentirano u Magacin → Dupli barkodovi.'})


@contextmanager
def barcode_save_guard(product, update_fields=None):
    from .models import BarcodeWriteLock, Product
    code = normalized_barcode(product.barkod)
    if not code or (update_fields is not None and 'barkod' not in update_fields):
        yield
        return
    old = Product.objects.filter(pk=product.pk).values_list('barkod', flat=True).first() if product.pk else None
    if normalized_barcode(old) == code:
        yield
        return
    validate_barcode(product, product.barkod)
    # Serialize assignments of this barcode, including concurrent new products.
    try:
        with transaction.atomic():
            BarcodeWriteLock.objects.get_or_create(code=code)
            BarcodeWriteLock.objects.select_for_update().get(code=code)
            validate_barcode(product, product.barkod, record=False)
            product.barkod = product.barkod.strip()
            yield
    except DuplicateBarcode:
        record_conflicts(product, product.barkod, conflicting_products(product, product.barkod))
        raise
