"""Atomic partner ledger operations using the existing warehouse movement service."""
import json
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from django.db import transaction
from django.db.models import Sum

from .magacin import MagacinError, apply_movement
from .ledger_orders import customer_orders
from .models import (Order, Product, ProductVariation, WarehouseLocation,
                     WarehouseMovement, WarehousePartner, WarehouseLedgerEntry as Entry,
                     WarehouseLedgerLine as Line)


def money(value):
    try:
        amount = Decimal(str(value).replace(',', '.'))
        if not amount.is_finite() or amount <= 0 or amount > Decimal('9999999999.99'):
            raise ValueError
        if amount != amount.quantize(Decimal('.01')):
            raise ValueError
        return amount
    except (ValueError, ArithmeticError):
        raise MagacinError('Unesi pozitivan iznos s najviše dvije decimale.')


def quantity(value):
    try:
        qty = int(str(value))
        if qty <= 0 or qty > 1000000:
            raise ValueError
        return qty
    except (ValueError, TypeError):
        raise MagacinError('Količina mora biti cijeli broj od 1 do 1.000.000.')


def stock_objects(data):
    try:
        product = Product.objects.get(pk=data.get('product_id'))
        variation = None
        if data.get('variation_id'):
            variation = ProductVariation.objects.get(pk=data['variation_id'], artikal=product)
        elif product.varijacije.exists():
            raise MagacinError('Odaberi varijaciju artikla.')
        location = WarehouseLocation.objects.get(pk=data.get('location_id'), aktivan=True)
        return product, variation, location
    except (Product.DoesNotExist, ProductVariation.DoesNotExist, WarehouseLocation.DoesNotExist, ValueError, TypeError):
        raise MagacinError('Odaberi važeći artikal, varijaciju i lokaciju.')


