"""Independent WMS records and pages; separate from legacy warehouse tools."""
from django.contrib.auth.decorators import login_required, user_passes_test
from django.forms import modelform_factory
from django.contrib import messages
from django.db import transaction
from django.urls import reverse
from urllib.parse import urlencode
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.shortcuts import get_object_or_404, redirect, render as django_render
from django.http import Http404, JsonResponse
from django.core.exceptions import PermissionDenied
from .bera_panel_modules import module_locked
from .models import WarehouseLocation, WMSOrder, Product, WarehouseStock, WMSSettings, WMSTransfer
from .magacin import apply_movement, set_location_counted_qty, MagacinError, is_ignored_stock_location
from .models import WarehouseMovement
from django.db.models import Q, Sum, Case, When, Value, IntegerField, F
from django.core.paginator import Paginator
from .warehouse_access import warehouse_user_required

SECTIONS = {'istorija': 'Istorija', 'zalihe': 'Zalihe', 'narudzbe': 'Narudžbe', 'pakovanje': 'Odvajanje robe', 'prenosnica': 'Prenosnica', 'lokacije': 'Lokacije', 'kupci': 'Kupci', 'stampa_cijena': 'Štampaj cijene', 'podesavanje': 'Podešavanje'}


WMS_MODULES = {'istorija': 'wms_zalihe', 'zalihe': 'wms_zalihe', 'narudzbe': 'wms_narudzbe', 'pakovanje': 'wms_pakovanje', 'prenosnica': 'wms_prenosnice', 'lokacije': 'wms_zalihe', 'kupci': 'wms_narudzbe'}


def require_wms_section(section):
    if module_locked(WMS_MODULES.get(section)):
        raise PermissionDenied('Ova WMS sekcija je zaključana u Moduli / Dozvole.')


