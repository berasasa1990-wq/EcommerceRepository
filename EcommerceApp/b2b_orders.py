"""B2B checkout and exact-location, deferred inventory deduction."""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .b2b_pricing import (
    discounts_for, net_price, brand_divisors, price_snapshot, apply_volume_discount,
    account_rabat_percent, order_rabat_percent,
)
from .models import (B2BAccount, B2BSubmission, Order, OrderItem, OrderStockHold,
                     Product, WarehouseStock, WarehouseMovement)
from .magacin import (MagacinError, ignored_location_q, apply_movement,
                      _location_for_pick_label)


def gross(net):
    return (net * Decimal('1.17')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


@transaction.atomic
def submit_order(account, cart, token, details):
    account = B2BAccount.objects.select_for_update().get(pk=account.pk, is_active=True)
    existing = B2BSubmission.objects.filter(account=account, token=token).first()
    if existing:
        return existing
    if not cart:
        raise MagacinError('Korpa je prazna.')
    products = {p.pk: p for p in Product.objects.select_for_update().filter(
        pk__in=[k.split(':')[0] for k in cart], aktivan=True, sakriven_do_stanja=False
    ).filter(Q(kategorija__aktivan=True) | Q(kategorija__isnull=True)).order_by('pk').prefetch_related('varijacije')}
    discounts = discounts_for(products)
    divisors = brand_divisors()
    lines = []
    for key, quantity in cart.items():
        product_id, variation_id = map(int, key.split(':'))
        product = products.get(product_id)
        variations = {v.pk: v for v in product.varijacije.all()} if product else {}
        variation = variations.get(variation_id)
        if not product or (variation_id and not variation) or (not variation_id and variations) or quantity <= 0:
            raise MagacinError('Artikal više nije dostupan. Provjerite korpu.')
        stocks = list(WarehouseStock.objects.select_for_update().filter(product=product,
            variation=variation, location__aktivan=True).exclude(ignored_location_q('location')).order_by('location_id', 'pk'))
        if sum(stock.dostupno for stock in stocks) < quantity:
            raise MagacinError(f'Nedovoljno dostupne količine: {product.naziv}.')
        price = net_price(product, variation, discounts, divisors)
        lines.append((product, variation, quantity, price, stocks))
    net_total = sum((price * qty for _, _, qty, price, _ in lines), Decimal('0.00'))
    line_gross = sum((gross(price) * qty for _, _, qty, price, _ in lines), Decimal('0.00'))
    rabat_percent = account_rabat_percent(account)
    net_after, volume_discount, total, _tax = apply_volume_discount(
        net_total, line_gross, percent=rabat_percent)
    label = 'žiralno' if details['payment'] == 'ziralno' else 'gotovinski'
    details_list = [{'opis': f'Plaćanje: {label}', 'placanje': details['payment']}]
    if rabat_percent > 0:
        pct = format(rabat_percent.normalize(), 'f')
        details_list.append({
            'opis': f'Rabat −{pct}%: −{volume_discount:.2f} KM netto',
            'rabat': True,
            'rabat_percent': str(rabat_percent),
            'volume_discount': str(volume_discount),
        })
    order = Order.objects.create(ime_prezime=account.company, email='',
        telefon='', adresa='', grad='',
        napomena=f'VP narudžba\nB2B: {account.username}\nPlaćanje: {label}\n{details.get("napomena", "")}',
        medjuzbir=line_gross, popust=line_gross - total, ukupno=total, izvor=Order.Izvor.MAGACIN,
        lager_status=Order.LagerStatus.REZERVISANO,
        popust_detalji=details_list)
    submission = B2BSubmission.objects.create(account=account, order=order, token=token,
        payment=details['payment'], netto_total=net_after)
    for product, variation, quantity, price, stocks in lines:
        pricing_snapshot = price_snapshot(product, variation, discounts, divisors)
        OrderItem.objects.create(narudzba=order, artikal=product, varijacija=variation,
            naziv=product.naziv, product_naziv=product.naziv,
            varijacija_naziv=variation.naziv if variation else '',
            sifra=(variation.sifra if variation else product.sifra) or '',
            cijena=gross(price), bazna_cijena=gross(price), kolicina=quantity,
            popust_opis=f'B2B VPC netto: {price} KM', b2b_pricing_snapshot=pricing_snapshot)
        remaining = quantity
        for stock in stocks:
            take = min(remaining, stock.dostupno)
            if take:
                apply_movement(product=product, variation=variation, location=stock.location,
                    tip=WarehouseMovement.Tip.REZERVACIJA, kolicina=take,
                    rezervisano=stock.rezervisano + take, napomena=f'B2B rezervacija #{order.broj}')
                OrderStockHold.objects.create(narudzba=order, product=product, variation=variation,
                    location=stock.location, kolicina=take)
                remaining -= take
            if not remaining:
                break
    from .views_magacin import invalidate_magacin_nav_counts
    transaction.on_commit(invalidate_magacin_nav_counts)
    return submission


@transaction.atomic
def save_pick(order, lines):
    """Record progress only. No stock changes, even for a partially picked line."""
    locked = Order.objects.select_for_update().get(pk=order.pk)
    if locked.lager_status in (Order.LagerStatus.VALIDIRANO, Order.LagerStatus.OTKAZANO):
        raise MagacinError('Picking je zatvoren.')
    state = dict(locked.pick_state or {})
    items = {item.pk: item for item in order.stavke.all()}
    for raw in lines or []:
        item_id = int(raw.get('item_id') or 0)
        got = int(raw.get('got') or 0)
        key = str(raw.get('key') or '')
        if item_id not in items or not key or got < 0 or got > items[item_id].kolicina:
            raise MagacinError('Neispravna B2B picking stavka ili količina.')
        loc = str(raw.get('loc') or '').strip()
        location = _location_for_pick_label(loc)
        if not location or not location.aktivan:
            raise MagacinError('Potvrdite fizičku lokaciju preuzimanja.')
        state[key] = {'item_id': item_id, 'got': got, 'need': items[item_id].kolicina,
                      'loc': loc, 'done': bool(raw.get('done'))}
    order.pick_state = state
    order.save(update_fields=['pick_state'])
    return state


@transaction.atomic
def finish_pick(order, user=None):
    locked = Order.objects.select_for_update().get(pk=order.pk)
    if locked.lager_status == Order.LagerStatus.VALIDIRANO:
        order.refresh_from_db()
        return
    if locked.status == Order.Status.OTKAZANA or locked.lager_status == Order.LagerStatus.OTKAZANO:
        raise MagacinError('Narudžba je otkazana.')
    items = list(locked.stavke.select_related('artikal', 'varijacija').order_by('artikal_id', 'pk'))
    # Same product lock order as checkout and warehouse movements.
    list(Product.objects.select_for_update().filter(pk__in=[i.artikal_id for i in items]).order_by('pk'))
    picked = defaultdict(int)
    deductions = defaultdict(int)
    item_map = {i.pk: i for i in items}
    for row in (locked.pick_state or {}).values():
        if not isinstance(row, dict) or row.get('item_id') not in item_map:
            continue
        if not row.get('done'):
            raise MagacinError('Potvrdite sve picking lokacije prije završetka.')
        item = item_map[row['item_id']]
        qty = int(row.get('got') or 0)
        if qty < 0:
            raise MagacinError('Neispravna pokupljena količina.')
        location = _location_for_pick_label(row.get('loc') or '')
        if not location or not location.aktivan:
            raise MagacinError('Picking lokacija nije dostupna.')
        picked[item.pk] += qty
        deductions[(item.artikal_id, item.varijacija_id, location.pk)] += qty
    if any(item.pk not in picked or picked[item.pk] > item.kolicina for item in items):
        raise MagacinError('Potvrdite pokupljenu količinu za svaki artikal.')
    # Release this order's reservations first, in the same transaction; other orders retain theirs.
    for hold in locked.magacin_holds.select_for_update().filter(status=OrderStockHold.Status.REZERVISANO).order_by('product_id', 'location_id', 'pk'):
        stock = WarehouseStock.objects.select_for_update().filter(product=hold.product,
            variation=hold.variation, location=hold.location).first()
        if not stock or stock.rezervisano < hold.kolicina:
            raise MagacinError('Rezervacija je promijenjena. Provjerite stanje u magacinu.')
        apply_movement(product=hold.product, variation=hold.variation, location=hold.location,
            tip=WarehouseMovement.Tip.REZERVACIJA, kolicina=hold.kolicina,
            rezervisano=stock.rezervisano - hold.kolicina, user=user,
            napomena=f'B2B završetak rezervacije #{locked.broj}')
        hold.status = OrderStockHold.Status.OTKAZANO
        hold.save(update_fields=['status'])
    for (product_id, variation_id, location_id), qty in deductions.items():
        if not qty:
            continue
        stock = WarehouseStock.objects.select_for_update().filter(product_id=product_id,
            variation_id=variation_id, location_id=location_id).first()
        if not stock or stock.dostupno < qty:
            raise MagacinError('Nedovoljno zalihe na potvrđenoj lokaciji. Provjerite picking.')
        apply_movement(product=product_id, variation=variation_id, location=location_id,
            tip=WarehouseMovement.Tip.PRODAJA, kolicina=qty, user=user,
            napomena=f'B2B picking #{locked.broj}', order=locked)
    gross_total = Decimal('0.00')
    net_total = Decimal('0.00')
    for item in items:
        qty = picked[item.pk]
        item.kolicina_pokupljeno = qty
        item.save(update_fields=['kolicina_pokupljeno'])
        gross_total += item.cijena * qty
        snap = item.b2b_pricing_snapshot or {}
        if snap.get('netto'):
            net_total += Decimal(str(snap['netto'])) * qty
        else:
            net_total += (item.cijena / Decimal('1.17')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) * qty
    net_after, volume_discount, total, _tax = apply_volume_discount(
        net_total, gross_total, percent=order_rabat_percent(locked))
    locked.medjuzbir = gross_total
    locked.popust = (gross_total - total).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    locked.ukupno = total
    locked.lager_status = Order.LagerStatus.VALIDIRANO
    locked.status = Order.Status.ZAVRSENA
    locked.zapakovana = True
    locked.zapakovana_at = timezone.now()
    locked.save(update_fields=['medjuzbir', 'popust', 'ukupno', 'lager_status', 'status', 'zapakovana', 'zapakovana_at'])
    submission = B2BSubmission.objects.filter(order_id=locked.pk).first()
    if submission is not None and submission.netto_total != net_after:
        submission.netto_total = net_after
        submission.save(update_fields=['netto_total'])
    order.refresh_from_db()
    from .views_magacin import invalidate_magacin_nav_counts
    transaction.on_commit(invalidate_magacin_nav_counts)