@transaction.atomic
def post_entry(*, partner_id, data, user):
    partner = WarehousePartner.objects.select_for_update().get(pk=partner_id)
    try:
        token = UUID(str(data.get('token', '')))
    except ValueError:
        raise MagacinError('Osvježi stranicu prije knjiženja.')
    existing = Entry.objects.filter(token=token).first()
    if existing:
        if existing.partner_id != partner.pk:
            raise MagacinError('Osvježi stranicu prije knjiženja.')
        return existing
    action = data.get('action')
    description = (data.get('description') or '').strip()[:300]
    common = dict(partner=partner, user=user, token=token)
    if action == 'delete_line':
        from .magacin import cancel_order_stock
        if not str(data.get('line_id') or '').isdigit():
            raise MagacinError('Odaberi artikal za brisanje.')
        line = Line.objects.select_related('entry', 'replacement_order').filter(pk=data['line_id'], entry__partner=partner).first()
        if not line:
            raise MagacinError('Artikal ovog kupca nije pronađen.')
        if line.voided_by_id:
            return line.voided_by
        if line.voided_by_id:
            raise MagacinError('Stavka je već obrisana.')
        if line.settled_by_id:
            raise MagacinError('Stavka je već izmirena uplatom.')
        if line.replacement_order_id:
            if line.replacement_order.lager_status == Order.LagerStatus.VALIDIRANO:
                raise MagacinError('Narudžba za ovaj artikal je već validatovana; nije moguće poništiti izvršeno slanje.')
            cancel_order_stock(line.replacement_order, user=user)
        remove_pending_excess_invoice(line)
        remove_pending_missing_items(line, user=user)
        returned = line.returns.aggregate(qty=Sum('returned_qty'), amount=Sum('amount'))
        remaining = max(0, line.quantity - (returned['qty'] or 0))
        value = max(Decimal('0'), line.amount - abs(returned['amount'] or Decimal('0')))
        negative = line.entry.kind in (Entry.Kind.CREDIT, Entry.Kind.MISSING, Entry.Kind.DAMAGED)
        reversal = Entry.objects.create(**common, kind=Entry.Kind.VOID,
            amount=value if negative else -value, source_line=line,
            description=f'Poništen pogrešan unos: {line.name}'[:300])
        if remaining and line.location_id and line.entry.kind in (Entry.Kind.DEBIT, Entry.Kind.CREDIT):
            apply_movement(product=line.product, variation=line.variation, location=line.location,
                tip=WarehouseMovement.Tip.PRIJEM if line.entry.kind == Entry.Kind.DEBIT else WarehouseMovement.Tip.PRODAJA,
                kolicina=remaining, user=user, napomena=f'Poništeno knjiženje #{line.entry_id}')
        line.voided_by = reversal
        line.save(update_fields=['voided_by'])
        return reversal
    if action in ('missing', 'excess', 'damaged'):
        entry_kind = {'missing': Entry.Kind.MISSING, 'excess': Entry.Kind.EXCESS, 'damaged': Entry.Kind.DAMAGED}[action]
        order_id = data.get('order_id')
        if not str(order_id or '').isdigit():
            raise MagacinError('Odaberi narudžbu ovog kupca.')
        allowed = customer_orders(partner).filter(pk=order_id).exists()
        order = Order.objects.select_for_update().filter(pk=order_id).first() if allowed else None
        if not order or order.status == Order.Status.OTKAZANA or order.lager_status == Order.LagerStatus.OTKAZANO:
            raise MagacinError('Odaberi neotkazanu narudžbu ovog kupca.')
        try:
            rows = json.loads(data.get('missing_json', '[]'))
        except (ValueError, TypeError):
            raise MagacinError('Lista artikala nije ispravna.')
        if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
            raise MagacinError('Unesi količinu za barem jedan artikal koji fali.')
        items = {str(item.pk): item for item in order.stavke.select_related('artikal', 'varijacija')}
        already = dict(Line.objects.filter(voided_by__isnull=True, order_item__narudzba=order, entry__kind__in=([Entry.Kind.MISSING, Entry.Kind.DAMAGED] if action != 'excess' else [Entry.Kind.EXCESS]))
                       .values('order_item_id').annotate(qty=Sum('quantity')).values_list('order_item_id', 'qty'))
        booked = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                raise MagacinError('Lista artikala nije ispravna.')
            key = str(row.get('item_id', ''))
            item = items.get(key)
            if not item or key in seen:
                raise MagacinError('Artikal ne pripada narudžbi ili je ponovljen.')
            seen.add(key)
            qty = quantity(row.get('quantity'))
            if action != 'excess' and qty + already.get(item.pk, 0) > item.kolicina:
                raise MagacinError(f'Količina prelazi preostalu količinu narudžbe: {item.naziv}.')
            booked.append((item, qty))
        subtotal = sum((item.cijena * item.kolicina for item in items.values()), Decimal('0'))
        net = max(Decimal('0'), subtotal - order.popust)
        line_values = {}
        cumulative = allocated = Decimal('0')
        for item in items.values():
            cumulative += item.cijena * item.kolicina
            target = (net * cumulative / subtotal).quantize(Decimal('.01'), rounding=ROUND_HALF_UP) if subtotal else Decimal('0')
            line_values[item.pk] = target - allocated
            allocated = target
        valued = []
        for item, qty in booked:
            before = already.get(item.pk, 0)
            value = line_values[item.pk]
            previous = (value * before / item.kolicina).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            target = (value * (before + qty) / item.kolicina).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            valued.append((item, qty, target - previous))
        total = sum((value for _, _, value in valued), Decimal('0'))
        if total < 0 or total > Decimal('999999999999.99'):
            raise MagacinError('Iznosi narudžbe nisu ispravni za knjiženje.')
        entry = Entry.objects.create(**common, kind=entry_kind, source_order=order,
                                     amount=total if action == 'excess' else -total,
                                     description=description or (f'Oštećen artikal iz narudžbe #{order.broj}' if action == 'damaged' else f'Nije došlo iz narudžbe #{order.broj}' if action == 'missing' else f'Više poslato iz narudžbe #{order.broj}'))
        for item, qty, value in valued:
            Line.objects.create(entry=entry, order_item=item, product=item.artikal, variation=item.varijacija,
                                name=item.puni_naziv[:300], code=item.sifra[:200], quantity=qty, amount=value)
        return entry
    if action == 'order':
        order = Order.objects.select_for_update().filter(broj=(data.get('order_number') or '').strip().lstrip('#')).first()
        if not order or order.lager_status != Order.LagerStatus.VALIDIRANO or order.status == Order.Status.OTKAZANA:
            raise MagacinError('Unesi broj validirane, neotkazane narudžbe.')
        if Entry.objects.filter(order=order).exists():
            raise MagacinError('Ova narudžba je već proknjižena.')
        rows = [item for item in order.stavke.select_related('artikal', 'varijacija') if item.kolicina_faktura > 0]
        subtotal = sum((row.ukupno for row in rows), Decimal('0'))
        # Preserve invoice total; allocate its goods value proportionally for partial returns.
        goods_total = max(Decimal('0'), order.ukupno - order.dostava)
        if not rows or subtotal <= 0 or order.ukupno < 0 or goods_total > subtotal:
            raise MagacinError('Iznosi narudžbe nisu usklađeni; provjeri fakturu prije knjiženja.')
        entry = Entry.objects.create(**common, kind=Entry.Kind.ORDER, amount=order.ukupno,
                                     order=order, description=description or f'Narudžba #{order.broj} — {order.ime_prezime}')
        allocated = Decimal('0')
        cumulative = Decimal('0')
        for row in rows:
            cumulative += row.ukupno
            target = (goods_total * cumulative / subtotal).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            amount = target - allocated
            allocated = target
            Line.objects.create(entry=entry, product=row.artikal, variation=row.varijacija,
                                name=row.puni_naziv[:300], code=row.sifra[:200], quantity=row.kolicina_faktura, amount=amount)
        return entry
    if action == 'return':
        try:
            line = Line.objects.select_related('entry', 'product', 'variation').get(pk=data.get('line_id'), entry__partner=partner)
            location = WarehouseLocation.objects.get(pk=data.get('location_id'), aktivan=True)
        except (Line.DoesNotExist, WarehouseLocation.DoesNotExist, ValueError, TypeError):
            raise MagacinError('Odaberi stavku partnera i lokaciju povrata.')
        if line.entry.kind in (Entry.Kind.MISSING, Entry.Kind.EXCESS, Entry.Kind.DAMAGED):
            raise MagacinError('Artikli koji fale nisu povrat robe i ne mogu se ovdje vraćati na lager.')
        if line.voided_by_id:
            raise MagacinError('Stavka je već obrisana.')
        if line.settled_by_id:
            raise MagacinError('Ova stavka je već izmirena uplatom.')
        qty = quantity(data.get('quantity'))
        returned = line.returns.aggregate(qty=Sum('returned_qty'), amount=Sum('amount'))
        previous_qty = returned['qty'] or 0
        if not line.product_id or qty + previous_qty > line.quantity:
            raise MagacinError('Povrat prelazi preostalu količinu ili artikal više nije dostupan.')
        if line.variation_id is None and line.product.varijacije.exists():
            raise MagacinError('Artikal sada ima varijacije. Prvo uskladi izvornu stavku i lager.')
        incoming = line.entry.kind in (Entry.Kind.DEBIT, Entry.Kind.ORDER)
        target = (line.amount * Decimal(qty + previous_qty) / line.quantity).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        value = target - abs(returned['amount'] or Decimal('0'))
        entry = Entry.objects.create(**common, kind=Entry.Kind.RETURN, source_line=line, returned_qty=qty,
                                     amount=-value if incoming else value,
                                     description=description or f'Povrat: {line.name}'[:300])
        apply_movement(product=line.product, variation=line.variation, location=location,
                       tip=WarehouseMovement.Tip.PRIJEM if incoming else WarehouseMovement.Tip.PRODAJA,
                       kolicina=qty, user=user, napomena=f'Duguje/Potražuje #{entry.pk}: {partner.naziv}'[:300])
        return entry
    kind = data.get('kind')
    if kind not in (Entry.Kind.DEBIT, Entry.Kind.CREDIT, Entry.Kind.RECEIPT, Entry.Kind.PAYMENT):
        raise MagacinError('Odaberi vrstu knjiženja.')
    goods = []
    if data.get('with_goods') == '1':
        if kind not in (Entry.Kind.DEBIT, Entry.Kind.CREDIT):
            raise MagacinError('Uplata ne može sadržavati robu.')
        if data.get('items_json'):
            try:
                rows = json.loads(data['items_json'])
            except (ValueError, TypeError):
                raise MagacinError('Lista artikala nije ispravna.')
            if not isinstance(rows, list) or not 1 <= len(rows) <= 100 or not all(isinstance(row, dict) for row in rows):
                raise MagacinError('Dodaj od 1 do 100 artikala.')
        else:
            rows = [data]
        for row in rows:
            product, variation, location = stock_objects(row)
            qty = quantity(row.get('quantity'))
            value = money(row.get('price')) * qty
            goods.append((product, variation, location, qty, value))
        amount = sum((row[4] for row in goods), Decimal('0'))
        if amount > Decimal('999999999999.99'):
            raise MagacinError('Ukupan iznos je prevelik.')
    else:
        amount = money(data.get('amount'))
    previous_balance = None
    if kind in (Entry.Kind.PAYMENT, Entry.Kind.RECEIPT):
        previous_balance = partner.entries.aggregate(total=Sum('amount'))['total'] or Decimal('0')
    entry = Entry.objects.create(**common, kind=kind,
                                 amount=amount if kind in (Entry.Kind.DEBIT, Entry.Kind.PAYMENT) else -amount,
                                 description=description or Entry.Kind(kind).label)
    if previous_balance is not None:
        new_balance = previous_balance + entry.amount
        fully_paid = (kind == Entry.Kind.PAYMENT and previous_balance < 0 <= new_balance) or (kind == Entry.Kind.RECEIPT and previous_balance > 0 >= new_balance)
        if fully_paid:
            for paid_line in Line.objects.filter(entry__partner=partner, settled_by__isnull=True, invoice_item__isnull=False).exclude(invoice_item__narudzba__lager_status=Order.LagerStatus.VALIDIRANO):
                remove_pending_excess_invoice(paid_line)
            for paid_line in Line.objects.filter(entry__partner=partner, settled_by__isnull=True, fulfillment_items__isnull=False).distinct():
                remove_pending_missing_items(paid_line, user=user)
            Line.objects.filter(entry__partner=partner, settled_by__isnull=True).update(settled_by=entry)
    # All lines and movements commit together; a failed line rolls back the whole booking.
    for product, variation, location, qty, value in goods:
        Line.objects.create(entry=entry, product=product, variation=variation, location=location,
                            name=(f'{product.naziv} — {variation.naziv}' if variation else product.naziv)[:300],
                            code=(variation.sifra if variation and variation.sifra else product.sifra or '')[:200],
                            quantity=qty, amount=value)
        apply_movement(product=product, variation=variation, location=location,
                       tip=WarehouseMovement.Tip.PRODAJA if kind == Entry.Kind.DEBIT else WarehouseMovement.Tip.PRIJEM,
                       kolicina=qty, user=user, napomena=f'Duguje/Potražuje #{entry.pk}: {partner.naziv}'[:300])
    return entry


