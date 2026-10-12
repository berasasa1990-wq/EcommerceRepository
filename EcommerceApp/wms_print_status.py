"""Explicit print acknowledgements in the existing persistent audit journal."""
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.http import require_POST
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from .models import WMSOrder, Order, SystemDataRevision
from .warehouse_access import warehouse_user_required

@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
@require_POST
def acknowledge(request, kind):
    from .views_wms import require_wms_section
    require_wms_section('narudzbe')
    if kind not in ('racun', 'faktura'):
        return JsonResponse({'error': 'Neispravna vrsta štampe.'}, status=400)
    values = request.POST.getlist('orders')
    if not values or len(values) > 1000 or any(not value.isdigit() or len(value) > 18 for value in values):
        return JsonResponse({'error': 'Odaberite narudžbe.'}, status=400)
    with transaction.atomic():
        orders = list(WMSOrder.objects.select_for_update().filter(pk__in=values).order_by('pk'))
        if len(orders) != len(set(values)):
            return JsonResponse({'error': 'Narudžba nije pronađena.'}, status=404)
        for order in orders:
            # Audit entries preserve reprint history without modifying stock or notes.
            SystemDataRevision.objects.create(table_name=WMSOrder._meta.db_table,
                record_key=str(order.pk), operation='PRINT', before=None,
                after={'kind': kind, 'user_id': request.user.pk})
        if kind == 'racun':
            Order.objects.filter(pk__in=[order.source_order_id for order in orders if order.source_order_id]).update(
                odstampana=True, odstampana_at=timezone.now())
    return JsonResponse({'ok': True})
