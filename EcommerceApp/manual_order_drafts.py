"""Permanent manual-order snapshots. No expiry or automatic deletion."""
import json
import re
import uuid

from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.http import JsonResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .models import ManualOrderDraft, ManualOrderDraftRevision
from .warehouse_access import warehouse_user_required

FIELDS = {
    'customer_id', 'ime_prezime', 'telefon', 'adresa', 'postanski_broj', 'grad',
    'email', 'vp_kupac', 'napomena', 'popust_pct', 'bez_dostave', 'placanje',
    'loyalty_auto', 'product_id', 'variation_id', 'kolicina', 'mp_ok',
    'rezervni', 'spare_naziv', 'spare_cijena',
}


def clean_payload(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('fields'), list):
        raise ValueError('Neispravan nacrt.')
    fields = []
    for pair in payload['fields']:
        if not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(v, str) for v in pair):
            raise ValueError('Neispravan unos.')
        if pair[0] in FIELDS:
            fields.append(pair)
    inputs = payload.get('inputs', {})
    if not isinstance(inputs, dict):
        raise ValueError('Neispravna polja.')
    inputs = {key: value for key, value in inputs.items()
              if key.startswith('mg') and isinstance(value, dict)
              and isinstance(value.get('value'), str)}
    return {'fields': fields, 'inputs': inputs, 'lines': payload.get('lines', [])}


def form_data(payload):
    data = QueryDict('', mutable=True)
    for name, value in payload.get('fields', []):
        if name in FIELDS:
            data.appendlist(name, value)
    return data


def posted_payload(request):
    # UI-only inputs travel with normal submits too, including a partially entered customer.
    try:
        ui = json.loads(request.POST.get('draft_ui') or '{}')
    except (ValueError, TypeError):
        ui = {}
    return clean_payload({
        'fields': [[name, value] for name, values in request.POST.lists()
                   if name in FIELDS for value in values],
        'inputs': ui.get('inputs', {}), 'lines': ui.get('lines', []),
    })


def draft_for_page(request, order_number=''):
    token = request.POST.get('draft_token') or request.GET.get('nacrt')
    qs = ManualOrderDraft.objects.filter(owner=request.user, status='active', order_number=order_number)
    if token:
        draft = get_object_or_404(ManualOrderDraft, token=token, owner=request.user)
        if draft.order_number != order_number and draft.status == 'active':
            from django.http import Http404
            raise Http404
        return draft
    else:
        draft = qs.first()
        if draft:
            return draft
    return ManualOrderDraft.objects.create(owner=request.user, token=uuid.uuid4().hex, order_number=order_number)


def capture_submit(request, draft):
    payload = posted_payload(request)
    with transaction.atomic():
        current = ManualOrderDraft.objects.select_for_update().get(pk=draft.pk)
        ManualOrderDraftRevision.objects.create(draft=current, payload=payload, event='submit')
        if current.status != 'active':
            return False
        # A stale tab must not silently replace a newer working copy.
        if str(current.version) != request.POST.get('draft_version', str(current.version)):
            return False
        current.payload = payload
        current.version += 1
        current.save(update_fields=['payload', 'version', 'updated_at'])
    draft.refresh_from_db()
    return True


def archive(draft, status, order_number=''):
    with transaction.atomic():
        current = ManualOrderDraft.objects.select_for_update().get(pk=draft.pk)
        ManualOrderDraftRevision.objects.create(draft=current, payload=current.payload, event=status)
        current.status = status
        current.order_number = order_number or current.order_number
        current.save(update_fields=['status', 'order_number', 'updated_at'])


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
@require_POST
def save_draft(request):
    try:
        data = json.loads(request.body)
        token = data.get('token', '')
        if not isinstance(token, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', token):
            raise ValueError('Neispravan nacrt.')
        payload = clean_payload(data.get('payload'))
        expected = int(data.get('version', 0))
    except (ValueError, TypeError, AttributeError):
        return JsonResponse({'error': 'Neispravan nacrt.'}, status=400)
    with transaction.atomic():
        draft = get_object_or_404(ManualOrderDraft.objects.select_for_update(), token=token, owner=request.user)
        if draft.status != 'active':
            event = 'conflict'
        elif data.get('event') == 'cancel':
            event = 'cancelled'
        elif draft.version != expected and payload != draft.payload:
            event = 'conflict'
        else:
            event = 'save'
        # Even a stale/offline tab is retained as its own revision.
        if payload != draft.payload or event != 'save':
            ManualOrderDraftRevision.objects.create(draft=draft, payload=payload, event=event)
        if event == 'conflict':
            return JsonResponse({'error': 'Postoji novija verzija. Tvoj unos je sačuvan u historiji.', 'version': draft.version}, status=409)
        if payload != draft.payload:
            draft.payload = payload
            draft.version += 1
        if event == 'cancelled':
            draft.status = 'cancelled'
        draft.save(update_fields=['payload', 'version', 'status', 'updated_at'])
    return JsonResponse({'ok': True, 'version': draft.version})


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def history(request):
    from .views_magacin import _magacin_context
    from django.core.paginator import Paginator
    context = _magacin_context(request, section='narudzbe', page_title='Historija unosa narudžbi')
    qs = ManualOrderDraftRevision.objects.filter(draft__owner=request.user).select_related('draft')
    context['revisions'] = Paginator(qs, 50).get_page(request.GET.get('page'))
    return render(request, 'staff/magacin/draft_history.html', context)


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
@require_POST
def restore_revision(request, pk):
    revision = get_object_or_404(ManualOrderDraftRevision, pk=pk, draft__owner=request.user)
    with transaction.atomic():
        draft = ManualOrderDraft.objects.create(owner=request.user, token=uuid.uuid4().hex, payload=revision.payload, version=1)
        ManualOrderDraftRevision.objects.create(draft=draft, payload=revision.payload, event='restored')
    return redirect(reverse('staff_magacin_narudzba_nova') + '?nacrt=' + draft.token)


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
@require_POST
def import_local_draft(request):
    """One-time migration of the previous browser-only implementation."""
    try:
        data = json.loads(request.body)
        token = data['token']
        if not isinstance(token, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', token):
            raise ValueError
        payload = clean_payload(data)
    except (ValueError, KeyError, TypeError):
        return JsonResponse({'error': 'Neispravan nacrt.'}, status=400)
    with transaction.atomic():
        draft, created = ManualOrderDraft.objects.get_or_create(token=token, defaults={
            'owner': request.user, 'payload': payload, 'version': 1,
        })
        if draft.owner_id != request.user.pk:
            return JsonResponse({'error': 'Nacrt nije dostupan.'}, status=403)
        if created or draft.payload != payload:
            ManualOrderDraftRevision.objects.create(draft=draft, payload=payload, event='imported')
    return JsonResponse({'ok': True, 'url': reverse('staff_magacin_narudzba_nova') + '?nacrt=' + draft.token})