def context(section):
    return {'wms_section': section, 'wms_title': SECTIONS.get(section, 'WMS Magacin' if not section else 'Nova narudžba'), 'wms_sections': SECTIONS.items(), 'wms_pending_picking': WMSOrder.objects.filter(status__in=['nova', 'pakovanje']).count() if not module_locked('wms_pakovanje') else 0, 'wms_pending_transfers': WMSTransfer.objects.filter(completed_at__isnull=True).count() if not module_locked('wms_prenosnice') else 0, 'wms_locations_enabled': not module_locked('wms_zalihe'), 'wms_transfers_enabled': not module_locked('wms_prenosnice'), 'wms_locked_sections': [key for key, field in WMS_MODULES.items() if module_locked(field)]}


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def workspace(request, section='zalihe'):
    if section == 'stampa_cijena':
        from .wms_price_print import price_print
        if request.headers.get('X-WMS-Permissions'):
            return JsonResponse({'redirect': reverse('staff_wms') if module_locked('stampa_cijena') else None})
        return price_print(request)
    if section not in SECTIONS:
        raise Http404
    locked = module_locked(WMS_MODULES.get(section))
    destination = next((key for key in WMS_MODULES if not module_locked(WMS_MODULES[key])), None) if locked else None
    empty_workspace = all(module_locked(field) for field in WMS_MODULES.values())
    target_url = reverse('staff_wms_section', args=[destination]) if destination else reverse('staff_wms')
    if request.method == 'GET' and request.headers.get('X-WMS-Permissions') == '1':
        response = JsonResponse({'redirect': target_url if locked and request.path != target_url else None})
        response['Cache-Control'] = 'no-store'
        return response
    if request.method in ('GET', 'HEAD') and empty_workspace:
        if request.path != reverse('staff_wms'):
            return redirect('staff_wms')
        return render(request, 'bera_reference/staff/wms/empty.html', context(''))
    if request.method in ('GET', 'HEAD') and locked:
        return redirect(target_url)
    require_wms_section(section)
    if section in ('narudzbe', 'pakovanje'):
        from .wms_orders import sync_panel_orders
        sync_panel_orders()
    ctx = context(section)
    if section == 'istorija':
        rows = WarehouseMovement.objects.select_related(
            'product', 'variation', 'location', 'to_location', 'korisnik', 'order',
        ).order_by('-pk')
        return render(request, 'bera_reference/staff/wms/history.html', {
            **ctx, 'history': Paginator(rows, 20).get_page(request.GET.get('page')),
        })
    if section == 'kupci':
        import json
        from .models import WMSCustomer
        data = json.loads(order_customers(request).content)
        for row in data['customers']:
            if not row['id']:
                customer, _ = WMSCustomer.objects.get_or_create(ime_prezime=row['name'], telefon=row['phone'], defaults={'adresa': row['address'], 'grad': row['city'], 'postanski_broj': row['postal']})
                row['id'] = customer.pk
        return render(request, 'bera_reference/staff/wms/customers.html', {**ctx, **data, 'query': request.GET.get('q', '')})
    if section == 'podesavanje':
        fields = ['vat_rate']
        if not module_locked('wms_prenosnice') and not module_locked('wms_zalihe'):
            fields.append('transfer_location')
        if not module_locked('wms_zalihe'):
            fields.extend(['show_storefront_availability', 'picking_last_location'])
        if not fields:
            if request.method == 'POST':
                raise PermissionDenied
            return render(request, 'bera_reference/staff/wms/podesavanje.html', ctx)
        if request.method == 'POST' and any(field not in fields for field in request.POST if field in ('transfer_location', 'show_storefront_availability', 'picking_last_location', 'vat_rate')):
            raise PermissionDenied
        settings, _ = WMSSettings.objects.get_or_create(pk=1)
        Form = modelform_factory(WMSSettings, fields=fields)
        form = Form(request.POST if request.method == 'POST' else None, instance=settings)
        if request.method == 'POST' and form.is_valid():
            form.save()
            from .views import _invalidate_storefront_product_caches
            transaction.on_commit(_invalidate_storefront_product_caches)
            messages.success(request, 'WMS podešavanje je sačuvano.')
            return redirect('staff_wms_section', section='podesavanje')
        ctx['form'] = form
        return render(request, 'bera_reference/staff/wms/podesavanje.html', ctx)
    if section == 'prenosnica':
        if request.method == 'POST':
            action = request.POST.get('action', 'complete')
            if action not in ('complete', 'cancel'):
                return JsonResponse({'error': 'Nepoznata akcija prenosnice.'}, status=400)
            if action == 'complete':
                require_wms_section('zalihe')
            with transaction.atomic():
                transfer_id = request.POST.get('transfer_id')
                product_id = get_object_or_404(WMSTransfer, pk=transfer_id).product_id
                product = get_object_or_404(Product.objects.select_for_update(), pk=product_id)
                transfer = get_object_or_404(WMSTransfer.objects.select_for_update(), pk=transfer_id)
                if transfer.completed_at:
                    messages.info(request, 'Završena prenosnica se ne može otkazati.' if action == 'cancel' else 'Prenosnica je već prenijeta.')
                elif action == 'cancel':
                    # Pending transfers have no stock movement to reverse.
                    # Use the same locks as completion so only one action can win.
                    transfer.delete()
                    messages.success(request, 'Prenosnica je otkazana. Roba ostaje na izvornoj lokaciji.')
                else:
                    if not transfer.source_id or not transfer.destination_id:
                        raise MagacinError('Prenosnica nema povezane originalne lokacije.')
                    stock = WarehouseStock.objects.select_for_update().filter(product=product, location=transfer.source, variation=transfer.variation).first()
                    if not stock or max(0, stock.kolicina - stock.rezervisano) < transfer.quantity:
                        raise MagacinError('Nema dovoljno slobodne količine. Rezervisana roba se ne može prenijeti.')
                    apply_movement(product=product, location=transfer.source, to_location=transfer.destination,
                                   variation=transfer.variation, tip=WarehouseMovement.Tip.TRANSFER,
                                   kolicina=transfer.quantity, user=request.user, napomena='WMS prenos robe')
                    from django.utils import timezone
                    transfer.completed_at = timezone.now()
                    transfer.save(update_fields=['completed_at'])
                    messages.success(request, 'Prenosnica je prenijeta. Stanje po lokacijama je ažurirano.')
            return redirect('staff_wms_section', section='prenosnica')
        transfer_filter = request.GET.get('status', 'aktivne')
        if transfer_filter not in ('aktivne', 'prenijete'):
            transfer_filter = 'aktivne'
        ctx['transfer_filter'] = transfer_filter
        ctx['transfers'] = Paginator(WMSTransfer.objects.select_related('product', 'source', 'destination').filter(completed_at__isnull=transfer_filter == 'aktivne'), 50).get_page(request.GET.get('page'))
        page_query = request.GET.copy()
        page_query.pop('page', None)
        ctx['page_query'] = page_query.urlencode()
        return render(request, 'bera_reference/staff/wms/prenosnica.html', ctx)
    if section == 'zalihe':
        query = (request.GET.get('q') or '').strip()
        if request.method == 'POST':
            require_wms_section('prenosnica')
            require_wms_section('lokacije')
            settings = WMSSettings.objects.filter(pk=1).select_related('transfer_location').first()
            destination = settings.transfer_location if settings else None
            try:
                quantity = int(request.POST.get('quantity', ''))
            except (ValueError, TypeError):
                quantity = 0
            with transaction.atomic():
                product = get_object_or_404(Product.objects.select_for_update(), pk=request.POST.get('product_id'))
                stock_id = request.POST.get('stock_id')
                stock = get_object_or_404(WarehouseStock.objects.select_for_update(), pk=stock_id, product=product) if stock_id else None
                source_id = stock.location_id if stock else None
                available = stock.kolicina if stock else product.stanje
                expected = request.POST.get('source_location', '')
                if not destination:
                    messages.error(request, 'Prvo izaberite lokaciju za prenos u WMS podešavanju.')
                elif not source_id or str(source_id) != expected or not stock:
                    messages.error(request, 'Raspored je promijenjen. Ponovo otvorite pregled.')
                elif source_id == destination.pk:
                    messages.error(request, 'Artikal je već na odredišnoj lokaciji.')
                elif quantity < 1 or quantity > available:
                    messages.error(request, 'Unesite količinu od 1 do dostupne količine na ovoj lokaciji.')
                else:
                    WMSTransfer.objects.create(product=product, variation=stock.variation, source_id=source_id, destination=destination, quantity=quantity)
                    messages.success(request, 'Prenosnica je kreirana. Roba se premješta tek potvrdom „Prenijeto“.')
            return redirect(reverse('staff_wms_section', args=['zalihe']) + '?' + urlencode({'q': query}))
        stock_filter = request.GET.get('stock_filter', '')
        search_submitted = 'q' in request.GET or stock_filter == 'low'
        products = Product.objects.none()
        if search_submitted:
            products = Product.objects.all()
            if query:
                from .views import _search_exists_match, _typo_tolerant_name_match
                products = products.filter(
                    _search_exists_match(query) | _typo_tolerant_name_match(query)
                    | Q(naziv__icontains=query) | Q(barkod__icontains=query) | Q(tagovi__naziv__icontains=query)
                ).distinct()
            if stock_filter == 'low':
                products = products.filter(magacin_meta__min_zaliha__gt=0, stanje__lt=F('magacin_meta__min_zaliha'))
            products = products.annotate(
                stock_priority=Case(
                    When(stanje__gt=0, then=Value(0)),
                    default=Value(1), output_field=IntegerField(),
                )
            ).prefetch_related('varijacije', 'magacin_zalihe__location', 'magacin_zalihe__variation').order_by('stock_priority', 'naziv', 'pk')
        ctx.update({'transfer_settings': WMSSettings.objects.filter(pk=1).select_related('transfer_location').first(), 'locations': WarehouseLocation.objects.all(), 'query': query, 'search_submitted': search_submitted, 'stock_filter': stock_filter, 'products': Paginator(products, 30).get_page(request.GET.get('page'))})
        for product in ctx['products']:
            product.canonical_wms_stocks = [stock for stock in product.magacin_zalihe.all() if not is_ignored_stock_location(stock.location)]
        return render(request, 'bera_reference/staff/wms/stock_search.html', ctx)
    if section in ('narudzbe', 'pakovanje'):
        if request.method == 'POST' and section == 'pakovanje':
            order = get_object_or_404(WMSOrder, pk=request.POST.get('order_id'))
            return redirect('staff_wms_pick_order', pk=order.pk)
        ctx['orders'] = WMSOrder.objects.select_related('source_order__b2b_submission', 'source_order__card_payment')
        if section == 'narudzbe':
            order_filter = request.GET.get('status', 'aktivna')
            if order_filter not in ('aktivna', 'zavrsene', 'pakovanje', 'otkazana'):
                order_filter = 'aktivna'
            ctx['order_filter'] = order_filter
            if order_filter == 'aktivna':
                ctx['orders'] = ctx['orders'].filter(status__in=['nova', 'pakovanje'])
            else:
                ctx['orders'] = ctx['orders'].filter(status={'zavrsene': 'zapakovana', 'pakovanje': 'pakovanje', 'otkazana': 'otkazana'}[order_filter])
            if order_filter == 'zavrsene':
                query = (request.GET.get('q') or '').strip()
                ctx['order_query'] = query
                if query:
                    match = Q(kupac__icontains=query) | Q(source_order__ime_prezime__icontains=query)
                    number = query.lstrip('#').strip()
                    if number.isascii() and number.isdigit() and len(number) <= 18:
                        match |= Q(pk=int(number))
                    ctx['orders'] = ctx['orders'].filter(match)
        if section == 'pakovanje':
            ctx['orders'] = ctx['orders'].exclude(status__in=['zapakovana', 'otkazana'])
        ctx['orders'] = Paginator(ctx['orders'].select_related('source_order__b2b_submission', 'source_order__card_payment').order_by('-kreirana', '-pk'), 50).get_page(request.GET.get('page'))
        page_query = request.GET.copy()
        page_query.pop('page', None)
        ctx['page_query'] = page_query.urlencode()
    else:
        model, fields = WarehouseLocation, ['sifra', 'naziv']
        Form = modelform_factory(model, fields=fields)
        form = Form(request.POST or None)
        if request.method == 'POST' and form.is_valid():
            form.save()
            return redirect('staff_wms_section', section=section)
        ctx['form'] = form
        ctx['records'] = model.objects.all()
        if section == 'lokacije':
            ctx['shortages'] = pending_shortages().select_related('item__artikal', 'wms_item__artikal', 'location', 'order__source_order').prefetch_related('item__artikal__varijacije', 'wms_item__artikal__varijacije')
    return render(request, 'bera_reference/staff/wms/workspace.html', ctx)


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def create_order(request, tip):
    require_wms_section('narudzbe')
    if tip not in ('vp', 'online'):
        raise Http404
    Form = modelform_factory(WMSOrder, fields=['kupac', 'email', 'telefon', 'adresa', 'grad', 'postanski_broj', 'napomena'])
    form = Form(request.POST or None)
    return create_online_order(request, form, tip)