@transaction.atomic
def create_replacement(*, partner_id, line_id, user):
    """Create a free replacement once; reserve real stock before committing anything."""
    from .models import OrderItem
    from .magacin import reserve_for_order
    from .views_magacin import invalidate_magacin_nav_counts

    partner = WarehousePartner.objects.select_for_update().get(pk=partner_id)
    if not str(line_id or '').isdigit():
        raise MagacinError('Odaberi artikal za slanje.')
    line = Line.objects.select_related('entry__source_order', 'replacement_order', 'variation').filter(
        pk=line_id, entry__partner=partner, entry__kind__in=[Entry.Kind.DAMAGED, Entry.Kind.MISSING],
    ).first()
    if not line:
        raise MagacinError('Artikal za slanje ovog kupca nije pronađen.')
    if line.voided_by_id:
        raise MagacinError('Stavka je već obrisana.')
    if line.settled_by_id:
        raise MagacinError('Artikal je već izmiren uplatom.')
    if line.replacement_order_id:
        return line.replacement_order
    if line.fulfillment_items.exclude(narudzba__lager_status__in=[Order.LagerStatus.OTKAZANO, Order.LagerStatus.VALIDIRANO]).exclude(narudzba__status=Order.Status.OTKAZANA).exists():
        raise MagacinError('Manjak je već dodat na picking aktivne narudžbe.')
    if line.returns.exists():
        raise MagacinError('Manjak je djelimično izmiren. Ostatak se dodaje kroz novu narudžbu kupca.')
    if not line.product_id:
        raise MagacinError('Artikal više nije dostupan za zamjenu.')
    product = Product.objects.select_for_update().get(pk=line.product_id)
    if not line.variation_id and product.varijacije.exists():
        raise MagacinError('Artikal sada ima varijacije; prvo uskladi oštećenu stavku.')
    original = line.entry.source_order
    customer = partner.customer
    def contact(field, fallback=''):
        value = getattr(customer, field, '') if customer else ''
        return value or getattr(original, field, '') or fallback
    name = contact('ime_prezime', partner.naziv)
    phone = contact('telefon', partner.telefon)
    address = contact('adresa', partner.adresa)
    city = contact('grad', partner.grad)
    if not all(str(value).strip() for value in (name, phone, address, city)):
        raise MagacinError('Dopuni ime, telefon, adresu i grad kupca prije kreiranja zamjene.')
    purpose = 'Besplatna zamjena oštećenog artikla' if line.entry.kind == Entry.Kind.DAMAGED else 'Slanje artikla koji nije došao'
    order = Order.objects.create(
        ime_prezime=name, telefon=phone, email=contact('email'), adresa=address, grad=city,
        postanski_broj=contact('postanski_broj'), izvor=Order.Izvor.MAGACIN,
        status=Order.Status.NOVA, medjuzbir=0, dostava=0, popust=0, ukupno=0,
        napomena=f'{purpose} — evidencija #{line.pk}, narudžba #{original.broj if original else "—"}. Bez naplate kupcu.',
    )
    OrderItem.objects.create(narudzba=order, artikal=product, varijacija=line.variation,
                             naziv=line.name[:200], product_naziv=product.naziv[:200],
                             varijacija_naziv=line.variation.naziv[:100] if line.variation else '',
                             sifra=line.code, cijena=0, bazna_cijena=0, kolicina=line.quantity)
    leftover = reserve_for_order(order, product, line.quantity, variation=line.variation, user=user,
                                 napomena=f'Zamjenski artikal #{order.broj}')
    if leftover:
        raise MagacinError(f'Nema dovoljno dostupne robe na lokacijama za zamjenu ({line.quantity} kom.). Narudžba nije kreirana.')
    order.lager_status = Order.LagerStatus.REZERVISANO
    order.save(update_fields=['lager_status'])
    line.replacement_order = order
    line.save(update_fields=['replacement_order'])
    transaction.on_commit(invalidate_magacin_nav_counts)
    return order


