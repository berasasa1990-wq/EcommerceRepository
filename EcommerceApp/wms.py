"""WMS paper picking and authenticated QR completion."""
import hashlib
import json
from collections import Counter

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .magacin import MagacinError, validate_order_stock
from .models import Order, Product, WarehouseStock
from .warehouse_access import warehouse_user_required

SALT = 'wms-paper-picking-v1'


def picking_plan(order):
    from .views_magacin import _order_pick_bundle
    queue, _, error = _order_pick_bundle(order)
    rows = [dict(row) for row in queue]
    totals = Counter()
    for row in queue:
        totals[row['item_id']] += int(row['need'])
    items = list(order.stavke.filter(ledger_excess_line__isnull=True, is_set_parent=False).order_by('pk'))
    problems = [str(error)] if error else []
    for item in items:
        missing = int(item.kolicina) - totals[item.pk]
        if missing != 0:
            problems.append(f'{item.sifra or item.naziv}: količine po lokacijama nisu potpune.')
        if missing > 0:
            rows.append({'key': f'missing:{item.pk}', 'item_id': item.pk, 'naziv': item.naziv, 'sifra': item.sifra,
                         'loc': 'Nema raspoložive lokacije', 'need': missing, 'missing': True})
    if not items:
        problems.append('Narudžba nema stavki za picking.')
    # Bind the QR to the printed items, quantities, locations and picking state.
    snapshot = {
        'items': [(i.pk, i.artikal_id, i.varijacija_id, i.naziv, i.sifra, i.kolicina,
                   str(i.cijena), i.kolicina_pokupljeno) for i in items],
        # Row order is presentation only; PostgreSQL need not return unordered
        # queries in the same order during printing and QR validation.
        'queue': sorted((r['key'], r['item_id'], r['loc'], r['need']) for r in queue),
        'state': order.pick_state, 'events': order.pick_short_events,
        'total': str(order.ukupno), 'status': order.status, 'lager': order.lager_status,
    }
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    return {'rows': rows, 'queue': queue, 'digest': digest, 'error': ' '.join(problems),
            'base_error': str(error or '') or ('Narudžba nema stavki za picking.' if not items else ''),
            'overallocated': any(totals[item.pk] > int(item.kolicina) for item in items)}


@transaction.atomic
def _prepare_paper_picking(order_id, user):
    """Reserve each printed order's shelves so completion order cannot reroute it."""
    from .magacin import reserve_for_order
    from .views import _magacin_hold_picks, _short_picked_qty_by_item, _item_remaining_pick_qty
    from .views_magacin import _unvalidated_orders_qs

    order = get_object_or_404(Order.objects.select_for_update(), pk=order_id)
    if not _unvalidated_orders_qs().filter(pk=order.pk).exists():
        return picking_plan(order)
    items = list(order.stavke.filter(
        ledger_excess_line__isnull=True, is_set_parent=False,
    ).select_related('artikal', 'varijacija').order_by('pk'))
    product_ids = {item.artikal_id for item in items if item.artikal_id}
    list(Product.objects.select_for_update().filter(pk__in=product_ids).order_by('pk'))
    list(WarehouseStock.objects.select_for_update().filter(product_id__in=product_ids).order_by('pk'))
    held = _magacin_hold_picks(order, items)
    picked = _short_picked_qty_by_item(order)
    for item in items:
        if not item.artikal_id:
            continue
        remaining = held.get(item.pk, ([], _item_remaining_pick_qty(item, picked)))[1]
        if remaining > 0:
            reserve_for_order(order, item.artikal, remaining, variation=item.varijacija, user=user)
    return picking_plan(order)


def paper_picking_job(request, order):
    from .views_magacin import _etiketa_qr_data_uri
    plan = _prepare_paper_picking(order.pk, request.user)
    for row in plan['rows']:
        row['display_loc'] = str(row['loc']).rstrip('/').rsplit('/', 1)[-1]
    token = signing.dumps({'order': order.pk, 'digest': plan['digest']}, salt=SALT, compress=True)
    url = request.build_absolute_uri(reverse('staff_magacin_wms_validate', kwargs={'token': token}))
    return {'rows': plan['rows'], 'error': plan['error'], 'qr': _etiketa_qr_data_uri(url)}


