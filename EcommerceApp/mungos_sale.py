"""Read-only price projection; webshop remains the source of truth."""
from django.utils import timezone

from .mungos_payload import sanitize_mungos_price


def product_price(product, selling_source):
    regular = sanitize_mungos_price(product.bazna_cijena)
    selling = sanitize_mungos_price(selling_source)
    end = None
    if regular is not None and selling is not None and selling < regular:
        # A flash price may have a different lifetime. Never borrow the normal
        # campaign date when a flash discount contributes to the winning price.
        flash = product.flash_sale_price(product.bazna_cijena)
        if (product.akcija_do and product.akcija_do >= timezone.localdate()
                and product.akcijska_cijena == selling_source
                and (flash is None or flash > selling_source)):
            end = product.akcija_do.isoformat()
    return {'Price': regular, 'SellingPrice': selling, 'Currency': None,
            'IsNegotiable': False, 'IsFree': False, 'DiscountEndDate': end}
