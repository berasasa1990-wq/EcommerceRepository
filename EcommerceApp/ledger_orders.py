"""Customer order lookup shared by ledger display and missing-item validation."""
from django.db.models import Q, Value
from django.db.models.functions import Replace

from .models import Order


def customer_orders(partner):
    if not partner or not partner.customer_id:
        return Order.objects.none()
    customer = partner.customer
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
    if customer.email:
        contact |= Q(email__iexact=customer.email)
    # Explicit links take precedence over contact snapshots on older/webshop orders.
    fallback = Q(ime_prezime__iexact=customer.ime_prezime) & contact
    conflicting = Order.objects.filter(vp_nacrti__customer__isnull=False).exclude(vp_nacrti__customer=customer).values('pk')
    return (Order.objects.annotate(ledger_phone=expression)
            .filter(Q(ledger_replacement_line__entry__partner=partner) | Q(vp_nacrti__customer=customer) | (fallback & ~Q(pk__in=conflicting)))
            .distinct().order_by('-kreirana', '-pk'))
