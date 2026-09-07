"""Keep ledger identities linked to the warehouse customer directory."""
from .models import WarehousePartner


def sync_customer_partner(sender, instance, raw=False, using='default', **kwargs):
    if raw:
        return
    # Read saved values, including when callers use update_fields/deferred instances.
    customer = sender.objects.using(using).get(pk=instance.pk)
    WarehousePartner.objects.using(using).update_or_create(
        customer_id=customer.pk,
        defaults={'naziv': customer.ime_prezime, 'telefon': customer.telefon,
                  'adresa': customer.adresa, 'grad': customer.grad},
    )