@transaction.atomic
def settle_replacement(order, *, user=None):
    """Close exactly the replaced damaged line, retaining the original audit trail."""
    from uuid import uuid5, NAMESPACE_URL
    if order.lager_status != Order.LagerStatus.VALIDIRANO or order.status == Order.Status.OTKAZANA:
        return None
    line = Line.objects.select_related('entry').filter(replacement_order=order, entry__kind__in=[Entry.Kind.DAMAGED, Entry.Kind.MISSING]).first()
    if line is None:
        return None
    WarehousePartner.objects.select_for_update().get(pk=line.entry.partner_id)
    line.refresh_from_db(fields=['settled_by'])
    if line.settled_by_id:
        return None
    token = uuid5(NAMESPACE_URL, f'warehouse-replacement-settled:{line.pk}:{order.pk}')
    entry, _ = Entry.objects.get_or_create(token=token, defaults={
        'partner_id': line.entry.partner_id, 'kind': Entry.Kind.SETTLED,
        'amount': line.amount, 'source_line': line, 'returned_qty': line.quantity,
        'source_order': order, 'user': user,
        'description': f'Zamjena #{order.broj} validatovana — {line.name}'[:300],
    })
    return entry


def active_invoice_orders(partner):
    return customer_orders(partner).filter(
        status__in=[Order.Status.NOVA, Order.Status.REZERVACIJA], zapakovana=False,
    ).exclude(lager_status__in=[Order.LagerStatus.VALIDIRANO, Order.LagerStatus.OTKAZANO])