@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def order_detail(request, pk):
    require_wms_section('narudzbe')
    order = get_object_or_404(WMSOrder, pk=pk)
    source = order.source_order if order.source_order_id else None
    items = list(source.stavke.select_related('artikal').prefetch_related('artikal__varijacije') if source else order.items.select_related('artikal').prefetch_related('artikal__varijacije'))
    for item in items:
        item.line_total = item.cijena * item.kolicina
        regular = item.bazna_cijena
        if regular is None and not source and item.popust and item.popust < 100:
            regular = (item.cijena / (1 - item.popust / 100)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if regular is None and item.artikal_id:
            regular = item.artikal.cijena
        item.regular_price = regular if regular is not None and regular > item.cijena else None
    subtotal = sum((item.line_total for item in items), Decimal('0'))
    discount = source.popust if source else Decimal('0')
    delivery = source.dostava if source else Decimal('0')
    if source:
        order.kupac = source.ime_prezime
        for field in ('email', 'telefon', 'adresa', 'grad', 'postanski_broj', 'napomena', 'kreirana'):
            setattr(order, field, getattr(source, field))
    total = source.ukupno if source else subtotal
    settings = WMSSettings.objects.filter(pk=1).first()
    vat_rate = settings.vat_rate if settings else Decimal('17.00')
    net_total = (total / (1 + vat_rate / 100)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    vat_total = total - net_total
    return render(request, 'bera_reference/staff/wms/order_detail.html', {**context('narudzbe'), 'order': order, 'items': items, 'order_total': total, 'net_total': net_total, 'vat_total': vat_total, 'vat_rate': vat_rate, 'subtotal': subtotal, 'discounted_subtotal': subtotal - discount, 'discount': discount, 'delivery': delivery, 'webshop_order': bool(source)})



@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def edit_order(request, pk):
    require_wms_section('narudzbe')
    order = get_object_or_404(WMSOrder, pk=pk)
    if order.source_order_id:
        # The established order editor handles source totals, variants and reservations.
        return redirect(reverse('staff_magacin_narudzba_nova') + '?' + urlencode({'broj': order.source_order.broj}))
    from django import forms
    from django.forms import inlineformset_factory
    from .models import WMSOrderItem

    class ItemForm(forms.ModelForm):
        kolicina = forms.IntegerField(min_value=1, label='Količina')
        cijena = forms.DecimalField(min_value=0, max_digits=10, decimal_places=2, label='Cijena po komadu (KM)')

        class Meta:
            model = WMSOrderItem
            fields = ['artikal', 'kolicina', 'cijena']

    Items = inlineformset_factory(WMSOrder, WMSOrderItem, form=ItemForm,
                                 extra=1, can_delete=True, min_num=1, validate_min=True,
                                 max_num=500, validate_max=True)
    Form = modelform_factory(WMSOrder, fields=['kupac', 'email', 'telefon', 'adresa', 'grad', 'postanski_broj', 'napomena'])
    with transaction.atomic():
        order = get_object_or_404(WMSOrder.objects.select_for_update(), pk=pk)
        if order.status in ('zapakovana', 'otkazana') or order.pick_lines.exists():
            messages.error(request, 'Narudžba je završena, otkazana ili već u odvajanju i ne može se mijenjati.')
            return redirect('staff_wms_order', pk=pk)
        form = Form(request.POST if request.method == 'POST' else None, instance=order)
        items = Items(request.POST if request.method == 'POST' else None, instance=order, prefix='items')
        if request.method == 'POST':
            valid_form, valid_items = form.is_valid(), items.is_valid()
            if valid_form and valid_items:
                form.save()
                for item_form in items.forms:
                    if not item_form.cleaned_data or item_form.cleaned_data.get('DELETE'):
                        continue
                    item = item_form.instance
                    product = item.artikal
                    if product and (not item.pk or 'artikal' in item_form.changed_data):
                        item.naziv = product.naziv
                        item.sifra = product.sifra or ''
                        item.varijacija = None
                        item.bazna_cijena = wms_entry_price(product, order.tip, regular=True)
                items.save()
                messages.success(request, 'Narudžba i artikli su izmijenjeni.')
                return redirect('staff_wms_section', section='narudzbe')
    return render(request, 'bera_reference/staff/wms/order_form.html', {
        **context('narudzbe'), 'form': form, 'items_formset': items,
        'order': order, 'editing': True, 'order_type': order.get_tip_display(),
    })


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def pack_order(request, pk):
    require_wms_section('narudzbe')
    require_wms_section('pakovanje')
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    WMSOrder.objects.filter(pk=get_object_or_404(WMSOrder, pk=pk).pk, status='nova').update(status='pakovanje')
    return redirect('staff_wms_section', section='pakovanje')


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def cancel_order(request, pk):
    require_wms_section('narudzbe')
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    order = get_object_or_404(WMSOrder, pk=pk)
    reason = (request.POST.get('reason') or '').strip()
    if not reason:
        messages.error(request, 'Unesite razlog otkazivanja.')
        return redirect('staff_wms_section', section='narudzbe')
    if len(reason) > 2000:
        messages.error(request, 'Razlog može imati najviše 2000 znakova.')
        return redirect('staff_wms_section', section='narudzbe')
    WMSOrder.objects.filter(pk=order.pk).exclude(status='otkazana').update(status='otkazana', cancellation_reason=reason)
    messages.success(request, 'Narudžba je otkazana.')
    return redirect('staff_wms_section', section='narudzbe')


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def restore_order(request, pk):
    require_wms_section('narudzbe')
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    order = get_object_or_404(WMSOrder, pk=pk)
    restored = WMSOrder.objects.filter(pk=order.pk, status='otkazana').update(status='nova')
    if restored:
        messages.success(request, 'Narudžba je vraćena među aktivne.')
    return redirect(reverse('staff_wms_section', args=['narudzbe']) + '?status=aktivna')


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def pick_order(request, pk):
    require_wms_section('pakovanje')
    order = get_object_or_404(WMSOrder.objects.select_related('source_order__b2b_submission', 'source_order__card_payment'), pk=pk)
    if order.status not in ('nova', 'pakovanje'):
        return redirect('staff_wms_section', section='pakovanje')
    rows = []
    allocated = {}
    order_items = order.source_order.stavke.select_related('artikal').prefetch_related('artikal__varijacije').exclude(is_set_parent=True) if order.source_order_id else order.items.select_related('artikal').prefetch_related('artikal__varijacije')
    if order_items:
        for item in order_items:
            remaining = item.kolicina
            locations = []
            if item.artikal_id:
                stocks = ordered_picking_stocks(item.artikal.magacin_zalihe.select_related('location').filter(variation_id=getattr(item, 'varijacija_id', None)))
                for stock in stocks:
                    available = max(0, stock.kolicina - (stock.rezervisano if not order.source_order_id else 0) - allocated.get(stock.pk, 0))
                    take = min(remaining, available)
                    if take:
                        locations.append({'name': stock.location.label, 'quantity': take, 'id': stock.location_id})
                        allocated[stock.pk] = allocated.get(stock.pk, 0) + take
                        remaining -= take
                    if not remaining:
                        break
            rows.append({'item': item, 'locations': locations, 'missing': remaining})
    from .models import WMSPickLine
    from django.utils import timezone
    from django import forms
    lines = order.pick_lines.select_related('item__artikal', 'wms_item__artikal', 'location').prefetch_related('item__artikal__varijacije', 'wms_item__artikal__varijacije')
    if request.method == 'POST':
        action = request.POST.get('action', 'start')
        with transaction.atomic():
            locked = WMSOrder.objects.select_for_update().get(pk=pk)
            if locked.status not in ('nova', 'pakovanje'):
                return redirect('staff_wms_section', section='pakovanje')
            if action == 'start' and not locked.pick_lines.exists():
                if not rows or any(row['missing'] for row in rows):
                    messages.error(request, 'Dodijelite dovoljne količine na WMS lokacije prije odvajanja.')
                else:
                    for row in rows:
                        for location in row['locations']:
                            WMSPickLine.objects.create(order=locked, **({'item': row['item']} if order.source_order_id else {'wms_item': row['item']}), location_id=location['id'], quantity=location['quantity'])
                    locked.status = 'pakovanje'
                    locked.save(update_fields=['status'])
            elif action == 'alternative':
                origin = get_object_or_404(WMSPickLine, order=locked, pk=request.POST.get('line_id'), confirmed_at__isnull=False)
                offers = picking_alternatives(locked, origin)
                offer = next((o for o in offers if str(o['location'].pk) == request.POST.get('location_id')), None)
                if offer:
                    replacement = WMSPickLine.objects.create(order=locked, item=origin.item, wms_item=origin.wms_item, location=offer['location'], quantity=offer['quantity'])
                    return redirect(reverse('staff_wms_pick_order', args=[pk]) + '?line=' + str(replacement.pk))
                messages.error(request, 'Lokacija više nema slobodnu količinu za ovaj artikal.')
                return redirect('staff_wms_pick_order', pk=pk)
            elif action == 'confirm':
                line = get_object_or_404(WMSPickLine.objects.select_for_update(), order=locked, pk=request.POST.get('line_id'))
                class ConfirmationForm(forms.Form):
                    quantity = forms.IntegerField(min_value=0, max_value=line.quantity)
                    photo = forms.ImageField(required=False)
                    def clean_photo(self):
                        photo = self.cleaned_data['photo']
                        if photo and photo.size > 10 * 1024 * 1024:
                            raise forms.ValidationError('Fotografija može imati najviše 10 MB.')
                        return photo
                form = ConfirmationForm(request.POST, request.FILES)
                if line.confirmed_at:
                    next_pending = locked.pick_lines.filter(confirmed_at__isnull=True).first()
                    if next_pending:
                        return redirect(reverse('staff_wms_pick_order', args=[pk]) + '?line=' + str(next_pending.pk))
                    return redirect('staff_wms_pick_order', pk=pk)
                if form.is_valid():
                    line.confirmed_quantity = form.cleaned_data['quantity']
                    if form.cleaned_data['photo']:
                        line.photo = form.cleaned_data['photo']
                    line.confirmed_at = timezone.now()
                    line.save(update_fields=['confirmed_quantity', 'photo', 'confirmed_at'])
                    offers = picking_alternatives(locked, line)
                    if offers:
                        offer = offers[0]
                        replacement = WMSPickLine.objects.create(order=locked, item=line.item, wms_item=line.wms_item, location=offer['location'], quantity=offer['quantity'])
                        return redirect(reverse('staff_wms_pick_order', args=[pk]) + '?line=' + str(replacement.pk))
                    next_pending = locked.pick_lines.filter(confirmed_at__isnull=True).first()
                    if next_pending:
                        return redirect(reverse('staff_wms_pick_order', args=[pk]) + '?line=' + str(next_pending.pk))
                    return redirect('staff_wms_pick_order', pk=pk)
                messages.error(request, 'Unesite pokupljenu količinu od 0 do tražene količine.')
                return redirect(reverse('staff_wms_pick_order', args=[pk]) + '?line=' + str(line.pk))
            elif action == 'finish':
                if locked.pick_lines.exists() and not locked.pick_lines.filter(confirmed_at__isnull=True).exists():
                    # Web orders are fulfilled once by complete_legacy_orders in
                    # the outer transaction; manual orders use the same stock ledger.
                    totals = {}
                    for line in locked.pick_lines.select_related('item', 'wms_item', 'location'):
                        item = line.picking_item
                        if not item.artikal_id or not line.location_id or line.confirmed_quantity > line.quantity:
                            raise MagacinError('Artikal, lokacija ili potvrđena količina nisu ispravni.')
                        key = (item.artikal_id, getattr(item, 'varijacija_id', None), line.location_id)
                        totals[key] = totals.get(key, 0) + line.confirmed_quantity
                    if not locked.source_order_id:
                        for (product_id, variation_id, location_id), quantity in sorted(totals.items(), key=lambda entry: (entry[0][0], entry[0][2], entry[0][1] or 0)):
                            if quantity:
                                apply_movement(product=product_id, variation=variation_id,
                                               location=WarehouseLocation.objects.get(pk=location_id),
                                               tip=WarehouseMovement.Tip.PRODAJA, kolicina=quantity,
                                               user=request.user, napomena=f'WMS odvajanje #{locked.display_number}')
                    locked.status = 'zapakovana'
                    locked.completed_at = timezone.now()
                    locked.save(update_fields=['status', 'completed_at'])
                    messages.success(request, 'Odvajanje robe je završeno.')
                    return redirect(reverse('staff_wms_section', args=['narudzbe']) + '?status=zavrsene')
                messages.error(request, 'Svi artikli moraju biti odvojeni.')
        if action == 'start':
            first = order.pick_lines.filter(confirmed_at__isnull=True).first()
            if first:
                return redirect(reverse('staff_wms_pick_order', args=[pk]) + '?line=' + str(first.pk))
        return redirect('staff_wms_pick_order', pk=pk)
    lines = list(lines)
    origin = next((line for line in lines if str(line.pk) == request.GET.get('alternative') and line.confirmed_at), None)
    offers = picking_alternatives(order, origin) if origin else []
    current = next((line for line in lines if str(line.pk) == request.GET.get('line')), None)
    next_line = next((line for line in lines if not line.confirmed_at), None)
    if request.GET.get('resume') == '1' and current is None:
        current = next_line
    pending_lines = [line for line in lines if not line.confirmed_at]
    cycle_next = None
    if current and current in pending_lines and len(pending_lines) > 1:
        cycle_next = pending_lines[(pending_lines.index(current) + 1) % len(pending_lines)]
    completed = sum(bool(line.confirmed_at) for line in lines)
    return render(request, 'bera_reference/staff/wms/picking.html', {
        **context('pakovanje'), 'order': order, 'rows': rows, 'lines': lines,
        'requested_quantity': sum(row['item'].kolicina for row in rows),
        'current': current, 'next_line': next_line, 'completed': completed, 'alternative_origin': origin, 'alternative_offers': offers,
        'can_finish': bool(lines) and completed == len(lines),
        'progress': round(completed * 100 / len(lines)) if lines else 0,
        'current_index': lines.index(current) + 1 if current else 0, 'cycle_next': cycle_next,
        'pending_lines': pending_lines, 'confirmed_lines': [line for line in lines if line.confirmed_at and line.confirmed_quantity > 0],
        'picked_lines': [line for line in lines if line.confirmed_at and line.confirmed_quantity > 0],
    })


def pending_shortages():
    from .models import WMSPickLine
    from django.db.models import F
    return WMSPickLine.objects.filter(confirmed_at__isnull=False,
        confirmed_quantity__lt=F('quantity'), shortage_resolved_at__isnull=True)


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def resolve_shortage(request, pk):
    from django.http import HttpResponseNotAllowed
    from django.utils import timezone
    require_wms_section('lokacije')
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    decision = request.POST.get('decision')
    if decision not in ('clear', 'keep'):
        return JsonResponse({'error': 'Izaberite Očisti ili Ostavi.'}, status=400)
    from .models import WMSPickLine, OrderItem, WMSOrderItem
    line = get_object_or_404(WMSPickLine.objects.select_related('item', 'wms_item'), pk=pk)
    item = line.picking_item
    if item is None or not item.artikal_id:
        messages.error(request, 'Artikal više nije dostupan.')
        return redirect('staff_wms_section', section='lokacije')
    variation_id = item.varijacija_id
    try:
        with transaction.atomic():
            product = get_object_or_404(Product.objects.select_for_update(), pk=item.artikal_id)
            # Subqueries avoid nullable outer joins in the FOR UPDATE query on PostgreSQL.
            # Only shortages for this exact product/variant/location are resolved.
            source_items = OrderItem.objects.filter(artikal=product, varijacija_id=variation_id).values('pk')
            manual_items = WMSOrderItem.objects.filter(artikal=product, varijacija_id=variation_id).values('pk')
            unresolved = pending_shortages().select_for_update().filter(
                Q(item_id__in=source_items) | Q(wms_item_id__in=manual_items),
                location_id=line.location_id,
            )
            unresolved_ids = list(unresolved.values_list('pk', flat=True))
            if pk not in unresolved_ids:
                return redirect('staff_wms_section', section='lokacije')
            if decision == 'clear':
                if not line.location_id:
                    raise MagacinError('Lokacija nije povezana s originalnim magacinom.')
                stock = WarehouseStock.objects.select_for_update().filter(
                    product=product, location_id=line.location_id, variation_id=variation_id,
                ).first()
                if stock and stock.rezervisano:
                    raise MagacinError('Lokacija s rezervisanom količinom ne može se očistiti.')
                set_location_counted_qty(location=line.location, product=product,
                    variation=item.varijacija, qty=0,
                    user=request.user, napomena='WMS: Lokacija očišćena — Očisti nakon odvajanja')
            WMSPickLine.objects.filter(pk__in=unresolved_ids).update(
                shortage_resolution=decision, shortage_resolved_at=timezone.now(),
            )
    except MagacinError as error:
        messages.error(request, str(error))
        return redirect('staff_wms_section', section='lokacije')
    messages.success(request, 'Lokacija je očišćena.' if decision == 'clear' else 'Količina je ostavljena na lokaciji.')
    return redirect('staff_wms_section', section='lokacije')


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def print_orders(request, kind):
    from decimal import Decimal
    require_wms_section('narudzbe')
    if kind not in ('racun', 'faktura', 'najava'):
        raise Http404
    orders = WMSOrder.objects.filter(status='zapakovana').select_related('source_order__b2b_submission', 'source_order__card_payment').prefetch_related('pick_lines__item', 'pick_lines__wms_item', 'pick_lines__location', 'source_order__stavke', 'items')
    if kind == 'faktura':
        orders = orders.filter(tip='vp')
    from datetime import date
    from django.utils import timezone
    print_date = timezone.localdate()
    all_picking = kind == 'racun' and request.GET.get('scope') == 'picking'
    if kind in ('najava', 'racun', 'faktura'):
        try:
            print_date = date.fromisoformat(request.GET.get('date', '')) if request.GET.get('date') else print_date
        except ValueError:
            return JsonResponse({'error': 'Unesite ispravan datum.'}, status=400)
        if kind in ('najava', 'faktura'):
            orders = orders.filter(Q(completed_at__date=print_date) | Q(completed_at__isnull=True, kreirana__date=print_date))
        else:
            from .wms_orders import sync_panel_orders
            sync_panel_orders()
            orders = WMSOrder.objects.exclude(status='otkazana').exclude(source_order__status='otkazana').filter(source_order__b2b_submission__isnull=True).select_related('source_order__b2b_submission', 'source_order__card_payment').prefetch_related('pick_lines__item', 'pick_lines__wms_item', 'pick_lines__location', 'source_order__stavke', 'items')
            if all_picking:
                orders = orders.filter(status='pakovanje')
            else:
                orders = orders.filter(Q(source_order__kreirana__date=print_date) | Q(source_order__isnull=True, kreirana__date=print_date))
        if kind in ('racun', 'faktura'):
            if request.GET.get('stage') != 'print':
                return render(request, 'bera_reference/staff/wms/invoice_selection.html', {
                    **context('narudzbe'), 'orders': orders, 'print_date': print_date,
                    'print_kind': kind, 'all_picking': all_picking, 'selection_title': 'Količine za fakturu' if kind == 'faktura' else 'Štampanje računa'})
            selected = [int(value) for value in request.GET.getlist('orders') if value.isdigit() and len(value) < 18]
            orders = orders.filter(pk__in=selected)

    jobs = []
    for order in orders:
        quantities = {}
        locations = {}
        for line in order.pick_lines.all():
            quantities[line.picking_item.pk] = quantities.get(line.picking_item.pk, 0) + line.confirmed_quantity
            if line.confirmed_quantity:
                locations.setdefault(line.picking_item.pk, []).append({'name': line.location.naziv, 'quantity': line.confirmed_quantity})
        items = []
        subtotal = Decimal('0.00')
        order_items = order.source_order.stavke.all() if order.source_order_id else order.items.all()
        if order_items:
            for item in order_items:
                quantity = quantities.get(item.pk, 0) if order.status == 'zapakovana' else item.kolicina
                if quantity:
                    total = item.cijena * quantity
                    subtotal += total
                    items.append({'name': item.naziv, 'code': item.sifra, 'quantity': quantity, 'price': item.cijena, 'total': total, 'locations': locations.get(item.pk, [])})
        source = order.source_order
        discount = min(subtotal, (source.popust * subtotal / source.medjuzbir).quantize(Decimal('0.01'))) if source and source.medjuzbir > 0 else Decimal('0.00')
        delivery = source.dostava if source else Decimal('0.00')
        jobs.append({'order': order, 'items': items, 'subtotal': subtotal, 'discount': discount,
                     'delivery': delivery, 'total': subtotal - discount + delivery})
    if kind == 'najava':
        return render(request, 'bera_reference/staff/wms/announcement_print.html', {'jobs': jobs, 'print_date': print_date, 'order_count': len(jobs)})
    return render(request, 'bera_reference/staff/wms/orders_print.html', {'jobs': jobs, 'kind': kind,
        'title': {'racun': 'Štampanje računa', 'faktura': 'Količine za fakturu', 'najava': 'Najava slanja'}[kind]})


def create_online_order(request, form, tip='online'):
    import json
    from .models import WMSOrderItem
    lines = []
    if request.method == 'POST':
        try:
            raw = json.loads(request.POST.get('items', '[]'))
            if not isinstance(raw, list) or not raw or len(raw) > 500:
                raise ValueError
            quantities = {}
            discounts = {}
            for row in raw:
                product_id, quantity = int(row['id']), int(row['quantity'])
                if quantity < 1 or quantity > 100000:
                    raise ValueError
                discount = Decimal(str(row.get('discount', 0)))
                if not discount.is_finite() or discount < 0 or discount > 100:
                    raise ValueError
                discount = discount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                if product_id in discounts and discounts[product_id] != discount:
                    raise ValueError
                discounts[product_id] = discount
                quantities[product_id] = quantities.get(product_id, 0) + quantity
            products = {product.pk: product for product in Product.objects.filter(pk__in=quantities)}
            if len(products) != len(quantities):
                raise ValueError
            lines = [{'id': pk, 'name': products[pk].naziv, 'code': products[pk].sifra, 'price': str(wms_entry_price(products[pk], tip)), 'quantity': qty, 'discount': str(discounts[pk])} for pk, qty in quantities.items()]
            if form.is_valid():
                with transaction.atomic():
                    order = form.save(commit=False)
                    order.tip = tip
                    from .models import WMSCustomer
                    customer_id = request.POST.get('customer_id', '')
                    if customer_id.isdigit():
                        order.customer = WMSCustomer.objects.filter(pk=customer_id).first()
                    order.save()
                    for pk, qty in quantities.items():
                        product = products[pk]
                        WMSOrderItem.objects.create(order=order, artikal=product, naziv=product.naziv,
                            sifra=product.sifra or '', kolicina=qty, popust=discounts[pk], bazna_cijena=wms_entry_price(product, tip, regular=True),
                            cijena=(wms_entry_price(product, tip) * (Decimal('1') - discounts[pk] / 100)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
                return redirect('staff_wms_section', section='narudzbe')
        except (ValueError, TypeError, KeyError, InvalidOperation):
            messages.error(request, 'Dodajte artikle i unesite ispravne količine.')
    return render(request, 'bera_reference/staff/wms/online_order_form.html', {**context('narudzbe'), 'form': form, 'initial_items': lines, 'order_type': tip})


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def order_products(request):
    require_wms_section('narudzbe')
    query = (request.GET.get('q') or '').strip()
    products = Product.objects.filter(aktivan=True).annotate(wms_reserved=Sum('magacin_zalihe__rezervisano'))
    if query:
        products = products.filter(Q(naziv__icontains=query) | Q(sifra__icontains=query))
    page = Paginator(products.prefetch_related('varijacije').order_by('naziv', 'pk'), 24).get_page(request.GET.get('page'))
    return JsonResponse({'pages': page.paginator.num_pages, 'page': page.number, 'results': [
        {'id': product.pk, 'name': product.naziv, 'code': product.sifra, 'price': str(wms_entry_price(product, request.GET.get('tip', 'online'))),
         'available': max(0, product.stanje - (product.wms_reserved or 0)),
         'image': product.prikazna_slika.url if product.prikazna_slika else ''} for product in page]})


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def picking_history(request):
    from datetime import date
    from django.db.models.functions import Coalesce
    from .models import WMSPickLine
    require_wms_section('pakovanje')
    rows = WMSPickLine.objects.select_related('order__source_order__b2b_submission', 'order__source_order__card_payment', 'item__artikal', 'wms_item__artikal', 'location').annotate(history_at=Coalesce('confirmed_at', 'order__completed_at', 'order__kreirana')).order_by('-history_at', '-pk')
    customer = (request.GET.get('customer') or '').strip()
    number = (request.GET.get('number') or '').strip().lstrip('#')
    if customer:
        rows = rows.filter(Q(order__kupac__icontains=customer) | Q(order__source_order__ime_prezime__icontains=customer))
    if number:
        match = Q(order__source_order__broj__icontains=number)
        if number.isascii() and number.isdigit() and len(number) <= 18:
            match |= Q(order_id=int(number))
        rows = rows.filter(match)
    dates = {}
    for key, lookup in [('date_from', 'history_at__date__gte'), ('date_to', 'history_at__date__lte')]:
        value = request.GET.get(key, '')
        try:
            parsed = date.fromisoformat(value)
        except (ValueError, TypeError):
            value = ''
        else:
            rows = rows.filter(**{lookup: parsed})
        dates[key] = value
    filters = {'customer': customer, 'number': number, **dates}
    return render(request, 'bera_reference/staff/wms/picking_history.html', {**context('pakovanje'), **filters, 'history': Paginator(rows, 50).get_page(request.GET.get('page')), 'history_query': urlencode(filters)})


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def order_customers(request):
    from .models import WMSCustomer, Order
    from .wms_customer_forms import WMSCustomerForm
    require_wms_section('narudzbe')
    if request.method == 'POST':
        form = WMSCustomerForm(request.POST)
        if not form.is_valid():
            return JsonResponse({'errors': form.errors.get_json_data()}, status=400)
        customer = form.save()
        return JsonResponse({'customer': {'id': customer.pk, 'name': customer.ime_prezime,
            'phone': customer.telefon, 'address': customer.adresa, 'city': customer.grad, 'postal': customer.postanski_broj}})
    if request.method != 'GET':
        return JsonResponse({'error': 'Nedozvoljen zahtjev.'}, status=405)
    query = (request.GET.get('q') or '').strip()
    from .wms_customer_query import customer_page
    return JsonResponse(customer_page(query, request.GET.get('page')))


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def manage_customer(request, pk, action):
    from .models import WMSCustomer
    from .wms_customer_forms import WMSCustomerForm
    require_wms_section('kupci')
    customer = get_object_or_404(WMSCustomer, pk=pk, is_deleted=False)
    if action not in ('izmjena', 'obrisi'):
        raise Http404
    form = WMSCustomerForm(request.POST or None, instance=customer)
    if request.method == 'POST':
        if action == 'obrisi':
            customer.is_deleted = True
            customer.save(update_fields=['is_deleted'])
            return redirect('staff_wms_section', section='kupci')
        if form.is_valid():
            form.save()
            return redirect('staff_wms_section', section='kupci')
    return render(request, 'bera_reference/staff/wms/customer_form.html', {**context('kupci'), 'form': form, 'customer': customer, 'delete_customer': action == 'obrisi'})


def picking_alternatives(order, origin):
    item = origin.picking_item
    if not item.artikal_id or origin.confirmed_quantity >= origin.quantity:
        return []
    lines = list(order.pick_lines.select_related('item', 'wms_item'))
    same_item = [line for line in lines if line.item_id == origin.item_id and line.wms_item_id == origin.wms_item_id]
    committed = sum(line.confirmed_quantity if line.confirmed_at else line.quantity for line in same_item)
    missing = max(0, item.kolicina - committed)
    visited = {line.location_id for line in same_item}
    offers = []
    for stock in ordered_picking_stocks(WarehouseStock.objects.filter(product_id=item.artikal_id, variation_id=getattr(item, 'varijacija_id', None), kolicina__gt=0).exclude(location_id__in=visited).select_related('location')):
        reserved = sum(line.confirmed_quantity if line.confirmed_at else line.quantity for line in lines if line.location_id == stock.location_id and line.picking_item.artikal_id == item.artikal_id)
        take = min(missing, max(0, stock.kolicina - reserved))
        if take:
            offers.append({'location': stock.location, 'quantity': take})
    return offers


def ordered_picking_stocks(stocks):
    settings = WMSSettings.objects.filter(pk=1).first()
    last_id = settings.picking_last_location_id if settings else None
    def sort_key(stock):
        name = stock.location.label.strip().casefold()
        last = stock.location_id == last_id if last_id else ('maloprodaja' in name or name == 'mp')
        return (last, name, stock.pk)
    return sorted((stock for stock in stocks if not is_ignored_stock_location(stock.location)), key=sort_key)


def wms_entry_price(product, tip, regular=False):
    price = product.cijena if regular else (product.akcijska_cijena or product.cijena)
    if tip == 'vp':
        price = price / Decimal('1.38')
    return price.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def location_detail(request, pk):
    require_wms_section('lokacije')
    location = get_object_or_404(WarehouseLocation, pk=pk)
    if request.method == 'POST':
        require_wms_section('prenosnica')
        try:
            quantity = int(request.POST.get('quantity', ''))
            destination_id = int(request.POST.get('destination', ''))
            stock_id = int(request.POST.get('stock_id', ''))
        except (ValueError, TypeError):
            messages.error(request, 'Unesite količinu i odredišnu lokaciju.')
            return redirect('staff_wms_location', pk=pk)
        destination = get_object_or_404(WarehouseLocation, pk=destination_id)
        with transaction.atomic():
            stock = get_object_or_404(WarehouseStock.objects.select_for_update(), pk=stock_id, location=location)
            if quantity < 1 or quantity > stock.kolicina or destination.pk == location.pk:
                messages.error(request, 'Provjerite količinu i izaberite drugu lokaciju.')
            else:
                WMSTransfer.objects.create(product=stock.product, variation=stock.variation, source=location, destination=destination, quantity=quantity)
                messages.success(request, 'Prenosnica je kreirana. Potvrdite Prenijeto da prebacite robu.')
                return redirect('staff_wms_section', section='prenosnica')
        return redirect('staff_wms_location', pk=pk)
    stocks = WarehouseStock.objects.filter(location=location, kolicina__gt=0).select_related('product').prefetch_related('product__varijacije').order_by('product__naziv', 'pk')
    stocks = Paginator(stocks, 30).get_page(request.GET.get('page'))
    return render(request, 'bera_reference/staff/wms/location_detail.html', {**context('lokacije'), 'location': location, 'stocks': stocks, 'destinations': WarehouseLocation.objects.exclude(pk=pk).only('pk', 'naziv').order_by('naziv')})


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def location_barcode_print(request, pk):
    require_wms_section('lokacije')
    from barcode import Code128
    from barcode.writer import SVGWriter
    import base64
    location = get_object_or_404(WarehouseLocation, pk=pk)
    svg = Code128(location.sifra, writer=SVGWriter()).render({'module_width': 0.35, 'module_height': 15, 'quiet_zone': 3, 'font_size': 10})
    image = 'data:image/svg+xml;base64,' + base64.b64encode(svg).decode('ascii')
    return render(request, 'bera_reference/staff/wms/location_barcode_print.html', {'location': location, 'barcode_image': image})


@login_required(login_url='login')
@user_passes_test(warehouse_user_required)
def excel_import(request):
    require_wms_section('zalihe')
    from .wms_excel import template_bytes, import_products
    from django.http import HttpResponse
    if request.method == 'GET' and request.GET.get('template') == '1':
        response = HttpResponse(template_bytes(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename="WMS-import-artikala.xlsx"'
        return response
    if request.method == 'POST':
        upload = request.FILES.get('excel_file')
        if not upload or not upload.name.lower().endswith('.xlsx') or upload.size > 10 * 1024 * 1024:
            messages.error(request, 'Izaberite .xlsx datoteku do 10 MB.')
        else:
            try:
                count = import_products(upload, user=request.user)
                messages.success(request, f'Import uspješan: {count} artikala.')
            except ValueError as exc:
                messages.error(request, str(exc))
            except Exception:
                import logging
                logging.getLogger(__name__).exception('WMS Excel import nije uspio')
                messages.error(request, 'Import nije uspio. Provjerite Excel datoteku; podaci nisu upisani.')
    return redirect('staff_wms_section', section='podesavanje')


# Shared destination identity and the existing warehouse transaction services.
def render(request, template, data=None):
    from .bera_home import storefront_context
    from .views import _base_context
    return django_render(request, template, {**_base_context(), **storefront_context(), **(data or {})})

from .wms_legacy import warehouse_compatible
for _wms_view_name in ('workspace', 'create_order', 'order_detail', 'edit_order', 'pack_order',
                       'cancel_order', 'restore_order', 'pick_order', 'resolve_shortage',
                       'print_orders', 'order_products', 'order_customers', 'manage_customer',
                       'location_detail', 'location_barcode_print', 'excel_import', 'picking_history'):
    globals()[_wms_view_name] = warehouse_compatible(globals()[_wms_view_name])
