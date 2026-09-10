"""Physical stock history, with order and location details preserved at movement time."""
import re

ORDER_NOTE = re.compile(r'(?:Validacija|Web narudžba|B2B picking|Prenos u MP|Prodaja|picking)\s*#(\d+)', re.I)


def location_label(location):
    if not location:
        return ''
    return ' — '.join(value for value in [location.sifra, location.naziv] if value)


def capture_movement(movement):
    from .models import Order
    if not movement.order_id:
        match = ORDER_NOTE.search(movement.napomena or '')
        if match:
            movement.order = Order.objects.filter(broj=match.group(1)).first()
    if movement.order_id:
        movement.order_number = movement.order.broj
        movement.customer_name = movement.order.ime_prezime
    movement.source_label = location_label(movement.location)
    movement.destination_label = location_label(movement.to_location)


def physical_movements(queryset):
    return queryset.exclude(tip='rezervacija').exclude(kolicina=0)


def attach_history(movements):
    from .models import Order
    movements = list(movements)
    numbers = {}
    for movement in movements:
        match = ORDER_NOTE.search(movement.napomena or '')
        numbers[movement.pk] = movement.order_number or (match.group(1) if match else '')
    orders = {order.broj: order for order in Order.objects.filter(broj__in=set(numbers.values()) - {''})}
    for movement in movements:
        number = numbers[movement.pk]
        order = movement.order if movement.order_id else orders.get(number)
        movement.history_order_number = number
        movement.history_order_link = order.broj if order else ''
        movement.history_customer = movement.customer_name or (order.ime_prezime if order else '')
        source = movement.source_label or location_label(movement.location)
        destination = movement.destination_label or location_label(movement.to_location)
        movement.history_source = source if movement.tip in {'prodaja', 'transfer'} or movement.kolicina < 0 else ''
        movement.history_destination = destination if movement.tip == 'transfer' else (source if movement.kolicina > 0 and movement.tip != 'prodaja' else '')
        movement.history_source_short = movement.history_source.split(' — ', 1)[0]
        movement.history_destination_short = movement.history_destination.split(' — ', 1)[0]
        movement.history_amount = abs(movement.kolicina)
        if movement.tip == 'transfer':
            movement.history_type = 'Prenos u MP' if ('prenos u mp' in (movement.napomena or '').lower() or (order and (order.pick_state or {}).get('kind') == 'prenos_mp')) else 'Transfer iz lokacije u lokaciju'
            movement.history_quantity = f'−{abs(movement.kolicina)} → +{abs(movement.kolicina)}'
        elif movement.tip == 'prodaja':
            movement.history_type = 'Prodaja'
            movement.history_quantity = f'−{abs(movement.kolicina)}'
        else:
            movement.history_type = 'Dodavanje u lokaciju' if movement.kolicina > 0 else 'Skidanje sa lokacije'
            movement.history_quantity = f'{movement.kolicina:+d}'
    return movements