@transaction.atomic
def add_excess_to_invoice(*, partner_id, line_id, order_id, user=None):
    from .models import OrderItem
    from .magacin import recalculate_order_totals
    if not str(line_id or '').isdigit() or not str(order_id or '').isdigit():
        raise MagacinError('Izaberi artikal i aktivnu narudžbu kupca.')
    partner = WarehousePartner.objects.select_for_update().get(pk=partner_id)
    line = Line.objects.select_for_update().select_related('entry').filter(
        pk=line_id, entry__partner=partner, entry__kind=Entry.Kind.EXCESS,
        settled_by__isnull=True, voided_by__isnull=True,
    ).first()
    if line is None:
        raise MagacinError('Aktivna stavka viška nije pronađena.')
    existing = OrderItem.objects.select_related('narudzba').filter(ledger_excess_line=line).first()
    if existing:
        if existing.narudzba.status == Order.Status.OTKAZANA or existing.narudzba.lager_status == Order.LagerStatus.OTKAZANO:
            raise MagacinError('Višak je na otkazanoj narudžbi. Prvo ukloni povezivanje kroz korpu.')
        return existing.narudzba
    order = active_invoice_orders(partner).select_for_update().filter(pk=order_id).first()
    if order is None:
        raise MagacinError('Izaberi aktivnu narudžbu ovog kupca koja još nije zapakovana.')
    if line.returns.exists():
        raise MagacinError('Ova stavka je već izmirena.')
    OrderItem.objects.create(
        narudzba=order, ledger_excess_line=line, artikal=line.product, varijacija=line.variation,
        naziv=line.name[:200], product_naziv=line.name[:200], sifra=line.code,
        cijena=line.unit_price.quantize(Decimal('.01'), rounding=ROUND_HALF_UP),
        kolicina=line.quantity, kolicina_pokupljeno=line.quantity,
    )
    recalculate_order_totals(order)
    return order


