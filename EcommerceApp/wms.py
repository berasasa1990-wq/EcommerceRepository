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
        'queue': [(r['key'], r['item_id'], r['loc'], r['need']) for r in queue],
        'state': order.pick_state, 'events': order.pick_short_events,
        'total': str(order.ukupno), 'status': order.status, 'lager': order.lager_status,
    }
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    return {'rows': rows, 'queue': queue, 'digest': digest, 'error': ' '.join(problems),
            'base_error': str(error or '') or ('Narudžba nema stavki za picking.' if not items else ''),
            'overallocated': any(totals[item.pk] > int(item.kolicina) for item in items)}


def paper_picking_job(request, order):
    from .views_magacin import _etiketa_qr_data_uri
    plan = picking_plan(order)
    for row in plan['rows']:
        row['display_loc'] = str(row['loc']).rstrip('/').rsplit('/', 1)[-1]
    token = signing.dumps({'order': order.pk, 'digest': plan['digest']}, salt=SALT, compress=True)
    url = request.build_absolute_uri(reverse('staff_magacin_wms_validate', kwargs={'token': token}))
    return {'rows': plan['rows'], 'error': plan['error'], 'qr': _etiketa_qr_data_uri(url)}


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
    if set(quantities) - valid_keys:
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
    if order_needs_mp_check(order):
        raise MagacinError('Prvo završi provjeru artikala u maloprodaji.')
    claimed, holder = claim_order_pick(order, user)
    if not claimed:
        raise MagacinError(f"Narudžbu je već preuzeo {holder or 'drugi radnik'}.")
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
            elif action not in (None, 'save'):
                raise MagacinError('Odaberi Da ili Ne prije potvrde narudžbe.')
            elif data.get('mode') == 'missing' or action == 'save':
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
                        summary.append({'name': item.product_naziv or item.naziv, 'code': item.sifra,
                                        'ordered': item.kolicina, 'found': found})
                context['summary'] = summary
    except (MagacinError, ValueError) as exc:
        context.update(message=str(exc), error=True)
        status = 409 if isinstance(exc, MagacinError) else 400
    context['changes'] = _signed_changes(order_id, digest, quantities)
    response = render(request, 'staff/magacin/wms_qr.html', context, status=status)
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response