def alternative_locations(order, item, plan):
    """Offer unreserved stock for the exact product/variation outside printed locations."""
    from .magacin import _location_for_pick_label, is_ignored_stock_location
    if not item.artikal_id:
        return []
    same_items = set(order.stavke.filter(artikal_id=item.artikal_id, varijacija_id=item.varijacija_id)
                     .values_list('pk', flat=True))
    original_ids = set()
    for row in plan['rows']:
        if row['item_id'] in same_items:
            location = _location_for_pick_label(row['loc'])
            if location:
                original_ids.add(location.pk)
    result = []
    stocks = WarehouseStock.objects.filter(product_id=item.artikal_id, variation_id=item.varijacija_id,
        location__aktivan=True, kolicina__gt=0).exclude(location_id__in=original_ids).select_related('location').order_by('location__sifra', 'pk')
    for stock in stocks:
        if stock.dostupno > 0 and not is_ignored_stock_location(stock.location):
            result.append({'stock_id': stock.pk, 'location': stock.location,
                           'label': stock.location.sifra, 'available': stock.dostupno,
                           'key': f'alt:{item.pk}:{stock.pk}'})
    return result


def _alternative_lines(order, plan, quantities, *, reserve=False, user=None):
    from .magacin import reserve_for_order
    items = {item.pk: item for item in order.stavke.filter(is_set_parent=False, ledger_excess_line__isnull=True)}
    rows = []
    needed = Counter()
    chosen = {}
    for key, qty in quantities.items():
        if not key.startswith('alt:'):
            continue
        try:
            _, item_id, stock_id = key.split(':')
            item = items[int(item_id)]
            candidate = next(row for row in alternative_locations(order, item, plan)
                             if row['stock_id'] == int(stock_id))
        except (KeyError, ValueError, StopIteration):
            raise MagacinError('Alternativna lokacija više nije dostupna. Ponovo unesi pronađene količine.')
        if type(qty) is not int or qty <= 0:
            raise MagacinError('Količina s alternativne lokacije mora biti pozitivan cijeli broj.')
        needed[candidate['stock_id']] += qty
        chosen[candidate['stock_id']] = (candidate, item)
        rows.append({'key': key, 'item_id': item.pk, 'loc': candidate['label'], 'got': qty, 'need': qty, 'done': True})
    for stock_id, qty in needed.items():
        candidate, item = chosen[stock_id]
        if qty > candidate['available']:
            raise MagacinError('Na odabranoj alternativnoj lokaciji više nema dovoljno slobodne količine.')
        if reserve and reserve_for_order(order, item.artikal, qty, variation=item.varijacija,
                location=candidate['location'], exact=True, user=user):
            raise MagacinError('Stanje na alternativnoj lokaciji se promijenilo. Ponovo provjeri količine.')
    return rows


@transaction.atomic
def complete_paper_picking(order_id, digest, user, quantities=None):
    from .views_magacin import _unvalidated_orders_qs, apply_order_pick, order_needs_mp_check, claim_order_pick
    order = get_object_or_404(Order.objects.select_for_update(), pk=order_id)
    if order.lager_status == Order.LagerStatus.VALIDIRANO or order.status == Order.Status.ZAVRSENA:
        return order, False
    if not _unvalidated_orders_qs().filter(pk=order.pk).exists():
        raise MagacinError('Ova narudžba trenutno nije dostupna za validaciju.')
    # Lock the physical stock before rebuilding the printed location allocation.
    product_ids = list(order.stavke.values_list('artikal_id', flat=True))
    list(Product.objects.select_for_update().filter(pk__in=product_ids).order_by('pk'))
    list(WarehouseStock.objects.select_for_update().filter(product_id__in=product_ids).order_by('pk'))
    plan = picking_plan(order)
    if plan['digest'] != digest:
        raise MagacinError('Narudžba ili lokacije su promijenjene. Odštampaj novu picking listu.')
    quantities = quantities or {}
    valid_keys = {row['key'] for row in plan['rows']}
    if {key for key in quantities if not key.startswith('alt:')} - valid_keys:
        raise MagacinError('Odabrani artikal nije na ovoj picking listi.')
    if plan['base_error'] or plan['overallocated']:
        raise MagacinError(plan['error'])
    lines = []
    totals = Counter()
    for row in plan['rows']:
        got = quantities.get(row['key'], row['need'])
        if type(got) is not int or not 0 <= got <= int(row['need']):
            raise MagacinError('Pronađena količina mora biti od 0 do količine sa liste.')
        if row.get('missing') and got != 0:
            raise MagacinError('Za artikal bez raspoložive lokacije unesi 0 ili provjeri lokacije u magacinu.')
        if row.get('already_picked') and got != row['need']:
            raise MagacinError('Već potvrđeno preuzimanje se ne može mijenjati ovom listom.')
        totals[row['item_id']] += got
        lines.append(dict(row, got=got, done=True))
    alternative_rows = _alternative_lines(order, plan, quantities)
    for row in alternative_rows:
        totals[row['item_id']] += row['got']
        lines.append(row)
    ordered = dict(order.stavke.values_list('pk', 'kolicina'))
    if any(qty > ordered.get(item_id, 0) for item_id, qty in totals.items()):
        raise MagacinError('Ukupna pronađena količina ne smije biti veća od naručene.')
    if order_needs_mp_check(order):
        raise MagacinError('Prvo završi provjeru artikala u maloprodaji.')
    claimed, holder = claim_order_pick(order, user)
    if not claimed:
        raise MagacinError(f"Narudžbu je već preuzeo {holder or 'drugi radnik'}.")
    _alternative_lines(order, plan, quantities, reserve=True, user=user)
    zero_ids = [item_id for item_id, qty in totals.items() if qty == 0]
    order.stavke.filter(pk__in=zero_ids).update(wms_out_of_stock=True)
    apply_order_pick(order, lines, finalize=True, user=user, preserve_unavailable=True)
    validate_order_stock(order, user=user)
    order.refresh_from_db()
    if not any(totals.values()):
        order.medjuzbir = order.dostava = order.popust = order.ukupno = 0
        order.save(update_fields=['medjuzbir', 'dostava', 'popust', 'ukupno'])
    return order, True


