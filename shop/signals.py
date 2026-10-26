from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import LoyaltyCard


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_loyalty_card(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        if 'loyalty' in settings.BERA_LICENSE_ENFORCED_MODULES:
            from bera_licensing.services import module_enabled
            if not module_enabled('loyalty'):
                return
        LoyaltyCard.objects.get_or_create(customer=instance)


from .models import Order


@receiver(post_save, sender=Order)
def update_order_points(sender, instance, raw=False, **kwargs):
    from .modules import loyalty_enabled
    if not raw and loyalty_enabled():
        from .loyalty import settle_points
        settle_points(instance.pk)
