"""End timed promotions before storefront prices are read."""
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import Product


def expire_promotions():
    expired = Product.objects.filter(sale_ends_at__lte=timezone.now())
    if not expired.exists():
        return
    with transaction.atomic():
        # Restore the regular price before clearing the old-price reference.
        expired.filter(original_price__gt=F('price')).update(price=F('original_price'))
        expired.update(sale_ends_at=None, sale_type='regular', original_price=None,
                       plus_product=None, plus_percent=None, bundle_percent=None,
                       quantity_percent_2=None, quantity_percent_3=None,
                       quantity_percent_over_3=None)


class PromotionExpiryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        expire_promotions()
        return self.get_response(request)