def _signed_changes(order_id, digest, quantities):
    return signing.dumps({'order': order_id, 'digest': digest, 'quantities': quantities},
                         salt=SALT + '-changes', compress=True)


def _read_changes(raw, order_id, digest):
    if not raw:
        return {}
    try:
        data = signing.loads(raw, salt=SALT + '-changes')
        if data['order'] != order_id or data['digest'] != digest:
            raise ValueError
        quantities = data['quantities']
        if not isinstance(quantities, dict) or any(type(v) is not int or v < 0 for v in quantities.values()):
            raise ValueError
        return quantities
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise MagacinError('Unesene količine nisu validne. Ponovo skeniraj QR kod.')


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
@require_http_methods(['GET', 'POST'])
def validate_qr(request, token):
    try:
        payload = signing.loads(token, salt=SALT)
        order_id, digest = payload['order'], payload['digest']
    except (signing.BadSignature, KeyError, TypeError):
        raise Http404('QR kod nije validan.')
    order = get_object_or_404(Order, pk=order_id)
    context = {'order': order, 'stage': 'question', 'success': False}
    status = 200
    data = request.POST if request.method == 'POST' else request.GET
    quantities = {}
    try:
        if order.lager_status == Order.LagerStatus.VALIDIRANO or order.status == Order.Status.ZAVRSENA:
            context.update(stage='result', success=True, message=f'Narudžba #{order.broj} je već validatovana.')
        else:
            quantities = _read_changes(data.get('changes', ''), order_id, digest)
            action = request.POST.get('action') if request.method == 'POST' else None
            if action in ('all', 'complete'):
                if action == 'complete' and not quantities:
                    raise MagacinError('Prvo unesi pronađenu količinu za artikal.')
                order, changed = complete_paper_picking(order_id, digest, request.user,
                                                       quantities if action == 'complete' else None)
                context.update(order=order, stage='result', success=True,
                               message=f'Narudžba #{order.broj} je validatovana. Račun je usklađen s pokupljenim količinama.')
            elif action not in (None, 'save', 'save_alternatives', 'skip_alternatives'):
                raise MagacinError('Odaberi Da ili Ne prije potvrde narudžbe.')
            elif data.get('mode') == 'missing' or action in ('save', 'save_alternatives', 'skip_alternatives'):
                context['stage'] = 'missing'
                plan = picking_plan(order)
                if plan['digest'] != digest:
                    raise MagacinError('Narudžba ili lokacije su promijenjene. Odštampaj novu picking listu.')
                rows = []
                for index, source in enumerate(plan['rows']):
                    row = dict(source, input_name=f'found_{index}')
                    row['display_loc'] = str(row['loc']).rstrip('/').rsplit('/', 1)[-1]
                    row['found'] = quantities.get(row['key'], row['need'])
                    rows.append(row)
                selected_id = str(data.get('item', ''))
                selected = next((item for item in order.stavke.all() if str(item.pk) == selected_id), None)
                if action == 'save':
                    item_rows = [row for row in rows if selected and row['item_id'] == selected.pk]
                    if not item_rows:
                        raise MagacinError('Artikal nije na ovoj picking listi.')
                    updates = {}
                    for row in item_rows:
                        raw = data.get(row['input_name'], '')
                        try:
                            value = int(raw)
                        except (TypeError, ValueError):
                            raise MagacinError('Unesi cijeli broj, uključujući 0 kada artikal nije pronađen.')
                        if value < 0 or value > row['need'] or (row.get('missing') and value != 0):
                            raise MagacinError('Pronađena količina mora biti od 0 do količine na lokaciji sa liste.')
                        updates[row['key']] = value
                    quantities.update(updates)
                    # Re-entering the original quantities replaces this item's previous alternative selection.
                    quantities = {key: qty for key, qty in quantities.items() if not key.startswith(f'alt:{selected.pk}:')}
                    context['saved'] = True
                    remaining = int(selected.kolicina) - sum(updates.values())
                    alternatives = alternative_locations(order, selected, plan) if remaining > 0 else []
                    if alternatives:
                        context.update(alternative_item=selected, alternatives=alternatives, alternative_remaining=remaining)
                elif action in ('save_alternatives', 'skip_alternatives'):
                    if not selected:
                        raise MagacinError('Artikal nije na ovoj picking listi.')
                    original = [row for row in rows if row['item_id'] == selected.pk]
                    if not original or not all(row['key'] in quantities for row in original):
                        raise MagacinError('Prvo potvrdi količine s originalnih lokacija.')
                    if action == 'save_alternatives':
                        candidates = alternative_locations(order, selected, plan)
                        original_found = sum(quantities.get(row['key'], row['need']) for row in original)
                        if candidates:
                            context.update(alternative_item=selected, alternatives=candidates,
                                           alternative_remaining=max(0, selected.kolicina - original_found))
                        valid_inputs = {f"alt_{candidate['stock_id']}" for candidate in candidates}
                        if any(key.startswith('alt_') and key not in valid_inputs and value not in ('', '0')
                               for key, value in data.items()):
                            raise MagacinError('Odabrana alternativna lokacija više nije dostupna.')
                        quantities = {key: qty for key, qty in quantities.items() if not key.startswith(f'alt:{selected.pk}:')}
                        for candidate in candidates:
                            try:
                                qty = int(data.get(f"alt_{candidate['stock_id']}", '0'))
                            except (TypeError, ValueError):
                                raise MagacinError('Unesi cijeli broj za alternativnu lokaciju.')
                            if qty < 0 or qty > candidate['available']:
                                raise MagacinError('Količina ne smije biti veća od slobodnog stanja na lokaciji.')
                            if qty:
                                quantities[candidate['key']] = qty
                        found = sum(quantities.get(row['key'], row['need']) for row in original)
                        found += sum(qty for key, qty in quantities.items() if key.startswith(f'alt:{selected.pk}:'))
                        if found > selected.kolicina:
                            raise MagacinError('Ukupna pronađena količina ne smije biti veća od naručene.')
                        _alternative_lines(order, plan, quantities)
                        context.pop('alternatives', None)
                    context['saved'] = True
                else:
                    context['selected'] = selected
                    context['item_rows'] = [row for row in rows if selected and row['item_id'] == selected.pk]
                query = str(data.get('q', '')).strip()
                context['query'] = query
                if query:
                    context['matches'] = order.stavke.filter(is_set_parent=False, ledger_excess_line__isnull=True).filter(
                        Q(sifra__icontains=query) | Q(naziv__icontains=query) | Q(product_naziv__icontains=query)
                    ).order_by('pk')
                summary = []
                for item in order.stavke.filter(is_set_parent=False, ledger_excess_line__isnull=True):
                    item_rows = [row for row in rows if row['item_id'] == item.pk]
                    if any(row['key'] in quantities for row in item_rows):
                        found = sum(quantities.get(row['key'], row['need']) for row in item_rows)
                        found += sum(qty for key, qty in quantities.items() if key.startswith(f'alt:{item.pk}:'))
                        selected_locations = []
                        for key, qty in quantities.items():
                            if key.startswith(f'alt:{item.pk}:'):
                                stock = WarehouseStock.objects.filter(pk=int(key.rsplit(':', 1)[-1])).select_related('location').first()
                                if stock:
                                    selected_locations.append({'label': stock.location.sifra, 'quantity': qty})
                        summary.append({'name': item.product_naziv or item.naziv, 'code': item.sifra,
                                        'ordered': item.kolicina, 'found': found, 'locations': selected_locations})
                context['summary'] = summary
    except (MagacinError, ValueError) as exc:
        context.update(message=str(exc), error=True)
        status = 409 if isinstance(exc, MagacinError) else 400
    context['changes'] = _signed_changes(order_id, digest, quantities)
    response = render(request, 'staff/magacin/wms_qr.html', context, status=status)
    response['Cache-Control'] = 'no-store'
    # HTTPS form submissions need a same-origin Referer for Django's CSRF check.
    # Keep the signed picking URL private when navigating to another origin.
    response['Referrer-Policy'] = 'same-origin'
    return response
