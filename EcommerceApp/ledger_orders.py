"""Customer order lookup shared by ledger display and missing-item validation."""
from django.db.models import Q, Value
from django.db.models.functions import Replace

from .models import Order, WarehouseCustomer


def customer_orders(partner):
    if not partner:
        return Order.objects.none()
    customer = partner.customer if partner.customer_id else partner
    name = customer.ime_prezime if partner.customer_id else partner.naziv
    email = getattr(customer, "email", "")
    phone = customer.telefon or ''
    expression = 'telefon'
    for character in [' ', '+', '-', '(', ')', '/', '.']:
        expression = Replace(expression, Value(character), Value(''))
        phone = phone.replace(character, '')
    phones = {phone} if phone else set()
    if phone.startswith('00387'):
        phones.add(phone[2:])
        phones.add('0' + phone[5:])
    elif phone.startswith('387'):
        phones.add('0' + phone[3:])
        phones.add('00' + phone)
    elif phone.startswith('0'):
        phones.add('387' + phone[1:])
        phones.add('00387' + phone[1:])
    contact = Q(pk__in=[])
    if phones:
        contact |= Q(ledger_phone__in=phones)
    if email:
        contact |= Q(email__iexact=email)
    # Explicit links take precedence over contact snapshots on older/webshop orders.
    fallback = Q(ime_prezime__iexact=name) & contact
    if email and not email.endswith('.local') and WarehouseCustomer.objects.filter(email__iexact=email).count() == 1:
        fallback |= Q(email__iexact=email) | Q(korisnik__email__iexact=email)
    if not phones and not email:
        fallback = Q(ime_prezime__iexact=name, telefon='', email='')
    conflicting = Order.objects.filter(vp_nacrti__customer__isnull=False)
    if partner.customer_id:
        conflicting = conflicting.exclude(vp_nacrti__customer=customer)
    conflicting = conflicting.values('pk')
    explicit = Q(vp_nacrti__customer=customer) if partner.customer_id else Q(pk__in=[])
    return (Order.objects.annotate(ledger_phone=expression)
            .filter(Q(ledger_replacement_line__entry__partner=partner) | explicit | (fallback & ~Q(pk__in=conflicting)))
            .distinct().order_by('-kreirana', '-pk'))