@transaction.atomic
def settle_invoiced_excess(order, *, user=None):
    from uuid import uuid5, NAMESPACE_URL
    if order.lager_status != Order.LagerStatus.VALIDIRANO or order.status == Order.Status.OTKAZANA:
        return
    for item in order.stavke.filter(ledger_excess_line__isnull=False).select_related('ledger_excess_line__entry'):
        line = item.ledger_excess_line
        WarehousePartner.objects.select_for_update().get(pk=line.entry.partner_id)
        if line.settled_by_id or line.voided_by_id:
            continue
        Entry.objects.get_or_create(token=uuid5(NAMESPACE_URL, f'warehouse-excess-invoiced:{line.pk}:{order.pk}'), defaults={
            'partner_id': line.entry.partner_id, 'kind': Entry.Kind.SETTLED,
            'amount': -line.amount, 'source_line': line, 'returned_qty': line.quantity,
            'source_order': order, 'user': user,
            'description': f'Višak fakturisan bez pickinga kroz #{order.broj} — {line.name}'[:300],
        })


def remove_pending_excess_invoice(line):
    from .models import OrderItem
    from .magacin import recalculate_order_totals
    item = OrderItem.objects.select_related('narudzba').filter(ledger_excess_line=line).first()
    if not item:
        return
    order = Order.objects.select_for_update().get(pk=item.narudzba_id)
    if order.lager_status == Order.LagerStatus.VALIDIRANO:
        raise MagacinError('Višak je već fakturisan na validiranoj narudžbi.')
    item.delete()
    recalculate_order_totals(order)


def pending_excess_lines():
    return Line.objects.filter(
        entry__kind=Entry.Kind.EXCESS, settled_by__isnull=True, voided_by__isnull=True,
        invoice_item__isnull=True, returns__isnull=True,
    ).select_related('entry__partner')


@transaction.atomic
def attach_customer_excess(order, customer, *, user=None):
    if not customer:
        return []
    partner = WarehousePartner.objects.select_for_update().filter(customer=customer).first()
    if not partner:
        return []
    attached = list(pending_excess_lines().filter(entry__partner=partner).order_by('pk'))
    for line in attached:
        add_excess_to_invoice(partner_id=partner.pk, line_id=line.pk, order_id=order.pk, user=user)
    return attached


