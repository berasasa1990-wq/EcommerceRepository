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


def ensure_order_partners():
    """Expose historical ordering customers without changing orders or ledger balances."""
    from django.db import transaction
    from django.db.models import Q
    from .models import Order, WarehouseCustomer, SiteSettings
    from .views_magacin import _customer_phone_key

    with transaction.atomic():
        SiteSettings.objects.get_or_create(pk=1)
        SiteSettings.objects.select_for_update().get(pk=1)
        customers = list(WarehouseCustomer.objects.all())
        partner_customer_ids = set(WarehousePartner.objects.exclude(customer_id=None).values_list('customer_id', flat=True))
        contacts = {}
        emails = {}
        for customer in customers:
            name = customer.ime_prezime.strip().casefold()
            phone = _customer_phone_key(customer.telefon)
            if phone:
                contacts.setdefault((name, phone), customer)
            if customer.email:
                emails.setdefault(customer.email.strip().casefold(), []).append(customer)
        orders = (Order.objects.exclude(Q(ime_prezime__iexact='Prenos u MP') | Q(pick_state__kind__isnull=False, pick_state__kind='prenos_mp'))
                  .prefetch_related('vp_nacrti__customer').order_by('pk'))
        for order in orders.iterator(chunk_size=200):
            name = (order.ime_prezime or '').strip()
            if not name:
                continue
            linked = next((draft.customer for draft in order.vp_nacrti.all() if draft.customer_id), None)
            phone = _customer_phone_key(order.telefon)
            email = (order.email or '').strip().casefold()
            email_matches = emails.get(email, []) if email else []
            customer = linked or contacts.get((name.casefold(), phone))
            if customer is None and len(email_matches) == 1 and not email.endswith('.local'):
                customer = email_matches[0]
            if customer is None:
                # Without a usable contact, keep separate named historical identities.
                if not phone and not email:
                    customer = next((c for c in customers if c.ime_prezime.strip().casefold() == name.casefold()
                                     and not _customer_phone_key(c.telefon) and not c.email), None)
                if customer is None:
                    customer = WarehouseCustomer.objects.create(
                        ime_prezime=name[:200], telefon=(order.telefon or '')[:30],
                        email=email[:254], adresa=(order.adresa or '')[:300],
                        grad=(order.grad or '')[:100], postanski_broj=(order.postanski_broj or '')[:20],
                    )
                    customers.append(customer)
                    if phone:
                        contacts[(name.casefold(), phone)] = customer
                    if email:
                        emails.setdefault(email, []).append(customer)
        # bulk-created/legacy customers may not have triggered the partner signal.
        for customer in customers:
            if customer.pk in partner_customer_ids:
                continue
            WarehousePartner.objects.get_or_create(customer=customer, defaults={
                'naziv': customer.ime_prezime, 'telefon': customer.telefon,
                'adresa': customer.adresa, 'grad': customer.grad,
            })