def pending_missing_lines():
    from django.db.models import Exists, OuterRef, F
    from django.db.models.functions import Coalesce
    from .models import OrderItem
    active = OrderItem.objects.filter(ledger_missing_line_id=OuterRef('pk')).exclude(
        narudzba__lager_status__in=[Order.LagerStatus.OTKAZANO, Order.LagerStatus.VALIDIRANO],
    ).exclude(narudzba__status=Order.Status.OTKAZANA)
    return Line.objects.filter(
        entry__kind=Entry.Kind.MISSING, settled_by__isnull=True, voided_by__isnull=True,
        replacement_order__isnull=True,
    ).annotate(returned=Coalesce(Sum('returns__returned_qty'), 0), in_fulfillment=Exists(active)).filter(
        quantity__gt=F('returned'), in_fulfillment=False,
    ).select_related('entry__partner', 'product', 'variation')


@transaction.atomic
def attach_customer_missing(order, customer, *, user=None):
    from .models import OrderItem
    from .magacin import exact_order_location_rows, reserve_for_order
    if not customer:
        return []
    partner = WarehousePartner.objects.select_for_update().filter(customer=customer).first()
    if not partner:
        return []
    attached = []
    for line in pending_missing_lines().filter(entry__partner=partner).order_by('pk'):
        if not line.product_id:
            continue
        available = sum(row['dostupno'] for row in exact_order_location_rows(line.product, line.variation))
        qty = min(line.quantity - line.returned, available)
        if qty <= 0:
            continue
        if reserve_for_order(order, line.product, qty, variation=line.variation, user=user, exact=True):
            raise MagacinError('Stanje se promijenilo. Pokušaj ponovo kreirati narudžbu.')
        item = OrderItem.objects.create(narudzba=order, ledger_missing_line=line,
            artikal=line.product, varijacija=line.variation, naziv=line.name[:200],
            product_naziv=line.name[:200], sifra=line.code, cijena=0, bazna_cijena=0, kolicina=qty)
        attached.append(item)
    return attached


@transaction.atomic
def settle_picked_missing(order, *, user=None):
    from uuid import uuid5, NAMESPACE_URL
    if order.lager_status != Order.LagerStatus.VALIDIRANO or order.status == Order.Status.OTKAZANA:
        return
    for item in order.stavke.filter(ledger_missing_line__isnull=False).select_related('ledger_missing_line__entry'):
        line = item.ledger_missing_line
        WarehousePartner.objects.select_for_update().get(pk=line.entry.partner_id)
        line.refresh_from_db(fields=['settled_by', 'voided_by'])
        if line.settled_by_id or line.voided_by_id:
            continue
        token = uuid5(NAMESPACE_URL, f'warehouse-missing-picked:{item.pk}:{order.pk}')
        if Entry.objects.filter(token=token).exists():
            continue
        returned = line.returns.aggregate(qty=Sum('returned_qty'), amount=Sum('amount'))
        previous_qty = returned['qty'] or 0
        qty = min(max(0, int(item.kolicina_pokupljeno or 0)), max(0, line.quantity - previous_qty))
        if not qty:
            continue
        target = (line.amount * Decimal(previous_qty + qty) / line.quantity).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        amount = max(Decimal('0'), target - (returned['amount'] or Decimal('0')))
        Entry.objects.create(token=token, partner_id=line.entry.partner_id, kind=Entry.Kind.SETTLED,
            amount=amount, source_line=line, returned_qty=qty, source_order=order, user=user,
            description=f'Manjak poslat kroz #{order.broj} — {item.naziv}, {qty} kom.'[:300])


def remove_pending_missing_items(line, *, user=None):
    from .magacin import release_holds_for_product, _clear_pick_state_for_item
    for item in line.fulfillment_items.exclude(narudzba__lager_status=Order.LagerStatus.VALIDIRANO).select_related('narudzba', 'artikal', 'varijacija'):
        order = Order.objects.select_for_update().get(pk=item.narudzba_id)
        if item.kolicina_pokupljeno or any(e.get('item_id') == item.pk and e.get('got') for e in (order.pick_short_events or [])):
            raise MagacinError('Artikal je već preuzet na pickingu. Prvo završi picking pa izmiri preostali dug.')
        if item.artikal_id:
            release_holds_for_product(order, item.artikal, item.varijacija, qty=item.kolicina, user=user)
        _clear_pick_state_for_item(order, item.pk)
        item.delete()
